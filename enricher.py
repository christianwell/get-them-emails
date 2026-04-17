import re
import smtplib
import socket
from email_finder import infer_email_pattern, generate_email_from_pattern
import config

try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False
    print("[!] dnspython not installed — email DNS verification disabled")


def is_stem_teacher(teacher: dict) -> bool:
    """Check if a teacher teaches STEM based on role/department."""
    role = (teacher.get("role") or "").lower()
    dept = (teacher.get("department") or "").lower()
    bio = (teacher.get("bio") or "").lower()
    source_url = (teacher.get("source_url") or "").lower()
    page_subject_hint = (teacher.get("page_subject_hint") or "").lower()

    role_dept_bio = f"{role} {dept} {bio}"
    combined = f"{role_dept_bio} {source_url} {page_subject_hint}"
    educator_terms = [
        "teacher", "educator", "faculty", "instructor", "professor",
        "specialist", "coach", "department", "curriculum", "classroom",
    ]
    has_educator_signal = any(term in role_dept_bio for term in educator_terms)

    strong_keywords = {
        "math", "mathematics", "algebra", "geometry", "calculus",
        "trigonometry", "statistics", "pre-calculus", "precalculus",
        "science", "biology", "chemistry", "physics", "earth science",
        "environmental science", "life science", "physical science",
        "anatomy", "physiology", "ecology", "geology", "astronomy",
    }
    broad_keywords = {
        "stem", "steam", "engineering", "computer science", "robotics",
        "technology", "coding", "programming", "information technology",
        "tech ed", "data science", "cyber", "makerspace",
    }

    for keyword in strong_keywords:
        if keyword in role_dept_bio:
            return True

    for keyword in broad_keywords:
        if keyword in role_dept_bio and has_educator_signal:
            return True

    if page_subject_hint in {"math", "science", "stem"}:
        # Pages that are clearly subject-specific can provide subject context
        # when role data is sparse.
        if has_educator_signal or not role.strip():
            return True

    # Fallback to original keyword set against all fields for edge cases.
    for keyword in config.STEM_KEYWORDS:
        keyword = keyword.lower()
        if " " in keyword or "/" in keyword or "-" in keyword:
            if keyword in combined and (has_educator_signal or keyword in strong_keywords):
                return True
        elif re.search(rf"\b{re.escape(keyword)}\b", combined):
            if has_educator_signal or keyword in strong_keywords:
                return True

    return False


def deduplicate_teachers(teachers: list[dict]) -> list[dict]:
    """Deduplicate by normalized name, merging fields."""
    seen: dict[str, dict] = {}
    for t in teachers:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        key = re.sub(r'[^a-z ]', '', name.lower()).strip()
        if not key:
            continue

        if key in seen:
            existing = seen[key]
            for field in ["email", "role", "department", "phone",
                          "linkedin_url", "bio", "source_url"]:
                if not existing.get(field) and t.get(field):
                    existing[field] = t[field]
        else:
            seen[key] = t
    return list(seen.values())


def enrich_emails(teachers: list[dict],
                  all_found_emails: list[str]) -> list[dict]:
    """Fill in missing emails using pattern inference."""
    known = list(set(
        [t["email"] for t in teachers if t.get("email")]
        + all_found_emails
    ))

    pattern_info = infer_email_pattern(known)
    if not pattern_info:
        return teachers

    print(f"  [*] Email pattern: {pattern_info['pattern']}@"
          f"{pattern_info['domain']} ({pattern_info['confidence']})")

    for t in teachers:
        if not t.get("email") and t.get("name"):
            inferred = generate_email_from_pattern(t["name"], pattern_info)
            if inferred:
                t["email"] = inferred
                t["email_status"] = f"inferred-{pattern_info['confidence']}"

    return teachers


