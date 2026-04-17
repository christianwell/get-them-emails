import asyncio
from collections import deque
import re
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse, quote_plus, unquote
from playwright.async_api import async_playwright, Page, Browser
import config


async def launch_browser() -> tuple:
    """Launch Playwright headless Chromium. Returns (playwright, browser)."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    return pw, browser


async def close_browser(pw, browser: Browser):
    await browser.close()
    await pw.stop()


async def new_page(browser: Browser) -> Page:
    """Create a page with realistic user agent."""
    ctx = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
    )
    return await ctx.new_page()


async def get_page_content(page: Page, url: str, quiet: bool = False) -> dict | None:
    """Navigate to URL and extract content."""
    try:
        if not quiet:
            print(f"    📄 {url}")
        resp = await page.goto(url, wait_until="domcontentloaded",
                               timeout=config.PAGE_TIMEOUT)
        if not resp or resp.status >= 400:
            if not quiet:
                print(f"       ❌ HTTP {resp.status if resp else 'no response'}")
            return None
        await page.wait_for_timeout(config.JS_WAIT)
        html = await page.content()
        text = await page.inner_text("body")
        if not quiet:
            print(f"       ✅ {len(text)} chars text")
        return {"html": html, "text": text, "url": url}
    except Exception as e:
        if not quiet:
            print(f"       ❌ {e}")
        return None


# ── Staff page detection ──

def _is_staff_page(text: str, page_url: str = "") -> bool:
    """Heuristic: is this actually a staff/faculty listing page?"""
    text_lower = text.lower()
    url_lower = page_url.lower()

    # Must have strong staff indicators
    strong = ["staff directory", "faculty directory", "our staff",
              "our faculty", "staff list", "faculty & staff",
              "faculty and staff", "meet our staff", "meet our teachers"]
    has_strong = any(kw in text_lower for kw in strong)
    has_strong_url = any(kw in url_lower for kw in [
        "staff", "faculty", "directory", "staff-directory",
        "staffsearch", "staff-search", "teacher", "employee",
    ])

    # Or multiple weak indicators
    weak = ["teacher", "staff", "faculty", "instructor", "educator",
            "department head"]
    weak_count = sum(1 for kw in weak if kw in text_lower)
    directory_controls = sum(
        1 for kw in [
            "search staff", "staff search", "first name", "last name",
            "all locations", "all departments", "filter by", "directory results",
        ]
        if kw in text_lower
    )
    negative_content = sum(
        1 for kw in [
            "how to", "help guide", "teacher help", "acceptable use agreement",
            "powerteacher", "powerschool", "schoolmessenger",
            "grading", "scoresheet", "category weighting", "current topics",
            "submit grades", "technology support",
        ]
        if kw in text_lower
    )

    email_count = len(re.findall(config.EMAIL_REGEX, text))
    phone_count = len(re.findall(r'[\(]?\d{3}[\)]?[\s\-\.]\d{3}[\s\-\.]\d{4}', text))
    name_like_count = len(re.findall(
        r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z\'\-]+){1,2}\b',
        text[:120000],
    ))

    if has_strong and (email_count >= 1 or phone_count >= 2 or name_like_count >= 8):
        return True
    # Directory UIs often hide emails but still have many names and filter controls.
    if directory_controls >= 2 and (name_like_count >= 6 or email_count >= 2):
        return True
    if has_strong_url and weak_count >= 1 and (email_count >= 2 or phone_count >= 2 or name_like_count >= 10):
        return True
    # Avoid false positives from teacher help/training pages.
    if negative_content >= 2 and not has_strong and directory_controls == 0:
        return False
    return False


# ── Link scoring ──

def _score_link(href: str, text: str) -> int:
    """Score how likely a link leads to a staff directory."""
    score = 0
    href_lower = href.lower()
    text_lower = text.lower().strip()
    anchor_words = text_lower.split()

    # High value: explicit staff/faculty directory links
    high = ["staff directory", "faculty directory", "our staff",
            "our faculty", "our team", "meet our", "staff list",
            "faculty & staff", "faculty and staff", "staff & directory"]
    for kw in high:
        if kw in text_lower:
            score += 15

    # Medium value
    medium = ["staff", "faculty", "directory", "teachers", "team"]
    for kw in medium:
        if kw == text_lower or kw in href_lower:
            score += 8

    for kw in config.STAFF_LINK_POSITIVE_HINTS:
        if kw in text_lower:
            score += 4
        if f"/{kw}" in href_lower:
            score += 4

    # STEM departments
    for kw in ["science", "math", "stem"]:
        if kw in text_lower or kw in href_lower:
            score += 5

    # URL path match
    for p in config.STAFF_URL_PATTERNS:
        if p in href_lower:
            score += 8

    # Kill non-relevant links
    for kw in config.STAFF_LINK_NEGATIVE_HINTS:
        if kw in href_lower or kw in text_lower:
            score -= 15

    # Penalize long sentence-like anchors that are usually article content.
    if len(anchor_words) >= 8:
        score -= 6

    # Homepage/self links are rarely useful as staff candidates.
    if href_lower.endswith("/") and text_lower in {"home", "district home", ""}:
        score -= 20

    return score


# ── Main crawl logic ──

async def find_staff_pages(page: Page, start_url: str) -> list[str]:
    """Find staff/faculty/directory pages by checking homepage links first."""
    parsed = urlparse(start_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    start_normalized = _normalize_url(start_url)

    staff_pages: list[str] = []
    staff_page_set: set[str] = set()
    checked_urls: set[str] = set()

    def add_staff_page(url: str):
        normalized = _normalize_url(url)
        if normalized and normalized not in staff_page_set:
            staff_page_set.add(normalized)
            staff_pages.append(normalized)

    # Phase 1: Load homepage and find staff links from navigation
    print(f"  🔍 Checking homepage for staff links...")
    content = await get_page_content(page, start_url, quiet=True)
    if content:
        if _is_staff_page(content["text"], start_url):
            print(f"    🎯 Start URL is a staff page!")
            add_staff_page(start_url)
            # Fast path: when user passes a concrete staff-directory URL directly,
            # skip expensive extra discovery phases.
            if _is_direct_staff_directory_url(start_url):
                return [start_normalized]

        links = await _get_links(page)
        scored: dict[str, tuple[int, str]] = {}
        for link in links:
            href = link.get("href", "")
            text = link.get("text", "")
            try:
                lp = urlparse(href)
                if lp.netloc and lp.netloc != parsed.netloc:
                    continue
            except Exception:
                continue
            full = _normalize_url(urljoin(start_url, href))
            if full == start_normalized:
                continue
            if not _is_staff_candidate_url(full):
                continue
            s = _score_link(href, text)
            if s >= config.MIN_LINK_SCORE and full not in staff_pages:
                previous = scored.get(full)
                if not previous or s > previous[0]:
                    scored[full] = (s, text)

        scored_links = sorted(
            [(s, full, text) for full, (s, text) in scored.items()],
            key=lambda x: x[0],
            reverse=True,
        )

        # Visit top-scored links
        visited = {start_url}
        for s, link_url, link_text in scored_links[:config.MAX_STAFF_LINK_CHECKS]:
            if link_url in visited:
                continue
            visited.add(link_url)
            checked_urls.add(link_url)
            print(f"    📋 [{s}] {link_text[:50]} → {link_url}")
            c = await get_page_content(page, link_url, quiet=True)
            if c and _is_staff_page(c["text"], link_url):
                print(f"    🎯 FOUND: {link_url}")
                add_staff_page(link_url)
                if _is_direct_staff_directory_url(link_url):
                    print("    ⚡ Using verified directory immediately")
                    return [_normalize_url(link_url)]
            await asyncio.sleep(0.2)

    # Phase 2: Broader same-domain discovery crawl for hidden staff links
    discovered = await _discover_staff_pages(
        page=page,
        start_url=start_url,
        domain=parsed.netloc,
        previsited=checked_urls,
    )
    for url in discovered:
        add_staff_page(url)
    preferred_discovered = _best_direct_directory(staff_pages)
    if preferred_discovered:
        print("    ⚡ Using verified directory immediately")
        return [preferred_discovered]

    # Phase 3: Sitemap discovery catches hidden CMS directory URLs.
    sitemap_urls = await _discover_staff_urls_from_sitemap(page, start_url, parsed.netloc)
    for url in sitemap_urls:
        add_staff_page(url)

    # Phase 4: Try common paths (runs even if we found some pages)
    print(f"  🔍 Trying common staff paths...")
    for path in config.STAFF_URL_PATTERNS:
        test_url = _normalize_url(base_url + path)
        if test_url in staff_page_set:
            continue
        content = await get_page_content(page, test_url, quiet=True)
        if content and _is_staff_page(content["text"], test_url):
            print(f"    🎯 FOUND: {test_url}")
            add_staff_page(test_url)
            if _is_direct_staff_directory_url(test_url):
                print("    ⚡ Using verified directory immediately")
                return [test_url]
        await asyncio.sleep(0.2)

    # Phase 5: If still nothing, keep highest-scored candidates as fallback.
    if not staff_pages:
        print(f"  🔍 Using top scored link fallbacks...")
        fallback_urls = await _discover_fallback_candidates(page, start_url, parsed.netloc)
        for u in fallback_urls:
            add_staff_page(u)

    ranked = sorted(staff_pages, key=_staff_url_priority, reverse=True)
    return ranked[:config.MAX_STAFF_LINK_CHECKS]


async def _discover_staff_pages(page: Page, start_url: str, domain: str,
                                previsited: set[str] | None = None) -> list[str]:
    """Breadth-first discovery for hidden staff directory links."""
    previsited = previsited or set()
    queue = deque([(start_url, 0)])
    seen = {_normalize_url(start_url)}
    visited_count = 0
    found: list[str] = []
    found_set: set[str] = set()

    print("  🔎 Deep discovery crawl for staff pages...")
    while queue and visited_count < config.MAX_DISCOVERY_VISITS:
        current_url, depth = queue.popleft()
        current_url = _normalize_url(current_url)
        if not current_url or current_url in previsited:
            continue
        if not _is_same_domain(current_url, domain):
            continue

        visited_count += 1
        content = await get_page_content(page, current_url, quiet=True)
        if not content:
            continue

        if (
            _is_staff_page(content["text"], current_url)
            and _is_staff_candidate_url(current_url)
            and current_url not in found_set
        ):
            found_set.add(current_url)
            found.append(current_url)
            print(f"    🎯 FOUND (discovery): {current_url}")

        if depth >= config.MAX_CRAWL_DEPTH:
            continue

        links = await _get_links(page)
        for link in links:
            href = link.get("href", "")
            text = link.get("text", "")
            full = _normalize_url(urljoin(current_url, href))
            if not full or full in seen:
                continue
            if not _is_same_domain(full, domain):
                continue
            if not _is_staff_candidate_url(full) and not (depth == 0 and _is_nav_hub_link(href, text)):
                continue

            score = _score_link(href, text)
            should_enqueue = False
            if score >= config.SECONDARY_LINK_SCORE and _is_staff_candidate_url(full):
                should_enqueue = True
            elif depth == 0 and _is_nav_hub_link(href, text):
                should_enqueue = True

            if should_enqueue:
                seen.add(full)
                queue.append((full, depth + 1))

    return found


async def _discover_staff_urls_from_sitemap(page: Page, start_url: str, domain: str) -> list[str]:
    """Read common sitemap files and extract staff-like URLs."""
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    pending = deque([
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/wp-sitemap.xml",
    ])
    fetched: set[str] = set()
    found: set[str] = set()

    print("  🗺️ Checking sitemap for staff URLs...")
    while pending and len(fetched) < config.MAX_SITEMAP_FETCHES:
        sitemap_url = _normalize_url(pending.popleft())
        if not sitemap_url or sitemap_url in fetched:
            continue
        fetched.add(sitemap_url)

        try:
            resp = await page.goto(
                sitemap_url,
                wait_until="domcontentloaded",
                timeout=config.PAGE_TIMEOUT,
            )
            if not resp or resp.status >= 400:
                continue
            raw = await page.content()
        except Exception:
            continue

        for loc in _extract_xml_locs(raw):
            normalized = _normalize_url(loc)
            if not normalized or not _is_same_domain(normalized, domain):
                continue
            if ".xml" in normalized.lower() and "sitemap" in normalized.lower():
                if normalized not in fetched:
                    pending.append(normalized)
                continue
            if _is_staff_candidate_url(normalized):
                found.add(normalized)

    if found:
        print(f"    🎯 Found {len(found)} staff-like URL(s) via sitemap")
    return sorted(found, key=_staff_url_priority, reverse=True)[:config.MAX_STAFF_LINK_CHECKS]


async def _discover_fallback_candidates(page: Page, start_url: str, domain: str) -> list[str]:
    """Return top same-domain staff-like links even if content check is inconclusive."""
    content = await get_page_content(page, start_url, quiet=True)
    if not content:
        return []
    links = await _get_links(page)
    scored: dict[str, int] = {}
    for link in links:
        href = link.get("href", "")
        text = link.get("text", "")
        full = _normalize_url(urljoin(start_url, href))
        if not full or not _is_same_domain(full, domain):
            continue
        if not _is_staff_candidate_url(full):
            continue
        score = _score_link(href, text)
        if score >= config.MIN_LINK_SCORE:
            scored[full] = max(score, scored.get(full, -999))
    return [u for u, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:3]]


async def scrape_paginated_directory(page: Page, url: str) -> list[dict]:
    """Handle paginated staff directories.
    Prefers direct URL navigation for `const_page`/`page_no`/`page` params,
    then falls back to click-based pagination.
    Returns list of {html, text, url} for each page view."""
    pages_content = []

    content = await get_page_content(page, url)
    if not content:
        return []
    pages_content.append(content)
    seen_fingerprints = {_content_fingerprint(content["text"])}

    # Detect total pages from pagination links
    total_pages = await _detect_total_pages(page)
    if total_pages > 1:
        print(f"    📑 Detected {total_pages} total pages")

    max_pages = min(total_pages, 50)
    pagination_param = await _detect_pagination_param(page)

    use_direct_navigation = bool(pagination_param and pagination_param != "const_page")

    if max_pages > 1 and use_direct_navigation:
        repeated_pages = 0
        for page_num in range(2, max_pages + 1):
            page_url = _build_paginated_url(url, pagination_param, page_num)
            paged_content = await get_page_content(page, page_url, quiet=True)
            if not paged_content:
                print(f"    ⏭️  Could not load page {page_num} URL, stopping")
                break

            fingerprint = _content_fingerprint(paged_content["text"])
            if fingerprint in seen_fingerprints:
                repeated_pages += 1
                if repeated_pages >= 2:
                    print(f"    ⏭️  Repeated content at page {page_num}, stopping")
                    break
                continue

            repeated_pages = 0
            seen_fingerprints.add(fingerprint)
            if page_num <= 5 or page_num % 5 == 0:
                print(f"    📄 Page {page_num}/{max_pages}: {len(paged_content['text'])} chars")
            pages_content.append(paged_content)
    else:
        # Some Blackboard-style directories with const_page can open on the last page.
        # Force page 1 first, then advance with click-based pagination.
        if pagination_param == "const_page":
            if await _click_next_page(page, 1):
                await page.wait_for_timeout(config.PAGINATION_WAIT)
                html = await page.content()
                text = await page.inner_text("body")
                page_one = {"html": html, "text": text, "url": page.url}
                pages_content = [page_one]
                seen_fingerprints = {_content_fingerprint(text)}

        repeated_pages = 0
        for page_num in range(2, max_pages + 1):
            clicked = await _click_next_page(page, page_num)
            if not clicked:
                print(f"    ⏭️  Could not navigate to page {page_num}, stopping")
                break
            await page.wait_for_timeout(config.PAGINATION_WAIT)
            html = await page.content()
            text = await page.inner_text("body")
            fingerprint = _content_fingerprint(text)
            if fingerprint in seen_fingerprints:
                repeated_pages += 1
                if repeated_pages >= 3:
                    print(f"    ⏭️  Repeated content at page {page_num}, stopping")
                    break
                continue

            repeated_pages = 0
            seen_fingerprints.add(fingerprint)
            if page_num <= 5 or page_num % 5 == 0:
                print(f"    📄 Page {page_num}/{max_pages}: {len(text)} chars")
            pages_content.append({"html": html, "text": text, "url": page.url})

    return pages_content


async def _detect_total_pages(page: Page) -> int:
    """Detect total number of pagination pages from current page."""
    try:
        result = await page.evaluate(
            "() => {"
            "  const links = document.querySelectorAll('a[href]');"
            "  let maxPage = 1;"
            "  for (const a of links) {"
            "    const href = a.href;"
            "    const text = (a.innerText || '').trim();"
            "    const m = href.match(/[?&](?:page_no|const_page|page)=(\\d+)/);"
            "    if (m) { const n = parseInt(m[1]); if (n > maxPage) maxPage = n; }"
            "    if (/^\\d+$/.test(text)) { const n = parseInt(text); if (n > maxPage && n < 200) maxPage = n; }"
            "  }"
            "  return maxPage;"
            "}"
        )
        return result
    except Exception:
        return 1


async def _detect_pagination_param(page: Page) -> str | None:
    """Detect the query param key used for pagination links."""
    try:
        return await page.evaluate(
            "() => {"
            "  const counts = { const_page: 0, page_no: 0, page: 0 };"
            "  const links = document.querySelectorAll('a[href]');"
            "  for (const a of links) {"
            "    const href = a.getAttribute('href') || '';"
            "    for (const key of Object.keys(counts)) {"
            "      const re = new RegExp(`[?&]${key}=\\\\d+`);"
            "      if (re.test(href)) counts[key] += 1;"
            "    }"
            "  }"
            "  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);"
            "  return sorted[0][1] > 0 ? sorted[0][0] : null;"
            "}"
        )
    except Exception:
        return None


def _build_paginated_url(base_url: str, param: str, page_num: int) -> str:
    """Build a pagination URL preserving existing query parameters."""
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[param] = str(page_num)
    new_query = urlencode(query)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                       parsed.params, new_query, parsed.fragment))


def _content_fingerprint(text: str) -> tuple[int, int]:
    """Fingerprint page text after whitespace normalization."""
    normalized = re.sub(r'\s+', ' ', text).strip().lower()
    return (len(normalized), hash(normalized))


async def _click_next_page(page: Page, target_page: int) -> bool:
    """Try to click to the next page of a paginated directory."""
    try:
        # Strategy 1: Click a link whose href contains the target page number
        # This handles const_page=N, page_no=N, page=N patterns
        selectors = [
            f'a[href*="const_page={target_page}&"]',
            f'a[href*="const_page={target_page}"]',
            f'a[href*="page_no={target_page}"]',
            f'a[href*="page={target_page}"]',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=500):
                    await el.click()
                    return True
            except Exception:
                continue

        # Strategy 2: Click a numeric link matching the target page
        try:
            el = page.locator(f'a:text-is("{target_page}")').first
            if await el.is_visible(timeout=500):
                await el.click()
                return True
        except Exception:
            pass

        # Strategy 3: Click "Next" / ">" / "›" / "»" buttons
        next_selectors = [
            'a:has-text("next page")',
            'a:has-text("next")',
            'a:has-text(">")',
            'a:has-text("›")',
            'a:has-text("»")',
            'button:has-text("Next")',
            'a.next',
            '[aria-label="Next"]',
            '[aria-label="Next page"]',
        ]
        for sel in next_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=500):
                    await el.click()
                    return True
            except Exception:
                continue

        return False
    except Exception:
        return False


async def _get_links(page: Page) -> list[dict]:
    try:
        return await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: (a.innerText || '').trim().substring(0, 200)
            }));
        }""")
    except Exception:
        return []


