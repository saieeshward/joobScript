# ── config.py ──────────────────────────────────────────────────────────────────
# This is the only file you need to edit to run the scraper for your own search.
# ───────────────────────────────────────────────────────────────────────────────


# ── What to search for ────────────────────────────────────────────────────────
# One search query per site per run. Keep this list focused — every extra query
# multiplies scraping time across all enabled sites.
SEARCH_QUERIES = [
    # ML / AI / Data Science roles
    '"machine learning engineer" OR "data scientist" OR "AI engineer" OR "deep learning engineer" OR "MLOps engineer" OR "NLP engineer" OR "computer vision engineer"',

    # Data / Backend / Python roles
    '"data engineer" OR "python developer" OR "backend engineer" OR "data analyst" OR "business intelligence engineer"',

    # Internships
    '("machine learning" OR "data scientist" OR "data engineer" OR "AI" OR "software engineer" OR "data analyst") AND ("intern" OR "internship")',

    # Food Tech / Restaurant Technology
    '"food tech" OR "food technology" OR "restaurant technology" OR "AgriTech" OR "food data" OR "food AI" OR "culinary technology" OR "hospitality technology"',
]


# ── Which sites to scrape ─────────────────────────────────────────────────────
# Comment out any line to disable that site.
# "linkedin" and "indeed" are controlled by LINKEDIN_GEOS and INDEED_COUNTRIES below.
ENABLED_SITES = [
    "linkedin",      # ✅ working — regions set by LINKEDIN_GEOS
    "indeed",        # ✅ working — countries set by INDEED_COUNTRIES
    "glassdoor",     # ✅ working — countries set by GLASSDOOR_COUNTRIES
    # "bayt",        # ❌ blocked — 0 results on all queries
    # "irishjobs",   # ❌ blocked — 0 results on all queries
    # "gulfttalent", # ❌ blocked — request timeout
    # "akhtaboot",   # ❌ blocked — scraper error
    # "naukri",      # ❌ blocked — Akamai bot protection
    # "naukrigulf",  # ❌ blocked — request timeout
]


# ── LinkedIn: which regions to search ─────────────────────────────────────────
# Comment out any region to skip it. Key = label used in logs, value = geoId.
# Full list of geoIds: linkedin.com/jobs → filter by location → inspect network tab.
LINKEDIN_GEOS = {
    "ireland":      "104738515",
    # "india":        "102713980",
    # "uae":          "104305776",
    # "saudi_arabia": "101004422",
    # "europe":       "91000000",
}


# ── Indeed: which countries to search ─────────────────────────────────────────
# One scraper instance is spawned per country code.
# Common codes: "ie" Ireland, "in" India, "ae" UAE, "uk" UK, "us" US, "sg" Singapore
INDEED_COUNTRIES = [
    "ie",   # Ireland
    # "in",   # India
    # "ae",   # UAE
]


# ── Glassdoor: which countries to search ──────────────────────────────────────
# Common codes: "ie" Ireland, "in" India, "ae" UAE, "uk" UK, "us" US
GLASSDOOR_COUNTRIES = [
    "ie",   # Ireland
    # "in",   # India
    # "ae",   # UAE
]


# ── Location priority ─────────────────────────────────────────────────────────
# Jobs are sorted by the first matching substring (case-insensitive).
# Lower index = shown first. Jobs matching nothing get rank len(list).
LOCATION_PRIORITY = [
    "dublin",
    "limerick",
    "cork",
    "ireland",
    "saudi",
    "dubai",
    "india",
]


# ── Keyword filters ───────────────────────────────────────────────────────────
# Jobs whose TITLE contains any EXCLUDE keyword are dropped entirely.
EXCLUDE_KEYWORDS = [
    "sales", "marketing", "recruiter", "HR", "finance",
    "SAP", "manual testing", "QA tester",
    "senior", "Sr.", "Sr ", "staff", "principal", "lead", "manager", "director", "head of", "VP", "vice president",
]

# Jobs whose title or description contains any PRIORITY keyword are sorted to
# the top of the output and marked with ★ in Excel.
PRIORITY_KEYWORDS = [
    "machine learning", "deep learning", "NLP", "AI", "data science",
    "python", "MLOps", "computer vision", "LLM", "neural network",
    "intern", "internship",
]
# ── Output ────────────────────────────────────────────────────────────────────
# JSONL format — one job per line, easy to parse in any language.
OUTPUT_FILE = "jobs.jsonl"


# ── Concurrency & rate limiting ───────────────────────────────────────────────
# MAX_CONCURRENT_SCRAPERS: how many browsers run at the same time.
#   Keep at 2–3. Higher values trigger bot detection on most job sites.
MAX_CONCURRENT_SCRAPERS = 3

# SCRAPER_START_DELAY_S: seconds between each scraper's launch.
#   Staggers the initial burst so sites don't see a wall of requests at t=0.
SCRAPER_START_DELAY_S = 2.0

# Milliseconds to wait between fetching individual job detail pages.
REQUEST_DELAY_MIN_MS = 700

# Milliseconds to wait between listing pages (page 1 → page 2 etc.).
REQUEST_DELAY_MAX_MS = 5_000


# ── Scraper behaviour ─────────────────────────────────────────────────────────
# Pages to scrape per query on sites that support a 24h date filter (LinkedIn, Indeed).
# Each page ≈ 10–25 results.
MAX_PAGES_PER_QUERY = 1

# Pages to scrape per query on sites with NO date filter (Bayt, IrishJobs, GulfTalent, Akhtaboot).
# Keep at 1 — rely on seen_jobs.db to avoid reprocessing old results on subsequent runs.
MAX_PAGES_UNFILTERED = 1

# Set False to skip fetching full job descriptions (much faster, but less detail for resume tailoring).
FETCH_DESCRIPTIONS = True

# Hard cap on jobs written per run. Priority jobs are never cut (sort happens before cap).
MAX_JOBS_PER_RUN = 20

# Early-stop pagination: if this fraction of a listing page's jobs are already in seen_jobs.db,
# stop fetching more pages for that query. 0.5 = stop when half the page is familiar.
EARLY_STOP_THRESHOLD = 0.7


# ── Personal overrides ────────────────────────────────────────────────────────
# personalize.py writes user_config.py (gitignored). Anything it sets wins over
# the defaults above, so you can re-run it without touching this file.
try:
    from user_config import *  # noqa: F401,F403
except ImportError:
    pass
