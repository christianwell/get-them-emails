#!/usr/bin/env python3
"""
School STEM Teacher Scraper

Given US school website URL(s), finds science/math/STEM teachers
and exports enriched contact info to CSV.

Usage:
    python main.py <url1> [url2] [url3] ...
    python main.py --file urls.txt
    python main.py <url> -o output.csv
"""

import argparse
import asyncio
import sys
import re
import time
from urllib.parse import urlparse

import config
from crawler import (launch_browser, close_browser, new_page,
                     get_page_content, find_staff_pages,
                     find_profile_links, get_contact_page,
                     google_search_teachers, scrape_paginated_directory)
from email_finder import extract_emails_from_html, extract_emails_from_text
from parser import parse_staff_from_html, extract_school_address
from enricher import (is_stem_teacher, deduplicate_teachers, enrich_emails,
                      verify_emails, merge_emails_with_teachers,
                      merge_linkedin_results)
from exporter import export_csv


def rank_teacher_for_output(teacher: dict, domain: str) -> tuple[int, str]:
    """Rank teachers so strongest STEM roles surface first."""
    role = (teacher.get("role") or "").lower()
    score = 0

    if "stem" in role:
        score += 80
    if "tech ed" in role or "technology" in role:
        score += 70
    if "digital learning" in role:
        score += 60
    if "science" in role:
        score += 50
    if "math" in role or "mathematics" in role:
        score += 45
    if "teacher" in role:
        score += 15

    return (score, teacher.get("name") or "")


async def scrape_school(url: str, page, output_file: str | None = None) -> list[dict]:
    """Scrape one school. Returns list of STEM teachers."""

    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    if not output_file:
        safe = re.sub(r'[^a-zA-Z0-9]', '_', domain)
        output_file = f"output_{safe}.csv"

    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  🏫 Scraping: {url}")
    print(f"{'='*60}")

    # ── 1. Find staff pages ──
    print("\n[1/5] 🔍 Finding staff pages...")
    staff_urls = await find_staff_pages(page, url)
    if not staff_urls:
        print("  ⚠️  No staff pages found — trying URL directly")
        staff_urls = [url]
    print(f"  📊 {len(staff_urls)} staff page(s) to process")

    # ── 2. Extract from each staff page (with pagination) ──
    print("\n[2/5] 🤖 Extracting teacher data...")
    all_teachers: list[dict] = []
    page_emails: dict[str, list[str]] = {}
    seen_page_signatures: set[tuple[int, int]] = set()

    for staff_url in staff_urls:
        print(f"\n  📑 Scraping directory: {staff_url}")
        page_contents = await scrape_paginated_directory(page, staff_url)
        print(f"  📄 Got {len(page_contents)} page(s) of content")

        for i, content in enumerate(page_contents):
            normalized_text = re.sub(r'\s+', ' ', content["text"]).strip().lower()
            page_signature = (len(normalized_text), hash(normalized_text))
            if page_signature in seen_page_signatures:
                continue
            seen_page_signatures.add(page_signature)

            # Direct email extraction
            html_emails = extract_emails_from_html(content["html"])
            text_emails = extract_emails_from_text(content["text"])
            found = list(set(html_emails + text_emails))
            page_emails[content["url"]] = found
            if found:
                print(f"    📧 Page {i+1}: {len(found)} email(s)")

            # LLM parsing
            teachers = parse_staff_from_html(content["html"], content["url"])
            for t in teachers:
                t["source_url"] = content["url"]
            all_teachers.extend(teachers)
            if i < 3 or (i + 1) % 5 == 0 or i == len(page_contents) - 1:
                print(f"    👥 Page {i+1}: {len(teachers)} staff extracted")

    if not all_teachers:
        print("\n  ❌ No staff found from school website.")

    # ── 3. Google/LinkedIn fallback ──
    print("\n[3/5] 🌐 Google/LinkedIn enrichment...")
    try:
        school_name = domain.split('.')[0].replace('-', ' ').title()
        lr = await google_search_teachers(page, school_name, domain)
        if lr:
            print(f"  ✅ {len(lr)} LinkedIn results")
            all_teachers = merge_linkedin_results(all_teachers, lr)
        else:
            print("  ⚠️  No LinkedIn results (Google may have blocked)")
    except Exception as e:
        print(f"  ❌ {e}")

    if not all_teachers:
        print("\n  ❌ No teachers found from any source. Skipping.")
        return []

    # Dedup
    all_teachers = deduplicate_teachers(all_teachers)
    print(f"\n  📊 {len(all_teachers)} unique staff after dedup")

    # ── 4. Email enrichment + STEM filter ──
    print("\n[4/5] 📧 Email enrichment & STEM filtering...")
    all_found = [e for elist in page_emails.values() for e in elist]
    all_teachers = merge_emails_with_teachers(all_teachers, page_emails)
    all_teachers = enrich_emails(all_teachers, all_found)

    # Filter STEM
    stem = [t for t in all_teachers if is_stem_teacher(t)]
    if not stem:
        print("  ⚠️  No STEM teachers identified for this school. Skipping export rows.")
        return []
    else:
        print(f"  🔬 {len(stem)} STEM teachers / {len(all_teachers)} total")

    # Verify emails
    print("  🔍 Verifying emails...")
    stem = verify_emails(stem)

    # Keep only teachers with concrete found/verified/matched emails
    allowed_status = {"found", "verified", "matched"}
    stem = [
        t for t in stem
        if (t.get("email") or "").strip()
        and (t.get("email_status", "").strip().lower() in allowed_status)
    ]
    if not stem:
        print("  ⚠️  No STEM teachers with found emails after verification.")
        return []

    # Surface strongest matches first for easier validation.
    stem.sort(key=lambda t: rank_teacher_for_output(t, domain), reverse=True)

    stats = {}
    for t in stem:
        s = t.get("email_status", "missing")
        stats[s] = stats.get(s, 0) + 1
    print(f"  📧 Email breakdown: {stats}")

    # ── 5. School address + export ──
    print("\n[5/5] 📮 Getting school address & exporting...")
    contact = await get_contact_page(page, url)
    school_info = {}
    if contact:
        school_info = extract_school_address(contact["html"])
        if school_info.get("school_name"):
            print(f"  🏫 {school_info['school_name']}")
        if school_info.get("address"):
            print(f"  📍 {school_info.get('address')}, {school_info.get('city')}, "
                  f"{school_info.get('state')} {school_info.get('zip')}")

    export_csv(stem, school_info, output_file)

    elapsed = time.time() - t0
    with_email = sum(1 for t in stem if t.get("email"))
    print(f"\n  ✅ DONE in {elapsed:.0f}s — {len(stem)} STEM teachers, "
          f"{with_email} with emails → {output_file}")

    # Print all teachers found
    print(f"\n  {'Name':<30} {'Role':<25} {'Email':<35} {'Status'}")
    print(f"  {'─'*30} {'─'*25} {'─'*35} {'─'*12}")
    for t in stem:
        print(f"  {(t.get('name') or '?'):<30} "
              f"{(t.get('role') or '-'):<25} "
              f"{(t.get('email') or '-'):<35} "
              f"{t.get('email_status', '')}")

    return stem