def _normalize_url(url: str) -> str:
    """Normalize URL for deduplication and same-page comparisons."""
    normalized = (url or "").split("#")[0].strip()
    if not normalized:
        return ""
    return normalized.rstrip("/")


def _is_same_domain(url: str, domain: str) -> bool:
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return True
        lhs = parsed.netloc.lower().removeprefix("www.")
        rhs = domain.lower().removeprefix("www.")
        return lhs == rhs
    except Exception:
        return False


def _is_nav_hub_link(href: str, text: str) -> bool:
    """Identify navigation-hub pages likely to contain nested directory links."""
    combined = f"{href} {text}".lower()
    if any(bad in combined for bad in ["board", "calendar", "news", "employment", "jobs"]):
        return False
    hubs = [
        "about", "our schools", "schools", "academics", "departments",
        "campus", "directory", "staff", "faculty",
    ]
    return any(h in combined for h in hubs)


def _staff_url_priority(url: str) -> int:
    lowered = url.lower()
    score = 0
    if "staff-directory" in lowered:
        score += 30
    if "/staff" in lowered:
        score += 25
    if "/faculty" in lowered:
        score += 20
    if "/directory" in lowered:
        score += 15
    if "staff-search" in lowered or "staffsearch" in lowered:
        score += 12
    if "/teacher" in lowered:
        score += 8
    if "/people" in lowered:
        score += 4
    return score


