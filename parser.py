import json
import re
from openai import OpenAI
from bs4 import BeautifulSoup
import config


def get_ai_client() -> OpenAI:
    """Create OpenAI client pointed at Hack Club AI."""
    return OpenAI(
        api_key=config.AI_API_KEY,
        base_url=config.AI_BASE_URL,
    )


def clean_html(html: str) -> str:
    """Strip non-content elements from HTML. Return clean text."""
    soup = BeautifulSoup(html, 'lxml')

    # Remove non-content tags
    for tag in soup.find_all(['script', 'style', 'noscript', 'iframe',
                              'svg', 'path', 'meta', 'link']):
        tag.decompose()

    # Try to find main content area
    main = (
        soup.find('main') or
        soup.find('article') or
        soup.find('div', {'role': 'main'}) or
        soup.find('div', {'id': re.compile(r'content|main', re.I)}) or
        soup.find('div', {'class': re.compile(
            r'content|main|staff|faculty|directory|listing', re.I)})
    )

    target = main or soup.find('body') or soup
    text = target.get_text(separator='\n', strip=True)

    # Also extract any emails from href attributes (before we lose them)
    emails_from_links = []
    for a in (main or soup).find_all('a', href=True):
        href = a['href']
        if 'mailto:' in href:
            email = href.replace('mailto:', '').split('?')[0].strip()
            name_text = a.get_text(strip=True)
            if name_text and email:
                emails_from_links.append(f"{name_text}: {email}")

    if emails_from_links:
        text += "\n\nEmails found in links:\n" + "\n".join(emails_from_links)

    # Collapse excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text


