import re
import base64
import config


def extract_emails_from_html(html: str) -> list[str]:
    """Extract emails from raw HTML using multiple methods."""
    emails = set()

    # Method 1: mailto links
    mailto_pattern = r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})'
    emails.update(re.findall(mailto_pattern, html, re.IGNORECASE))

    # Method 2: Cloudflare email protection (data-cfemail)
    cfemail_pattern = r'data-cfemail="([a-fA-F0-9]+)"'
    for encoded in re.findall(cfemail_pattern, html):
        decoded = _decode_cloudflare_email(encoded)
        if decoded:
            emails.add(decoded)

    # Method 3: Deobfuscate common patterns then regex scan
    text = html
    for pattern, replacement in config.EMAIL_DEOBFUSCATION:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    emails.update(re.findall(config.EMAIL_REGEX, text))

    # Method 4: Base64 encoded emails
    b64_pattern = r'(?:atob|decode)\s*\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)'
    for b64 in re.findall(b64_pattern, html):
        try:
            decoded = base64.b64decode(b64).decode('utf-8', errors='ignore')
            found = re.findall(config.EMAIL_REGEX, decoded)
            emails.update(found)
        except Exception:
            pass

    return _clean_emails(emails)


def extract_emails_from_text(text: str) -> list[str]:
    """Extract emails from plain text with deobfuscation."""
    for pattern, replacement in config.EMAIL_DEOBFUSCATION:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    emails = re.findall(config.EMAIL_REGEX, text)
    return _clean_emails(set(emails))


def _decode_cloudflare_email(encoded: str) -> str | None:
    """Decode Cloudflare's data-cfemail obfuscation."""
    try:
        key = int(encoded[:2], 16)
        decoded = ''
        for i in range(2, len(encoded), 2):
            char_code = int(encoded[i:i+2], 16) ^ key
            decoded += chr(char_code)
        if re.match(config.EMAIL_REGEX, decoded):
            return decoded.lower()
    except Exception:
        pass
    return None


def _clean_emails(emails: set[str]) -> list[str]:
    """Clean and filter email set."""
    cleaned = set()
    bad_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js',
                      '.pdf', '.doc', '.ico', '.webp')
    for email in emails:
        email = email.lower().strip().rstrip('.')
        if not email.endswith(bad_extensions):
            cleaned.add(email)
    return list(cleaned)


def infer_email_pattern(known_emails: list[str]) -> dict | None:
    """Infer email pattern from known emails.

    Returns dict with 'pattern', 'domain', 'confidence'.
    """
    if not known_emails:
        return None

    # Group by domain
    domain_emails: dict[str, list[str]] = {}
    for email in known_emails:
        parts = email.split('@')
        if len(parts) == 2:
            domain = parts[1]
            local = parts[0]
            if domain not in domain_emails:
                domain_emails[domain] = []
            domain_emails[domain].append(local)

    if not domain_emails:
        return None

    # Find the most common domain (likely the school's domain)
    school_domain = max(domain_emails, key=lambda d: len(domain_emails[d]))
    locals_list = domain_emails[school_domain]

    if len(locals_list) < 2:
        return {'pattern': 'unknown', 'domain': school_domain, 'confidence': 'low'}

    has_dot = sum(1 for l in locals_list if '.' in l)
    has_underscore = sum(1 for l in locals_list if '_' in l)

    if has_dot > len(locals_list) * 0.5:
        return {'pattern': 'first.last', 'domain': school_domain, 'confidence': 'high'}
    elif has_underscore > len(locals_list) * 0.5:
        return {'pattern': 'first_last', 'domain': school_domain, 'confidence': 'high'}
    else:
        avg_len = sum(len(l) for l in locals_list) / len(locals_list)
        if avg_len < 8:
            return {'pattern': 'flast', 'domain': school_domain, 'confidence': 'medium'}
        else:
            return {'pattern': 'firstlast', 'domain': school_domain, 'confidence': 'medium'}


def generate_email_from_pattern(name: str, pattern_info: dict) -> str | None:
    """Generate an email address from a name and inferred pattern."""
    if not name or not pattern_info:
        return None

    parts = name.strip().split()
    if len(parts) < 2:
        return None

    first = re.sub(r'[^a-z]', '', parts[0].lower())
    last = re.sub(r'[^a-z]', '', parts[-1].lower())
    domain = pattern_info['domain']
    pattern = pattern_info['pattern']

    if not first or not last:
        return None

    patterns = {
        'first.last': f"{first}.{last}@{domain}",
        'first_last': f"{first}_{last}@{domain}",
        'flast': f"{first[0]}{last}@{domain}",
        'firstlast': f"{first}{last}@{domain}",
        'first.l': f"{first}.{last[0]}@{domain}",
        'f.last': f"{first[0]}.{last}@{domain}",
    }
    return patterns.get(pattern, f"{first}.{last}@{domain}")