def _is_direct_staff_directory_url(url: str) -> bool:
    """Return True when URL itself is an explicit staff-directory target."""
    normalized = _normalize_url(url)
    if not normalized:
        return False
    parsed = urlparse(normalized)
    path = (parsed.path or "").lower()
    if path in {"", "/"}:
        return False
    return _staff_url_priority(normalized) >= 20 and _is_staff_candidate_url(normalized)


def _best_direct_directory(urls: list[str]) -> str | None:
    """Pick the strongest direct staff-directory URL from discovered pages."""
    direct = [u for u in urls if _is_direct_staff_directory_url(u)]
    if not direct:
        return None
    return sorted(direct, key=_staff_url_priority, reverse=True)[0]


def _is_staff_candidate_url(url: str) -> bool:
    """Filter to URLs likely to be actual staff directories, not staff documents."""
    lowered = url.lower()
    include_markers = [
        "staff-directory", "staff-search", "staffsearch",
        "/directory", "/faculty", "/teachers", "/teacher",
        "/page/staff", "/page/faculty", "/people/staff",
    ]
    exclude_markers = [
        "/documents/", "/doc/", "/files/", "/news/", "/events/",
        "/for-current-staff", "/current-staff", "/join-our-team",
        "/salary", "/benefits", "/wellness", "/recruitment", "/retention",
        "/employment", "/jobs", "/forms", "/handbook", "/policies",
        "/board", "/calendar", "/contact-hr",
    ]
    if any(marker in lowered for marker in exclude_markers):
        return False
    if any(marker in lowered for marker in include_markers):
        return True
    return _staff_url_priority(url) >= 15


