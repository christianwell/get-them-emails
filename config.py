import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Force unbuffered output so logs appear in real-time
sys.stdout.reconfigure(line_buffering=True)

# Hack Club AI API
AI_BASE_URL = "https://ai.hackclub.com/proxy/v1"
AI_API_KEY = os.getenv("HACKCLUB_AI_KEY", "")
AI_MODEL = "qwen/qwen3-32b"

# Crawl settings
MAX_CRAWL_DEPTH = 2
MAX_PAGES = 30
PAGE_TIMEOUT = 15000  # ms
JS_WAIT = 1500  # ms — wait for JS render
PAGINATION_WAIT = 1200  # ms — lighter wait after pagination navigation
CRAWL_DELAY = 0.3  # seconds between page loads
MIN_LINK_SCORE = 8  # only follow links scoring >= this
SECONDARY_LINK_SCORE = 6  # broader threshold for discovery crawl
MAX_STAFF_LINK_CHECKS = 12  # cap deep staff-link checks per site
MAX_DISCOVERY_VISITS = 24  # cap pages visited while hunting staff directories
MAX_SITEMAP_FETCHES = 6  # cap sitemap files fetched during discovery
GOOGLE_RENDER_WAIT = 1000  # ms — wait for search results DOM
GOOGLE_QUERY_DELAY = 0.5  # sec between Google queries

# URL path patterns to try directly
STAFF_URL_PATTERNS = [
    "/staff", "/faculty", "/directory", "/teachers",
    "/our-staff", "/our-team", "/staff-directory",
    "/about/staff", "/about/faculty",
    "/faculty-staff", "/faculty-and-staff",
    "/apps/pages/staff-directory", "/staff-directory/home",
    "/staff-directory/search", "/staff-search",
    "/district/staff-directory", "/schools/staff-directory",
    "/administration/staff-directory", "/about-us/staff",
    "/people", "/directory/staff", "/employees/staff-directory",
    "/site/default.aspx?pagetype=2&domainid=", "/site/default.aspx?pagetype=15",
]

STAFF_LINK_POSITIVE_HINTS = [
    "staff", "faculty", "directory", "teacher", "teachers",
    "employee", "employees", "people", "contact",
]

STAFF_LINK_NEGATIVE_HINTS = [
    "calendar", "news", "event", "lunch", "menu", "bus",
    "parent", "student", "enrollment", "registration",
    "login", "donate", "careers", "jobs", "apply", "employment",
    "twitter", "facebook", "instagram", "youtube", "athletics",
    "board", "trustees", "policy", "procurement", "resources",
    ".pdf", ".doc", "mailto:", "tel:", "javascript:",
]

# STEM subject keywords for filtering teachers
STEM_KEYWORDS = [
    # Math
    "math", "mathematics", "algebra", "geometry", "calculus",
    "trigonometry", "statistics", "pre-calculus", "precalculus",
    "ap calculus", "ap statistics", "pre-algebra",
    # Science
    "science", "biology", "chemistry", "physics",
    "earth science", "environmental science", "life science",
    "physical science", "ap biology", "ap chemistry", "ap physics",
    "anatomy", "physiology", "ecology", "geology", "astronomy",
    "marine biology", "forensic science", "zoology", "botany",
    # STEM/STEAM
    "stem", "steam", "engineering", "computer science",
    "robotics", "technology", "coding", "programming",
    "information technology", "computer", "tech ed",
    "data science", "cyber", "biomedical", "digital learning",
    "instructional technology", "design technology", "makerspace",
]

# Email regex pattern
EMAIL_REGEX = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'

# Common email obfuscation replacements
EMAIL_DEOBFUSCATION = [
    (r'\s*\[\s*at\s*\]\s*', '@'),
    (r'\s*\(\s*at\s*\)\s*', '@'),
    (r'\s+at\s+', '@'),
    (r'\s*\[\s*dot\s*\]\s*', '.'),
    (r'\s*\(\s*dot\s*\)\s*', '.'),
    (r'\s+dot\s+', '.'),
]

# HTML chunk size for LLM (chars)
HTML_CHUNK_SIZE = 10000

# LLM prompts
STAFF_EXTRACTION_PROMPT = """You are an expert at extracting structured data from school website content.
Extract ALL staff members, teachers, and faculty from the following content.

Return a JSON array where each element has these fields:
- "name": full name (string, required)
- "email": email address (string or null)
- "role": job title/position (string or null)
- "department": department or subject area (string or null)
- "phone": phone number (string or null)

Rules:
- Include EVERY person mentioned who appears to be staff/faculty/teacher
- Do NOT include students, parents, or non-staff
- If you see a department heading (like "Science Department"), apply that department to all people listed under it
- If you see subject area context (like a page about "Math"), tag people with that department
- Extract emails even if partially obfuscated
- Return ONLY valid JSON array, no markdown fences, no explanation, no extra text
- If no staff found, return: []"""

SCHOOL_ADDRESS_PROMPT = """Extract the school's name and mailing address from the following content.
This is a US school.

Return a JSON object with:
- "school_name": name of the school (string)
- "address": street address (string or null)
- "city": city (string or null)
- "state": US state abbreviation (string or null)
- "zip": zip code (string or null)
- "phone": main phone number (string or null)

Return ONLY valid JSON object, no markdown fences, no explanation, no extra text.
If you cannot find an address, still return the object with null values."""
