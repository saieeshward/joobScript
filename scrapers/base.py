from __future__ import annotations
import asyncio, logging, random, re, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
from playwright_stealth import Stealth

_stealth = Stealth()

from storage.seen import filter_new, mark_seen_bulk
from config import (
    REQUEST_DELAY_MIN_MS, REQUEST_DELAY_MAX_MS,
    MAX_PAGES_PER_QUERY, FETCH_DESCRIPTIONS, EARLY_STOP_THRESHOLD,
    PRIORITY_KEYWORDS, EXCLUDE_KEYWORDS,
    DETAIL_CONCURRENCY, SKIP_INTERNSHIPS, EXTRA_TITLE_KEYWORDS,
)
from job_filters import INTERN_TITLE, infer_country, title_wanted

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Shared Gulf location → country mapping used by Bayt, GulfTalent, Akhtaboot
_GULF_LOC_RULES: list[tuple[list[str], str]] = [
    (["saudi", "riyadh", "jeddah", "ksa", "makkah", "medina"], "Saudi Arabia"),
    (["dubai", "abu dhabi", "sharjah", "ajman", "uae", "emirates"], "UAE"),
    (["kuwait"], "Kuwait"),
    (["qatar", "doha"], "Qatar"),
    (["bahrain", "manama"], "Bahrain"),
    (["oman", "muscat"], "Oman"),
    (["egypt", "cairo", "alexandria"], "Egypt"),
    (["jordan", "amman"], "Jordan"),
    (["lebanon", "beirut"], "Lebanon"),
]


def country_from_gulf_location(location: str) -> str:
    loc = location.lower()
    for keywords, country in _GULF_LOC_RULES:
        if any(kw in loc for kw in keywords):
            return country
    return "Gulf"


@dataclass
class Job:
    job_name: str
    company: str
    location: str
    apply_link: str
    description: str
    requirements: list[str]
    source_site: str
    country: str = ""
    scraped_on: str = field(default_factory=lambda: date.today().isoformat())
    priority: bool = False

    @property
    def dedupe_key(self) -> str:
        try:
            p = urlparse(self.apply_link)
            _NOISE = {"utm_source", "utm_medium", "utm_campaign", "trackingId",
                      "refId", "trk", "ref", "src", "sid"}
            qs = {k: v for k, v in parse_qs(p.query).items() if k not in _NOISE}
            clean = p._replace(query=urlencode(qs, doseq=True), fragment="")
            return urlunparse(clean).rstrip("/").lower()
        except Exception:
            return self.apply_link.strip().lower()


def extract_job_id(url: str) -> str:
    """Canonical short key for a job URL, used as seen-DB primary key."""
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if "linkedin.com" in host:
            m = re.search(r"/jobs/view/(\d+)", p.path)
            if m:
                return f"li_{m.group(1)}"
        if "indeed.com" in host:
            qs = parse_qs(p.query)
            if "jk" in qs:
                return f"in_{qs['jk'][0]}"
        if "bayt.com" in host:
            m = re.search(r"-(\d{6,})", p.path)
            if m:
                return f"bayt_{m.group(1)}"
        if "naukri.com" in host:
            m = re.search(r"-(\d{8,})", p.path)
            if m:
                return f"nk_{m.group(1)}"
    except Exception:
        pass
    try:
        _NOISE = {"utm_source", "utm_medium", "utm_campaign", "trackingId",
                  "refId", "trk", "ref", "src", "sid"}
        p2 = urlparse(url)
        qs2 = {k: v for k, v in parse_qs(p2.query).items() if k not in _NOISE}
        clean = p2._replace(query=urlencode(qs2, doseq=True), fragment="")
        return urlunparse(clean).rstrip("/").lower()
    except Exception:
        return url.strip().lower()


def should_exclude(job: Job, exclude_keywords: list[str]) -> bool:
    title_lower = job.job_name.lower()
    return any(kw.lower() in title_lower for kw in exclude_keywords)


def mark_priority(job: Job, priority_keywords: list[str]) -> Job:
    haystack = (job.job_name + " " + job.description).lower()
    job.priority = any(kw.lower() in haystack for kw in priority_keywords)
    return job


class BaseScraper(ABC):
    site_key: str = ""

    @abstractmethod
    async def async_scrape(self, queries: list[str]) -> list[Job]: ...

    def scrape(self, queries: list[str]) -> list[Job]:
        """Sync wrapper — for standalone testing only. main.py calls async_scrape directly."""
        return asyncio.run(self.async_scrape(queries))