async def main_async(urls: list[str], output: str | None):
    if not config.AI_API_KEY:
        print("❌ HACKCLUB_AI_KEY not set in .env")
        print("   Get a free key: https://ai.hackclub.com/dashboard")
        sys.exit(1)

    print(f"🚀 School STEM Teacher Scraper")
    print(f"   {len(urls)} school(s) to process")
    print(f"   AI model: {config.AI_MODEL}")

    pw, browser = await launch_browser()
    page = await new_page(browser)

    all_results = []
    try:
        for i, url in enumerate(urls):
            out = output if len(urls) == 1 else None
            if len(urls) > 1:
                safe = re.sub(r'[^a-zA-Z0-9]', '_',
                              urlparse(url).netloc.replace("www.", ""))
                out = f"output_{safe}.csv"

            results = await scrape_school(url, page, out)
            all_results.extend(results)

        if len(urls) > 1 and output:
            # Combined export
            export_csv(all_results, {}, output)
            print(f"\n📦 Combined: {len(all_results)} teachers → {output}")

    finally:
        await close_browser(pw, browser)

    print(f"\n🎉 All done! {len(all_results)} total STEM teachers found.")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape US school websites for STEM teacher contact info")
    parser.add_argument("urls", nargs="*", help="School website URL(s)")
    parser.add_argument("-f", "--file", help="File with one URL per line")
    parser.add_argument("-o", "--output", help="Output CSV file")

    args = parser.parse_args()

    urls = list(args.urls or [])
    if args.file:
        with open(args.file) as f:
            urls.extend(line.strip() for line in f if line.strip()
                       and not line.startswith('#'))

    if not urls:
        parser.print_help()
        print("\nExamples:")
        print("  python main.py https://www.cvsdvt.org/")
        print("  python main.py https://sbhs.sbschools.net/ -o results.csv")
        print("  python main.py url1 url2 url3")
        print("  python main.py --file school_urls.txt -o all_teachers.csv")
        sys.exit(1)

    asyncio.run(main_async(urls, args.output))


if __name__ == "__main__":
    main()
