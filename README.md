# joobScript

> **New here? Start with [HANDOFF.md](HANDOFF.md)** — one-command setup for macOS, Linux and Windows, plus the full daily workflow. Parts of this README below (Excel output, site list) are out of date.

Automated nightly job scraper. Pulls listings from 9 job sites across India, Ireland, UAE, Saudi Arabia, and Europe — deduplicates them, and appends new ones to a single Excel file for you to review and apply from each evening.

---

## Setup

**1. Install dependencies**

```bash
pip install -r requirements.txt
```

**2. Install the headless browser** (one-time, needed for LinkedIn and GulfTalent)

```bash
playwright install chromium
```

**3. Configure your search**

Open `config.py`. The key settings:

| Setting | What it does |
|---|---|
| `SEARCH_QUERIES` | List of role/keyword strings to search across all sites |
| `ENABLED_SITES` | Comment out any site to skip it |
| `PRIORITY_KEYWORDS` | Jobs whose title/description matches get a ★ and yellow highlight in Excel |
| `EXCLUDE_KEYWORDS` | Jobs whose title matches are dropped entirely (e.g. "sales", "recruiter") |
| `MAX_PAGES_PER_QUERY` | How many listing pages to scrape per query per site (default 3) |
| `FETCH_DESCRIPTIONS` | Set to `False` for a fast first run — skips detail pages, no description/requirements |
| `MAX_JOBS_PER_RUN` | Safety cap on total rows written per night. Priority jobs are never cut first. |

---

## Running

```bash
python main.py
```

On the first run it creates `jobs.xlsx`. Every subsequent run appends only new jobs — duplicates are automatically skipped based on the apply link.

Logs are written to `logs/run.log` and also printed to stdout.

---

## Output — `jobs.xlsx`

Each row is one job. The columns are:

| Column | Description |
|---|---|
| Priority | ★ if the job matched a `PRIORITY_KEYWORDS` term. Entire row is yellow-highlighted. |
| Job Title | Role name |
| Company | Employer |
| Location | City / region from the listing |
| Apply Link | Clickable hyperlink to the job page |
| Source Site | Which scraper found it (`naukri`, `linkedin`, etc.) |
| Scraped On | Date it was first picked up |
| Description | Full job description text |
| Requirements | Bullet points extracted from the listing |
| Status | **You fill this in** — e.g. `Applied`, `Rejected`, `Saved` |
| Notes | **You fill this in** — anything you want to remember |

Priority jobs always appear at the top of each day's batch (sorted before non-priority), and are never cut by `MAX_JOBS_PER_RUN`.

---

## Site coverage

| Site | Region | Approach |
|---|---|---|
| Naukri | India | requests + BeautifulSoup |
| Indeed (India) | India | requests + BeautifulSoup |
| Indeed (Ireland) | Ireland | requests + BeautifulSoup |
| IrishJobs | Ireland | requests + BeautifulSoup |
| Bayt | UAE / Middle East | requests + BeautifulSoup |
| NaukriGulf | UAE / Gulf | requests + BeautifulSoup |
| Akhtaboot | Jordan / MENA | requests + BeautifulSoup |
| LinkedIn | India, Ireland, UAE, Saudi Arabia, Europe | Playwright (headless browser) |
| GulfTalent | Gulf region | Playwright (headless browser) |

LinkedIn searches are against public (non-logged-in) job search pages only. The scraper targets jobs posted in the last 24 hours via LinkedIn's `f_TPR=r86400` filter.

---

## Nightly cron (optional)

To run this automatically every night at 10pm:

```bash
crontab -e
```

Add:

```
0 22 * * * cd /path/to/joobScript && python main.py >> logs/run.log 2>&1
```

Replace `/path/to/joobScript` with the actual path to this folder.

---

## Troubleshooting

**A scraper returns 0 cards**
HTML selectors break when sites update their frontend. Open the scraper file (e.g. `scrapers/naukri.py`) and check the `_parse_listing` method — the CSS selectors in `soup.select(...)` may need updating to match the current page structure. Use browser DevTools (Inspect Element) on the live site to find the right selectors.

**LinkedIn shows 0 results**
LinkedIn occasionally changes its public search page layout. Check `scrapers/linkedin.py` → `_scrape_search` and update the selectors in `page.query_selector_all(...)`.

**Rate limiting / getting blocked**
Increase `REQUEST_DELAY_MIN` and `REQUEST_DELAY_MAX` in `config.py`. For persistent blocks on a site, try rotating the `User-Agent` string in `HEADERS`.

**Playwright crashes**
Make sure you ran `playwright install chromium` after installing the package. If it still crashes, try running with `headless=False` temporarily in the scraper to see what the browser is actually loading.
