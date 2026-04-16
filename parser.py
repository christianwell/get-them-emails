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

            if person.get('name'):
                staff.append(person)
        else:
            i += 1

    return staff


def parse_staff_from_html(html: str, page_url: str = "") -> list[dict]:
    """Extract staff info from HTML. Uses fast regex first, LLM as fallback."""
    text = clean_html(html)

    if not text.strip() or len(text.strip()) < 50:
        return []

    # Try fast regex parsing first
    staff = _regex_parse_staff(text)
    if staff:
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
    text = clean_html(html)

    # Address is usually near top or bottom
    if len(text) > config.HTML_CHUNK_SIZE:
        # Take beginning and end
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