def _extract_xml_locs(xml_text: str) -> list[str]:
    """Extract URL values from <loc> tags in XML sitemap content."""
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, flags=re.IGNORECASE)


async def find_profile_links(page: Page, base_domain: str) -> list[str]:
    """Find individual teacher profile page links."""
    links = await _get_links(page)
    profiles = []
    seen = set()

    for link in links:
        href = link.get("href", "")
        text = link.get("text", "")
        try:
            lp = urlparse(href)
            if lp.netloc and lp.netloc != base_domain:
                continue
        except Exception:
            continue

        href_lower = href.lower()
        is_profile = False

        for p in ["/staff/", "/faculty/", "/teacher/", "/profile/",
                  "/bio/", "/people/", "/user/", "/cms/one"]:
            if p in href_lower:
                is_profile = True
                break

        if re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z]\.?)?$', text.strip()):
            is_profile = True

        if any(kw in text.lower() for kw in
               ["view profile", "read more", "bio", "full bio"]):
            is_profile = True

        if is_profile and href not in seen:
            seen.add(href)
            profiles.append(href)

    return profiles


async def get_contact_page(page: Page, start_url: str) -> dict | None:
    """Load contact/about page for address extraction."""
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in ["/contact", "/contact-us", "/about", "/about-us", "/"]:
        content = await get_page_content(page, base + path, quiet=True)
        if content:
            return content
    return None