def chunk_text(text: str, chunk_size: int = config.HTML_CHUNK_SIZE) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Prefer breaking at double newline
        break_pos = text.rfind('\n\n', start + chunk_size // 2, end)
        if break_pos == -1:
            break_pos = text.rfind('\n', start + chunk_size // 2, end)
        if break_pos > start:
            end = break_pos

        chunks.append(text[start:end])
        start = end

    return chunks


def _regex_parse_staff(text: str) -> list[dict]:
    """Fast regex-based staff extraction for structured directory pages.
    Works on pages with repeating Name/Role/Location/Email patterns."""
    email_re = config.EMAIL_REGEX
    phone_re = r'[\(]?\d{3}[\)]?[\s\-\.]\d{3}[\s\-\.]\d{4}'

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    staff = []
    skip_words = {'skip to', 'search', 'select', 'jump to', 'find us',
                  'phone:', 'fax:', 'showing', 'of ', 'page', 'next',
                  'previous', 'copyright', 'all rights', 'powered by',
                  'translate', 'menu', 'schools', 'home', 'keyword',
                  'first name', 'last name', 'location', 'all locations',
                  'departments', 'school district', 'high school',
                  'middle school', 'elementary', 'central school',
                  'community school', 'central office', 'school board',
                  'equity'}
    junk_name_phrases = {
        'staff directory', 'faculty directory', 'directory', 'keyword',
        'first name', 'last name', 'all locations', 'departments'
    }

    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip junk lines
        if len(line) < 3 or len(line) > 80:
            i += 1
            continue
        if any(kw in line.lower() for kw in skip_words):
            i += 1
            continue

        # A name line: mostly letters, 2-5 words, no email/phone/numbers
        words = line.split()
        is_name = (
            2 <= len(words) <= 5 and
            re.match(r'^[A-Za-z\s\.\-\'\,]+$', line) and
            not re.search(email_re, line) and
            not re.search(phone_re, line) and
            not re.search(r'\d', line) and
            len(line) >= 5 and
            not any(phrase in line.lower() for phrase in junk_name_phrases) and
            not re.search(r'\bschool\b', line.lower())
        )

        if is_name:
            # Normalize name to title case
            name = line.strip()
            if name == name.upper():
                name = name.title()
            person = {'name': name}
            i += 1

            # Consume following lines for role, location, email, phone
            consumed = 0
            while i < len(lines) and consumed < 6:
                next_line = lines[i].strip()
                if not next_line or len(next_line) < 2:
                    i += 1
                    consumed += 1
                    continue

                email_match = re.search(email_re, next_line)
                phone_match = re.search(phone_re, next_line)

                if email_match and not person.get('email'):
                    person['email'] = email_match.group().lower()
                    i += 1
                    consumed += 1
                elif next_line.lower().startswith('school:') or next_line.lower().startswith('phone:'):
                    if phone_match:
                        person['phone'] = phone_match.group()
                    i += 1
                    consumed += 1
                elif phone_match and not email_match and not person.get('phone'):
                    person['phone'] = phone_match.group()
                    i += 1
                    consumed += 1
                elif not person.get('role') and len(next_line) > 3 and not re.match(r'^\d+$', next_line):
                    person['role'] = next_line
                    i += 1
                    consumed += 1
                elif not person.get('department') and len(next_line) > 3 and len(next_line) < 80:
                    # Could be location/school name
                    person['department'] = next_line
                    i += 1
                    consumed += 1
                else:
                    break

            role_text = (person.get("role") or "").lower()
            has_contact_signal = bool(person.get("email") or person.get("phone"))
            has_role_signal = bool(re.search(
                r"\b(teacher|principal|assistant principal|counselor|director|"
                r"coordinator|specialist|librarian|psychologist|nurse|coach|"
                r"instructor|professor|educator|interventionist|paraeducator|"
                r"administrator|dean|secretary)\b",
                role_text,
            ))

            # Avoid treating nav headings and document titles as people rows.
            if person.get('name') and (has_contact_signal or has_role_signal):
                staff.append(person)
        else:
            i += 1

    return staff


def _parse_labeled_directory_rows(text: str) -> list[dict]:
    """Parse directories that use repeated Name/Titles/Locations/Email labels."""
    email_re = config.EMAIL_REGEX
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    people: list[dict] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if not _is_likely_person_name(line):
            i += 1
            continue

        # Look ahead for the labeled pattern used in many district directories.
        window = lines[i + 1:i + 8]
        joined = " ".join(window).lower()
        if "titles:" not in joined or "email" not in joined:
            i += 1
            continue

        person: dict = {"name": line}
        j = i + 1
        consumed = 0
        while j < len(lines) and consumed < 8:
            current = lines[j].strip()
            current_lower = current.lower()

            if current_lower.startswith("titles:"):
                person["role"] = current.split(":", 1)[1].strip()
            elif current_lower.startswith("locations:"):
                person["department"] = current.split(":", 1)[1].strip()
            elif current_lower.startswith("email:"):
                email_inline = re.search(email_re, current)
                if email_inline:
                    person["email"] = email_inline.group().lower()
            else:
                email_match = re.search(email_re, current)
                if email_match and not person.get("email"):
                    person["email"] = email_match.group().lower()
                    # End of this row in labeled directories.
                    j += 1
                    break

                # Start of next person row.
                if consumed >= 1 and _is_likely_person_name(current):
                    break

            j += 1
            consumed += 1

        if person.get("email") or person.get("role"):
            people.append(person)
            i = j
            continue

        i += 1

    return people


def _is_likely_person_name(name: str) -> bool:
    words = [w for w in name.strip().split() if w]
    if len(words) < 2 or len(words) > 5:
        return False
    if len(name) < 5 or len(name) > 60:
        return False
    if any(ch.isdigit() for ch in name):
        return False
    lowered = name.lower()
    bad_fragments = [
        "directory", "staff", "faculty", "department", "school",
        "district", "instruction", "contact", "search", "start over",
        "home", "email address", "office phone",
        "powerteacher", "powerschool", "schoolmessenger", "acceptable use",
        "agreement", "current topics", "score", "gradebook", "grading",
        "how to", "set up", "setup", "print", "reports", "help guides",
        "teacher help", "standards based", "recalculate",
    ]
    if any(fragment in lowered for fragment in bad_fragments):
        return False
    if not re.match(r"^[A-Za-z][A-Za-z\.\-\'\,\s]+$", name):
        return False

    # Require at least two alphabetic name-like tokens.
    clean_tokens = [t.strip(".,") for t in words]
    alpha_tokens = [t for t in clean_tokens if re.match(r"^[A-Za-z][A-Za-z'\-]*$", t)]
    if len(alpha_tokens) < 2:
        return False
    uppercase_starts = sum(
        1 for token in alpha_tokens
        if token and token[0].isupper()
    )
    if uppercase_starts < 2:
        return False
    return True


def _is_noise_staff_row(person: dict) -> bool:
    """Drop rows that look like page chrome/help content, not actual people."""
    name = (person.get("name") or "").strip()
    role = (person.get("role") or "").strip()
    department = (person.get("department") or "").strip()
    email = (person.get("email") or "").strip().lower()

    if not _is_likely_person_name(name):
        return True

    combined = f"{name} {role} {department}".lower()
    noise_terms = [
        "powerteacher", "powerschool", "schoolmessenger", "acceptable use",
        "how to", "help guide", "teacher help", "gradebook", "grading",
        "reports", "scoresheet", "current topics", "setup", "set up",
    ]
    if any(term in combined for term in noise_terms):
        return True

    if not role:
        name_words = name.split()
        if len(name_words) > 3:
            return True
        if any(
            token in name.lower()
            for token in [
                "attend", "annual", "breakdown", "wellness",
                "health fair", "understanding", "where", "how to",
                "category", "standards", "screen size", "current topics",
                "recalculate", "finalizing", "acceptable use",
            ]
        ):
            return True

    # Tutorial pages often fabricate dot-separated email locals; reject them.
    if email:
        local = email.split("@")[0]
        if local.count(".") >= 2 and not role:
            return True

    return False


def _sanitize_staff_rows(staff: list[dict]) -> list[dict]:
    cleaned = []
    for person in staff:
        if _is_noise_staff_row(person):
            continue
        cleaned.append(person)
    return cleaned


def _is_high_quality_regex_result(staff: list[dict]) -> bool:
    """Reject low-quality regex parses that are mostly page chrome/content."""
    if not staff:
        return False

    name_like = sum(1 for person in staff if _is_likely_person_name(person.get("name", "")))
    with_email_or_phone = sum(
        1 for person in staff
        if person.get("email") or person.get("phone")
    )
    with_role = sum(
        1 for person in staff
        if person.get("role") and len((person.get("role") or "").strip()) >= 3
    )
    total = len(staff)

    # Small directories can pass with one strongly structured match.
    if total <= 3:
        return name_like >= 1 and (with_email_or_phone >= 1 or with_role >= 1)

    if name_like / total < 0.7:
        return False
    if with_email_or_phone >= 2:
        return True
    if with_role / total >= 0.4:
        return True
    return False


def _detect_page_subject_hint(text: str, page_url: str) -> str | None:
    """Infer a STEM subject context from page text/url for empty-role rows."""
    combined = f"{text[:12000]} {page_url}".lower()
    subject_aliases = {
        "math": ["math", "mathematics", "algebra", "calculus"],
        "science": ["science", "biology", "chemistry", "physics"],
        "stem": ["stem", "steam", "engineering", "robotics", "computer science"],
    }

    matches = set()
    for label, aliases in subject_aliases.items():
        if any(alias in combined for alias in aliases):
            matches.add(label)

    # Only apply when page context is clearly one subject bucket.
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _apply_page_subject_hint(staff: list[dict], subject_hint: str | None) -> list[dict]:
    if not subject_hint:
        return staff
    for person in staff:
        person["page_subject_hint"] = subject_hint
        if not person.get("department"):
            person["department"] = subject_hint
    return staff


def parse_staff_from_html(html: str, page_url: str = "") -> list[dict]:
    """Extract staff info from HTML. Uses fast regex first, LLM as fallback."""
    text = clean_html(html)

    if not text.strip() or len(text.strip()) < 50:
        return []

    # Try fast deterministic parsing first for labeled directory UIs.
    staff = _parse_labeled_directory_rows(text)
    if staff:
        staff = _sanitize_staff_rows(staff)
        subject_hint = _detect_page_subject_hint(text, page_url)
        staff = _apply_page_subject_hint(staff, subject_hint)
        if _is_high_quality_regex_result(staff):
            print(f"    ⚡ Fast-parsed {len(staff)} staff (labeled)")
            for s in staff[:3]:
                print(f"       → {s.get('name', '?')} | {s.get('role', '?')} | {s.get('email', '?')}")
            if len(staff) > 3:
                print(f"       ... and {len(staff)-3} more")
            return staff
        staff = []

    # Try general regex parsing next
    staff = _regex_parse_staff(text)
    if staff:
        staff = _sanitize_staff_rows(staff)
        subject_hint = _detect_page_subject_hint(text, page_url)
        staff = _apply_page_subject_hint(staff, subject_hint)
        if not _is_high_quality_regex_result(staff):
            print(f"    ⚠️  Regex parse looked noisy ({len(staff)} rows), using AI fallback")
            staff = []
        else:
            print(f"    ⚡ Fast-parsed {len(staff)} staff (regex)")
            for s in staff[:3]:
                print(f"       → {s.get('name', '?')} | {s.get('role', '?')} | {s.get('email', '?')}")
            if len(staff) > 3:
                print(f"       ... and {len(staff)-3} more")
            return staff

    # Fallback to LLM for complex pages
    client = get_ai_client()
    chunks = chunk_text(text)
    all_staff = []
    print(f"    🧹 Cleaned HTML → {len(text)} chars text, {len(chunks)} chunk(s)")

    subject_hint = _detect_page_subject_hint(text, page_url)

    for i, chunk in enumerate(chunks):
        print(f"    🤖 Sending chunk {i+1}/{len(chunks)} to AI "
              f"({len(chunk)} chars)...")

        prompt = config.STAFF_EXTRACTION_PROMPT + f"\n\nContent:\n{chunk}"

        try:
            response = client.chat.completions.create(
                model=config.AI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=8000,
            )
            result = response.choices[0].message.content.strip()
            print(f"    📥 AI response: {len(result)} chars")
            parsed = _parse_json_array(result)
            if parsed:
                parsed = _sanitize_staff_rows(parsed)
                parsed = _apply_page_subject_hint(parsed, subject_hint)
                print(f"    ✅ Parsed {len(parsed)} staff from chunk {i+1}")
                for s in parsed[:3]:
                    print(f"       → {s.get('name', '?')} | {s.get('role', '?')} | {s.get('email', '?')}")
                if len(parsed) > 3:
                    print(f"       ... and {len(parsed)-3} more")
                all_staff.extend(parsed)
            else:
                print(f"    ⚠️  No staff parsed from chunk {i+1}")
        except Exception as e:
            print(f"    ❌ AI error: {e}")

    return all_staff


def extract_school_address(html: str) -> dict:
    """Use LLM to extract school address from page."""
    client = get_ai_client()
    # Use full page text (not clean_html) since addresses are often in
    # headers/footers that clean_html strips out.
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup.find_all(['script', 'style', 'noscript', 'iframe', 'svg']):
        tag.decompose()
    text = soup.get_text(separator='\n', strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Address is usually near top or bottom
    if len(text) > config.HTML_CHUNK_SIZE:
        text = text[:config.HTML_CHUNK_SIZE // 2] + "\n...\n" + text[-config.HTML_CHUNK_SIZE // 2:]

    prompt = config.SCHOOL_ADDRESS_PROMPT + f"\n\nContent:\n{text}"

    try:
        response = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )
        result = response.choices[0].message.content.strip()
        return _parse_json_object(result)
    except Exception as e:
        print(f"  [!] AI address error: {e}")
        return {}


def _strip_llm_wrapper(text: str) -> str:
    """Remove markdown fences and thinking tags from LLM output."""
    # Remove <think>...</think> (closed)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove unclosed <think>... (qwen3 sometimes doesn't close it)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    # Remove markdown fences
    text = re.sub(r'^```(?:json)?\s*\n?', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'\n?```\s*$', '', text.strip(), flags=re.MULTILINE)
    return text.strip()


def _parse_json_array(text: str) -> list[dict] | None:
    """Parse JSON array from LLM response."""
    text = _strip_llm_wrapper(text)

    # Try full text first
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Find the first [ and last ] to extract the array
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end+1]
        try:
            result = json.loads(candidate)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            # Try fixing truncated JSON by closing it
            # Sometimes LLM cuts off mid-array
            for fix in [']', '}]', '"}]', '", "phone": null}]']:
                try:
                    result = json.loads(candidate + fix)
                    if isinstance(result, list):
                        print(f"    🔧 Fixed truncated JSON")
                        return result
                except json.JSONDecodeError:
                    continue

    print(f"    [!] Failed to parse JSON array: {text[:200]}...")
    return None


def _parse_json_object(text: str) -> dict:
    """Parse JSON object from LLM response."""
    text = _strip_llm_wrapper(text)

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    print(f"  [!] Failed to parse JSON object: {text[:200]}...")
    return {}
