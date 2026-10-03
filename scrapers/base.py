from __future__ import annotations
import asyncio, logging, re
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
)

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
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self.dismiss_popups(page)
                await page.wait_for_selector(self.list_selector, timeout=15_000)
            except PWTimeout:
                log.warning("%s: timeout '%s' p%d", self.site_key, query, page_num)
                break

            raw_cards = await page.evaluate(self.cards_js(self._base_url))

            if not raw_cards:
                log.warning("%s: zero cards '%s' p%d — selector may be broken: %s",
                            self.site_key, query, page_num, self.list_selector)
                break

            for c in raw_cards:
                c["job_id"] = extract_job_id(c["href"])

            new_cards, seen_ratio = filter_new(raw_cards)
            log.info("%s: %d cards '%s' p%d (%d new, %.0f%% seen)",
                     self.site_key, len(raw_cards), query, page_num,
                     len(new_cards), seen_ratio * 100)

            _priority_kws = [kw.lower() for kw in PRIORITY_KEYWORDS]
            _exclude_kws  = [kw.lower() for kw in EXCLUDE_KEYWORDS]
            for card in new_cards:
                if not card.get("title") or not card.get("href"):
                    continue
                title_lower = card["title"].lower()
                # Skip excluded titles early — no point fetching their descriptions
                if any(kw in title_lower for kw in _exclude_kws):
                    log.debug("%s: skipping excluded title — %s", self.site_key, card["title"])
                    continue
                description = ""
                is_priority = any(kw in title_lower for kw in _priority_kws)
                if FETCH_DESCRIPTIONS and self.detail_selector and is_priority:
                    log.info("%s: fetching description — %s", self.site_key, card["title"])
                    description = await self._fetch_detail(page, card["href"])
                jobs.append(Job(
                    job_name=card["title"],
                    company=card.get("company", ""),
                    location=card.get("location", ""),
                    apply_link=card["href"],
                    description=description,
                    requirements=[],
                    source_site=self.site_key,
                    country=country_fn(card),
                ))
                await page.wait_for_timeout(REQUEST_DELAY_MIN_MS)

            mark_seen_bulk([c["job_id"] for c in raw_cards])

            if seen_ratio >= EARLY_STOP_THRESHOLD:
                log.info("%s: early stop '%s' (%.0f%% seen)", self.site_key, query, seen_ratio * 100)
                break

            if page_num < self.max_pages:
                await page.wait_for_timeout(REQUEST_DELAY_MAX_MS)

        return jobs

    async def _fetch_detail(self, page: Page, url: str) -> str:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
            await self.dismiss_popups(page)
            await page.wait_for_selector(self.detail_selector, timeout=5_000)
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