async def _handle_google_consent(page: Page) -> None:
    """Accept Google consent dialog when present."""
    consent_selectors = [
        'button:has-text("I agree")',
        'button:has-text("Accept all")',
        'button:has-text("Accept everything")',
        'button:has-text("Agree")',
        'form[action*="consent"] button[type="submit"]',
        'input[type="submit"][value*="I agree"]',
    ]
    for selector in consent_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=750):
                await el.click()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(800)
                return
        except Exception:
            continue


def _normalize_google_result_url(url: str) -> str:
    """Normalize Google redirect URLs to their destination."""
    if not url:
        return ""
    cleaned = url.strip()
    parsed = urlparse(cleaned)

    if "google." in parsed.netloc and parsed.path.startswith("/url"):
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        target = query.get("q", "")
        if target:
            cleaned = unquote(target)

    cleaned = cleaned.split("#")[0]
    if "?" in cleaned:
        cleaned = cleaned.split("?", 1)[0]
    return cleaned.rstrip("/")


async def _extract_google_items(page: Page) -> list[dict]:
    """Extract Google organic result cards with robust selectors."""
    try:
        return await page.evaluate("""() => {
            const results = [];
            const containers = document.querySelectorAll('div.g, div.MjjYud, div[data-sokoban-container]');
            for (const el of containers) {
                const a = el.querySelector('a[href]');
                const h = el.querySelector('h3, [role="heading"]');
                if (!a || !h) continue;
                const snippet =
                    el.querySelector('.VwiC3b') ||
                    el.querySelector('.st') ||
                    el.querySelector('[data-sncf]') ||
                    el.querySelector('.MUxGbd');
                const title = (h.innerText || '').trim();
                const url = (a.href || '').trim();
                if (!title || !url) continue;
                results.push({
                    url,
                    title,
                    snippet: snippet ? (snippet.innerText || '').trim() : ''
                });
            }
            return results;
        }""")
    except Exception:
        return []


