"""
Entry point. Run manually or via cron:
    python main.py
    0 8 * * * cd /path/to/joobScript && python main.py >> logs/run.log 2>&1
"""
from __future__ import annotations
import asyncio, logging, sys
from pathlib import Path

import config
from scrapers.base import should_exclude, mark_priority
from storage.jsonl import append_jobs
from storage.seen import count as seen_count

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path("logs") / "run.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main")


def build_scrapers():
    from scrapers.indeed import IndeedScraper
    from scrapers.linkedin import LinkedInScraper
    from scrapers.glassdoor import GlassdoorScraper
    from scrapers.bayt import BaytScraper
    from scrapers.irishjobs import IrishJobsScraper
    from scrapers.gulfttalent import GulfTalentScraper
    from scrapers.akhtaboot import AkhtabootScraper

    enabled = set(config.ENABLED_SITES)
    scrapers = {}

    if "linkedin" in enabled:
        scrapers["linkedin"] = LinkedInScraper()

    if "indeed" in enabled:
        for cc in config.INDEED_COUNTRIES:
            scrapers[f"indeed_{cc}"] = IndeedScraper(cc)

    if "glassdoor" in enabled:
        for cc in config.GLASSDOOR_COUNTRIES:
            scrapers[f"glassdoor_{cc}"] = GlassdoorScraper(cc)

    single_site_map = {
        "irishjobs":  IrishJobsScraper,
        "bayt":       BaytScraper,
        "gulfttalent": GulfTalentScraper,
        "akhtaboot":  AkhtabootScraper,
    }
    for key, cls in single_site_map.items():
        if key in enabled:
            scrapers[key] = cls()

    return scrapers


async def _run_all(scrapers: dict, queries: list[str]) -> list:
    sem = asyncio.Semaphore(config.MAX_CONCURRENT_SCRAPERS)

    async def run_one(site_key: str, scraper, delay: float):
        await asyncio.sleep(delay)
        async with sem:
            try:
                jobs = await scraper.async_scrape(queries)
                return site_key, jobs, None
            except Exception as exc:
                return site_key, [], exc

    tasks = [
        run_one(site_key, scraper, i * config.SCRAPER_START_DELAY_S)
        for i, (site_key, scraper) in enumerate(scrapers.items())
    ]
    results = await asyncio.gather(*tasks)

    all_jobs = []
    for site_key, jobs, exc in results:
        if exc:
            log.error("%s crashed: %s", site_key, exc, exc_info=exc)
        else:
            log.info("%s returned %d job(s)", site_key, len(jobs))
            all_jobs.extend(jobs)
    return all_jobs


def main() -> None:
    scrapers = build_scrapers()
    log.info("Running %d scraper(s) in parallel: %s", len(scrapers), list(scrapers.keys()))
    log.info("Seen DB: %d active job IDs", seen_count())
    log.info("Queries: %s", config.SEARCH_QUERIES)

    all_jobs = asyncio.run(_run_all(scrapers, config.SEARCH_QUERIES))
    log.info("Total scraped (before filters): %d", len(all_jobs))

    all_jobs = [j for j in all_jobs if not should_exclude(j, config.EXCLUDE_KEYWORDS)]
    log.info("After exclusion filter: %d", len(all_jobs))

    all_jobs = [mark_priority(j, config.PRIORITY_KEYWORDS) for j in all_jobs]

    _loc_ranks = [p.lower() for p in config.LOCATION_PRIORITY]

    def _location_rank(job) -> int:
        haystack = (job.location + " " + job.country).lower()
        for i, term in enumerate(_loc_ranks):
            if term in haystack:
                return i
        return len(_loc_ranks)

    all_jobs.sort(key=lambda j: (not j.priority, _location_rank(j)))

    if len(all_jobs) > config.MAX_JOBS_PER_RUN:
        dropped = len(all_jobs) - config.MAX_JOBS_PER_RUN
        log.warning("Capping at %d jobs — dropping %d lowest-priority.",
                    config.MAX_JOBS_PER_RUN, dropped)
        all_jobs = all_jobs[:config.MAX_JOBS_PER_RUN]

    priority_count = sum(1 for j in all_jobs if j.priority)
    log.info("Jobs to write: %d total (%d priority)", len(all_jobs), priority_count)

    written = append_jobs(all_jobs, config.OUTPUT_FILE)
    log.info("New rows written to %s: %d", config.OUTPUT_FILE, written)


if __name__ == "__main__":
    main()
