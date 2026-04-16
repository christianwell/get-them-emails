import asyncio
import re
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
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

def _is_staff_page(text: str) -> bool:
    """Heuristic: is this actually a staff/faculty listing page?"""
    text_lower = text.lower()

    # Must have strong staff indicators
    strong = ["staff directory", "faculty directory", "our staff",
              "our faculty", "staff list", "faculty & staff",
              "faculty and staff", "meet our staff", "meet our teachers"]
    has_strong = any(kw in text_lower for kw in strong)

    # Or multiple weak indicators
    weak = ["teacher", "staff", "faculty", "instructor", "educator",
            "department head"]
    weak_count = sum(1 for kw in weak if kw in text_lower)

    email_count = len(re.findall(config.EMAIL_REGEX, text))
    phone_count = len(re.findall(r'[\(]?\d{3}[\)]?[\s\-\.]\d{3}[\s\-\.]\d{4}', text))

    if has_strong and (email_count >= 3 or phone_count >= 3):
        return True
    if weak_count >= 2 and email_count >= 5:
        return True
    # Also match pages with many emails and some staff-like content
    if email_count >= 3 and weak_count >= 1:
        return True
    return False


# ── Link scoring ──

def _score_link(href: str, text: str) -> int:
    """Score how likely a link leads to a staff directory."""
    score = 0
    href_lower = href.lower()
    text_lower = text.lower().strip()

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
        if kw == text_lower or kw in href_lower.split('/'):
            score += 8

    # STEM departments
    for kw in ["science", "math", "stem"]:
        if kw in text_lower or kw in href_lower:
            score += 5

    # URL path match
    for p in config.STAFF_URL_PATTERNS:
        if p in href_lower:
            score += 8

    # Kill non-relevant links
    negative = ["calendar", "news", "event", "lunch", "menu", "bus",
                "parent", "student", "enrollment", "login", "donate",
                "careers", "jobs", "apply", "employment", "twitter",
                "facebook", "instagram", "youtube", ".pdf", ".doc",
                "mailto:", "tel:", "javascript:", "curriculum",
                "assessment", "grants", "literacy", "resources",
                "choice", "design", "journey"]
    for kw in negative:
        if kw in href_lower or kw in text_lower:
            score -= 15

    return score


# ── Main crawl logic ──

async def find_staff_pages(page: Page, start_url: str) -> list[str]:
    """Find staff/faculty/directory pages by checking homepage links first."""
    parsed = urlparse(start_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    staff_pages = []

    # Phase 1: Load homepage and find staff links from navigation
    print(f"  🔍 Checking homepage for staff links...")
    content = await get_page_content(page, start_url, quiet=True)
    if content:
        if _is_staff_page(content["text"]):
            print(f"    🎯 Start URL is a staff page!")
            staff_pages.append(start_url)

        links = await _get_links(page)
        scored = []
        for link in links:
            href = link.get("href", "")
            text = link.get("text", "")
            try:
                lp = urlparse(href)
                if lp.netloc and lp.netloc != parsed.netloc:
                    continue
            except Exception:
                continue
            full = urljoin(start_url, href).split('#')[0].rstrip('/')
            s = _score_link(href, text)
            if s >= config.MIN_LINK_SCORE and full not in staff_pages:
                scored.append((s, full, text))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Visit top-scored links
        visited = {start_url}
        for s, link_url, link_text in scored[:config.MAX_STAFF_LINK_CHECKS]:
            if link_url in visited:
                continue
            visited.add(link_url)
            print(f"    📋 [{s}] {link_text[:50]} → {link_url}")
            c = await get_page_content(page, link_url, quiet=True)
            if c and _is_staff_page(c["text"]):
                print(f"    🎯 FOUND: {link_url}")
                if link_url not in staff_pages:
                    staff_pages.append(link_url)
            await asyncio.sleep(0.2)

    # Phase 2: If nothing found, try a few common paths
    if not staff_pages:
        print(f"  🔍 Trying common staff paths...")
        for path in config.STAFF_URL_PATTERNS[:6]:
            test_url = base_url + path
            if test_url in staff_pages:
                continue
            content = await get_page_content(page, test_url, quiet=True)
            if content and _is_staff_page(content["text"]):
                print(f"    🎯 FOUND: {test_url}")
                staff_pages.append(test_url)
                break
            await asyncio.sleep(0.2)

    return list(dict.fromkeys(staff_pages))


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
            search_url = f"https://www.google.com/search?q={query}&num=15"
            print(f"    🔎 Google: {query[:60]}...")
            resp = await page.goto(search_url, wait_until="domcontentloaded",
                                   timeout=10000)
            if not resp or resp.status >= 400:
                continue
            await page.wait_for_timeout(config.GOOGLE_RENDER_WAIT)

            items = await page.evaluate("""() => {
                const r = [];
                document.querySelectorAll('div.g, div[data-sokoban-container]').forEach(el => {
                    const a = el.querySelector('a');
                    const h = el.querySelector('h3');
                    const s = el.querySelector('.VwiC3b, .st, [data-sncf]');
                    if (a && h) r.push({
                        url: a.href, title: h.innerText,
                        snippet: s ? s.innerText : ''
                    });
                });
                return r;
            }""")

            for item in items:
                url = item.get("url", "")
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
