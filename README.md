# 🏫 School STEM Teacher Scraper

Given any US school website URL, finds **science, math, and STEM teachers** and exports their enriched contact info to CSV.

Uses **Playwright** (headless Chromium) to handle JS-rendered sites and **Hack Club AI** for parsing arbitrary HTML layouts. Also searches **Google/LinkedIn** for additional teacher data.

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt
playwright install chromium

# 2. Set API key (free: https://ai.hackclub.com/dashboard)
cp .env.example .env
# Edit .env → add your HACKCLUB_AI_KEY

# 3. Run
python main.py https://www.cvsdvt.org/
python main.py https://sbhs.sbschools.net/ -o output.csv
python main.py url1 url2 url3 -o combined.csv
python main.py --file urls.txt -o combined.csv
```

## Data Sources

| Source | What It Gets |
|---|---|
| **School website** | Names, emails, roles, departments (via Playwright crawl + LLM) |
| **Google Search** | Additional teacher listings, cached pages |
| **LinkedIn (via Google)** | Enriched titles, bios, profile URLs |
| **Email pattern inference** | Generates missing emails from `first.last@school.edu` patterns |
| **DNS/SMTP verification** | Validates email addresses exist |

## Output CSV Columns

| Column | Description |
|---|---|
| `name` | Full name |
| `email` | Email address |
| `email_status` | `found` / `verified` / `matched` |
| `role` | Job title |
| `department` | Subject area |
| `phone` | Phone number |
| `linkedin_url` | LinkedIn profile URL |
| `bio` | Bio snippet from LinkedIn |
| `school_name` | School name |
| `school_address` | Street address |
| `school_city` | City |
| `school_state` | State |
| `school_zip` | ZIP code |
| `school_phone` | School phone |
| `source_url` | URL where info was found |

## How It Works

1. **Crawl** — Playwright finds staff/directory pages by following links + trying common paths
2. **Extract** — Emails via regex, Cloudflare decoding, mailto links, deobfuscation
3. **Parse** — LLM (Hack Club AI) converts unstructured HTML → structured JSON
4. **Enrich** — Google/LinkedIn search for additional data, email pattern inference
5. **Verify** — DNS MX + SMTP RCPT TO validation
6. **Filter** — Keep only science/math/STEM teachers by role/department keywords
7. **Export** — CSV with school address from contact page

## Self-Contained

Only external APIs used:
- **Hack Club AI** (free LLM) — for HTML parsing
- **Google Search** (via Playwright) — for LinkedIn enrichment

No Reacher, no SaaS validators, no paid APIs.