def verify_emails(teachers: list[dict]) -> list[dict]:
    """Verify emails via DNS MX + SMTP RCPT TO."""
    mx_cache: dict[str, bool] = {}

    for t in teachers:
        email = t.get("email")
        if not email:
            t.setdefault("email_status", "missing")
            continue

        t.setdefault("email_status", "found")

        domain = email.split("@")[1] if "@" in email else ""
        if not domain:
            continue

        # DNS MX check (cached per domain)
        if domain not in mx_cache:
            mx_cache[domain] = _check_mx(domain)

        if not mx_cache[domain]:
            t["email_status"] = "bad-domain"
            continue

        # SMTP check (best effort, only for inferred emails)
        # Emails found directly on the page are trusted
        if t["email_status"] == "inferred-high":
            result = _verify_smtp(email, domain)
            if result == "valid":
                t["email_status"] = "verified"
            elif result == "invalid":
                t["email_status"] = "rejected"
        elif t["email_status"] == "found":
            # Trust emails found on the school website
            t["email_status"] = "found"

    return teachers


def merge_emails_with_teachers(teachers: list[dict],
                                page_emails: dict[str, list[str]]) -> list[dict]:
    """Match unassigned page-level emails to teachers by name."""
    all_emails = set()
    for emails in page_emails.values():
        all_emails.update(e.lower() for e in emails)

    assigned = {t["email"].lower() for t in teachers if t.get("email")}
    unassigned = all_emails - assigned

    if not unassigned:
        return teachers

    for t in teachers:
        if t.get("email"):
            continue
        name = (t.get("name") or "").lower()
        parts = name.split()
        if len(parts) < 2:
            continue

        first = re.sub(r'[^a-z]', '', parts[0])
        last = re.sub(r'[^a-z]', '', parts[-1])
        if not first or not last:
            continue

        for email in list(unassigned):
            local = email.split("@")[0]
            if ((first in local and last in local) or
                    f"{first[0]}{last}" == local or
                    f"{first}.{last}" == local or
                    f"{first}{last}" == local or
                    f"{first}_{last}" == local or
                    f"{first[0]}.{last}" == local):
                t["email"] = email
                t["email_status"] = "matched"
                unassigned.discard(email)
                break

    return teachers


def merge_linkedin_results(teachers: list[dict],
                            linkedin_results: list[dict]) -> list[dict]:
    """Merge LinkedIn search results into teacher list."""
    teacher_names = {re.sub(r'[^a-z ]', '', (t.get("name") or "").lower()).strip()
                     for t in teachers}

    for lr in linkedin_results:
        lr_name = re.sub(r'[^a-z ]', '', (lr.get("name") or "").lower()).strip()

        if lr_name in teacher_names:
            # Enrich existing teacher
            for t in teachers:
                t_name = re.sub(r'[^a-z ]', '', (t.get("name") or "").lower()).strip()
                if t_name == lr_name:
                    if not t.get("linkedin_url"):
                        t["linkedin_url"] = lr.get("linkedin_url", "")
                    if not t.get("role") and lr.get("role"):
                        t["role"] = lr["role"]
                    if lr.get("snippet"):
                        t["bio"] = lr["snippet"]
                    break
        else:
            # New teacher from LinkedIn
            teachers.append({
                "name": lr.get("name", ""),
                "email": None,
                "role": lr.get("role", ""),
                "department": None,
                "phone": None,
                "linkedin_url": lr.get("linkedin_url", ""),
                "bio": lr.get("snippet", ""),
                "source_url": lr.get("linkedin_url", ""),
                "email_status": "missing",
            })

    return teachers


def _check_mx(domain: str) -> bool:
    """Check if domain has MX records."""
    if not HAS_DNS:
        return True
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        return len(answers) > 0
    except Exception:
        return False


def _verify_smtp(email: str, domain: str) -> str:
    """SMTP RCPT TO check. Returns 'valid', 'invalid', or 'unknown'."""
    if not HAS_DNS:
        return "unknown"
    try:
        mx_records = dns.resolver.resolve(domain, 'MX')
        mx_host = str(sorted(mx_records,
                              key=lambda x: x.preference)[0].exchange).rstrip('.')
    except Exception:
        return "unknown"

    try:
        smtp = smtplib.SMTP(timeout=5)
        smtp.connect(mx_host, 25)
        smtp.helo("verify.local")
        smtp.mail("verify@verify.local")
        code, _ = smtp.rcpt(email)
        smtp.quit()

        if code == 250:
            return "valid"
        elif code == 550:
            return "invalid"
        # 552, 553 are definite rejections; others are inconclusive
        elif code in (552, 553):
            return "invalid"
        return "unknown"
    except (smtplib.SMTPException, socket.error, OSError):
        return "unknown"