class PlaywrightBaseScraper(BaseScraper):
    """
    Shared browser lifecycle, page loop, dedup, and detail fetch.

    Subclasses must implement: list_selector, page_url(), cards_js()
    Subclasses may override: detail_selector, detail_js(), country_for_card(), max_pages,
                             dismiss_popups(), fresh_context_per_query
    """
    _base_url: str = ""
    fresh_context_per_query: bool = False  # set True to reset session state between queries

    # ── Abstract interface ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def list_selector(self) -> str:
        """CSS selector to wait for on listing pages."""
        ...

    @abstractmethod
    def page_url(self, query: str, page_num: int) -> str:
        """Listing URL for this query at 1-indexed page_num."""
        ...

    @abstractmethod
    def cards_js(self, base_url: str) -> str:
        """JS expression returning [{title, company, location, href}, ...]."""
        ...

    # ── Optional overrides ────────────────────────────────────────────────────

    @property
    def detail_selector(self) -> str:
        return ""

    def detail_js(self) -> str:
        return "() => ({description: ''})"

    def country_for_card(self, card: dict) -> str:
        return ""

    @property
    def max_pages(self) -> int:
        return MAX_PAGES_PER_QUERY

    async def dismiss_popups(self, page: Page) -> None:
        """Called after every page.goto. Override to dismiss site-specific modals."""
        pass

    # ── Engine ────────────────────────────────────────────────────────────────

    async def async_scrape(self, queries: list[str]) -> list[Job]:
        jobs: list[Job] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            if self.fresh_context_per_query:
                for query in queries:
                    ctx = await browser.new_context(user_agent=_USER_AGENT)
                    await _stealth.apply_stealth_async(ctx)
                    page = await ctx.new_page()
                    jobs.extend(await self._scrape_query(
                        page, query,
                        url_fn=self.page_url,
                        country_fn=self.country_for_card,
                    ))
                    await ctx.close()
            else:
                ctx = await browser.new_context(user_agent=_USER_AGENT)
                await _stealth.apply_stealth_async(ctx)
                page = await ctx.new_page()
                for query in queries:
                    jobs.extend(await self._scrape_query(
                        page, query,
                        url_fn=self.page_url,
                        country_fn=self.country_for_card,
                    ))
            await browser.close()
        return jobs

    async def _scrape_query(
        self,
        page: Page,
        query: str,
        url_fn: Callable[[str, int], str],
        country_fn: Callable[[dict], str],
    ) -> list[Job]:
        jobs: list[Job] = []
        for page_num in range(1, self.max_pages + 1):
            url = url_fn(query, page_num)
            t_page = time.time()
            raw_cards = None
            for attempt in (1, 2):                      # one retry: first load often hits a bot wall or a slow CDN
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await self.dismiss_popups(page)
                    await page.wait_for_selector(self.list_selector, timeout=15_000)
                    raw_cards = await page.evaluate(self.cards_js(self._base_url))
                    break
                except PWTimeout:
                    log.warning("%s: timeout '%s' p%d (attempt %d)", self.site_key, query, page_num, attempt)
                    await page.wait_for_timeout(2_000 * attempt + random.randint(0, 1_500))
            if raw_cards is None:
                break

            if not raw_cards:
                log.warning("%s: zero cards '%s' p%d — selector may be broken: %s",
                            self.site_key, query, page_num, self.list_selector)
                break

            log.info("%s: listing p%d loaded in %.0fs", self.site_key, page_num, time.time() - t_page)
            page_jobs, seen_ratio = await self._process_cards(page, raw_cards, country_fn)
            jobs.extend(page_jobs)

            if seen_ratio >= EARLY_STOP_THRESHOLD:
                log.info("%s: early stop '%s' (%.0f%% seen)", self.site_key, query, seen_ratio * 100)
                break

            if page_num < self.max_pages:
                await page.wait_for_timeout(REQUEST_DELAY_MAX_MS)

        return jobs

    async def _process_cards(self, page, raw_cards: list[dict], country_fn: Callable[[dict], str]):
        """Filter new cards, fetch descriptions, build Jobs, mark seen. Shared by every scraper (browser or HTTP).
        Returns (jobs, seen_ratio)."""
        jobs: list[Job] = []
        for c in raw_cards:
            c["job_id"] = extract_job_id(c["href"])

        new_cards, seen_ratio = filter_new(raw_cards)
        log.info("%s: %d cards (%d new, %.0f%% seen)", self.site_key, len(raw_cards), len(new_cards), seen_ratio * 100)

        _exclude_kws = [kw.lower() for kw in EXCLUDE_KEYWORDS]
        kept: list[dict] = []
        for card in new_cards:
            title = card.get("title", "")
            if not title or not card.get("href"):
                continue
            tl = title.lower()
            if any(kw in tl for kw in _exclude_kws):
                log.debug("%s: skip excluded title — %s", self.site_key, title)
                continue
            if SKIP_INTERNSHIPS and INTERN_TITLE.search(title):
                log.debug("%s: skip internship — %s", self.site_key, title)
                continue
            if not title_wanted(title, EXTRA_TITLE_KEYWORDS):
                log.debug("%s: skip off-topic title — %s", self.site_key, title)
                continue
            kept.append(card)

        # Descriptions for EVERY kept job (not just priority titles), a few at a time: the pipeline needs them
        # to read required years and sponsorship. If a site blocks detail pages (bot check) the first batch trips a
        # circuit breaker and descriptions are skipped for that site for the rest of the run.
        if FETCH_DESCRIPTIONS and self.detail_selector and kept and not getattr(self, "_details_broken", False):
            t0 = time.time()
            log.info("%s: fetching %d description(s), %d at a time", self.site_key, len(kept), DETAIL_CONCURRENCY)
            descriptions = await self._fetch_details_many(page, [c["href"] for c in kept])
            log.info("%s: descriptions done in %.0fs (%d ok, %d empty)", self.site_key, time.time() - t0,
                     sum(1 for d in descriptions if d), sum(1 for d in descriptions if not d))
        else:
            descriptions = [""] * len(kept)
        for card, description in zip(kept, descriptions):
            # A job whose description could not be fetched is still kept (title/company/location/link are enough to
            # apply, and the pipeline screens it conservatively) rather than being lost for 30 days.
            location = card.get("location", "")
            jobs.append(Job(
                job_name=card["title"],
                company=card.get("company", ""),
                location=location,
                apply_link=card["href"],
                description=description,
                requirements=[],
                source_site=self.site_key,
                country=infer_country(location, country_fn(card)),
            ))

        mark_seen_bulk([c["job_id"] for c in raw_cards])

        return jobs, seen_ratio

    async def _fetch_details_many(self, page: Page, urls: list[str]) -> list[str]:
        """Fetch job descriptions DETAIL_CONCURRENCY at a time, each in its own tab of the same browser context."""
        sem = asyncio.Semaphore(max(1, DETAIL_CONCURRENCY))
        ctx = page.context
        stats = {"ok": 0, "tried": 0}
        PROBE = 4          # if the first few fetches all fail, the site is blocking detail pages: stop wasting time

        async def one(url: str) -> str:
            async with sem:
                if getattr(self, "_details_broken", False):
                    return ""
                tab = await ctx.new_page()
                try:
                    for attempt in (1, 2):
                        text = await self._fetch_detail(tab, url)
                        if text:
                            stats["ok"] += 1
                            stats["tried"] += 1
                            return text
                        await tab.wait_for_timeout(1_000 * attempt + random.randint(0, REQUEST_DELAY_MIN_MS))
                    stats["tried"] += 1
                    if stats["tried"] >= PROBE and stats["ok"] == 0:
                        self._details_broken = True
                        log.warning("%s: first %d description fetches all failed (bot check?). Skipping descriptions "
                                    "for this site for the rest of the run.", self.site_key, stats["tried"])
                    return ""
                finally:
                    await tab.close()
                    await asyncio.sleep(random.uniform(0.2, 0.8))

        return list(await asyncio.gather(*(one(u) for u in urls)))

    async def _fetch_detail(self, page: Page, url: str) -> str:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            await self.dismiss_popups(page)
            await page.wait_for_selector(self.detail_selector, timeout=8_000)
        except PWTimeout:
            return ""
        result = await page.evaluate(self.detail_js())
        return result.get("description", "") if isinstance(result, dict) else ""

    async def _click_if_present(self, page: Page, *selectors: str) -> None:
        """Helper for dismiss_popups overrides."""
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(300)
            except Exception:
                pass