async def google_search_teachers(page: Page, school_name: str,
                                  domain: str) -> list[dict]:
    """Search Google for teacher info. Returns enrichment data."""
    results = []
    seen_linkedin_urls = set()
    queries = [
        f'site:linkedin.com/in "{school_name}" science OR math OR STEM teacher',
        f'"{domain}" "math teacher" OR "science teacher" email',
    ]

    for query in queries:
        try:
            encoded_query = quote_plus(query)
            search_url = (
                "https://www.google.com/search"
                f"?hl=en&gl=us&num=20&pws=0&q={encoded_query}"
            )
            print(f"    🔎 Google: {query[:60]}...")
            resp = await page.goto(search_url, wait_until="domcontentloaded",
                                    timeout=10000)
            if not resp or resp.status >= 400:
                continue

            await _handle_google_consent(page)
            await page.wait_for_timeout(config.GOOGLE_RENDER_WAIT)
            current_url = (page.url or "").lower()
            if "sorry/index" in current_url or "/recaptcha/" in current_url:
                print("    ⚠️  Google blocked this query (captcha/anti-bot)")
                continue

            items = await _extract_google_items(page)
            if not items:
                print("    ⚠️  Google returned no parseable results")
                continue

            for item in items:
                url = _normalize_google_result_url(item.get("url", ""))
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                if "linkedin.com/in/" in url:
                    if url in seen_linkedin_urls:
                        continue
                    seen_linkedin_urls.add(url)
                    name = title.split(" - ")[0].split(" | ")[0].strip()
                    role = title.split(" - ")[1].strip() if " - " in title else ""
                    results.append({
                        "name": name, "role": role,
                        "linkedin_url": url, "source": "linkedin",
                        "snippet": snippet,
                    })
            if len(results) >= 3:
                break
            await asyncio.sleep(config.GOOGLE_QUERY_DELAY)
        except Exception as e:
            print(f"    ❌ Google error: {e}")

    return results
