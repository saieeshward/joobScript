from __future__ import annotations
import asyncio, html, logging, random, re
from urllib.parse import quote_plus

import requests

from playwright.async_api import Page, async_playwright
from playwright_stealth import Stealth

_stealth = Stealth()

import config
from .base import PlaywrightBaseScraper, Job, _USER_AGENT

log = logging.getLogger(__name__)


class LinkedInScraper(PlaywrightBaseScraper):
    site_key = "linkedin"
    _base_url = "https://www.linkedin.com"
    _PAGE_SIZE = 25

    _GEO_TO_COUNTRY = {
        "india": "India", "ireland": "Ireland", "uae": "UAE",
        "saudi_arabia": "Saudi Arabia", "europe": "Europe",
    }

    @property
    def list_selector(self) -> str:
        return "ul.jobs-search__results-list li"

    def page_url(self, query: str, page_num: int) -> str:
        return ""  # not used — async_scrape drives the geo loop directly

    def _geo_url(self, query: str, geo_id: str, page_num: int) -> str:
        return (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={quote_plus(query)}&geoId={geo_id}"
            f"&f_TPR=r86400&start={(page_num - 1) * self._PAGE_SIZE}"
        )

    def cards_js(self, base_url: str) -> str:
        return r"""() => Array.from(document.querySelectorAll(
            'ul.jobs-search__results-list li'
        )).map(c => {
            const titleEl   = c.querySelector('h3.base-search-card__title');
            const companyEl = c.querySelector('h4.base-search-card__subtitle');
            const locEl     = c.querySelector('span.job-search-card__location');
            const linkEl    = c.querySelector('a.base-card__full-link');
            const href      = linkEl ? (linkEl.getAttribute('href') || '').replace(/\?.*$/, '') : '';
            return {
                title:    titleEl   ? titleEl.innerText.trim()   : '',
                company:  companyEl ? companyEl.innerText.trim() : '',
                location: locEl     ? locEl.innerText.trim()     : '',
                href,
            };
        })"""

    @property
    def detail_selector(self) -> str:
        return "div.show-more-less-html__markup"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector('div.show-more-less-html__markup');
            return {description: d ? d.innerText.trim() : ''};
        }"""

    # ── Fast path: LinkedIn's public (logged-out) job-detail endpoint, ~0.4s vs ~30s in a browser tab ──────────
    _HTTP_HEADERS = {"User-Agent": _USER_AGENT, "Accept-Language": "en-IE,en;q=0.9"}
    _DESC_RE = re.compile(r'<div class="show-more-less-html__markup[^"]*">(.*?)</div>', re.S)

    @staticmethod
    def _html_to_text(fragment: str) -> str:
        t = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
        t = re.sub(r"</(p|ul|ol|div|h\d)>", "\n", t, flags=re.I)
        t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
        t = re.sub(r"</li>", "\n", t, flags=re.I)
        t = html.unescape(re.sub(r"<[^>]+>", "", t))
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()

    @classmethod
    def _http_description(cls, job_url: str) -> str:
        m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)", job_url)
        if not m:
            return ""
        api = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + m.group(1)
        for attempt in (1, 2, 3):
            try:
                r = requests.get(api, headers=cls._HTTP_HEADERS, timeout=15)
            except requests.RequestException:
                r = None
            if r is not None and r.status_code == 200:
                d = cls._DESC_RE.search(r.text)
                return cls._html_to_text(d.group(1)) if d else ""
            if r is not None and r.status_code not in (429, 500, 502, 503, 999):
                return ""
            import time as _t
            _t.sleep(2 * attempt + random.random())      # rate limited: back off and retry
        return ""

    async def _fetch_details_many(self, page: Page, urls: list[str]) -> list[str]:
        sem = asyncio.Semaphore(max(1, config.DETAIL_CONCURRENCY + 1))

        async def one(u: str) -> str:
            async with sem:
                text = await asyncio.to_thread(self._http_description, u)
                await asyncio.sleep(random.uniform(0.2, 0.6))
                return text

        results = list(await asyncio.gather(*(one(u) for u in urls)))
        missing = [i for i, t in enumerate(results) if not t]
        if missing and page is not None:   # fall back to the slow browser path only for the ones HTTP could not get
            log.info("linkedin: %d description(s) via HTTP failed, retrying in browser", len(missing))
            slow = await super()._fetch_details_many(page, [urls[i] for i in missing])
            for i, t in zip(missing, slow):
                results[i] = t
        return results

    async def dismiss_popups(self, page: Page) -> None:
        await self._click_if_present(page,
            "button.modal__dismiss",
            "button[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
            "button.sign-in-modal__outlet-btn--light",
        )

    async def _browser_scrape(self, queries: list[str]) -> list[Job]:
        """Slow fallback: drive a real browser (used only if the public HTTP endpoints stop answering)."""
        jobs: list[Job] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=_USER_AGENT)
            await _stealth.apply_stealth_async(ctx)
            page = await ctx.new_page()
            for query in queries:
                for geo_name, geo_id in config.LINKEDIN_GEOS.items():
                    country = self._GEO_TO_COUNTRY.get(geo_name, geo_name.replace("_", " ").title())
                    log.info("LinkedIn: '%s' in %s", query, geo_name)
                    found = await self._scrape_query(
                        page, query,
                        url_fn=lambda q, p, gid=geo_id: self._geo_url(q, gid, p),
                        country_fn=lambda card, c=country: c,
                    )
                    jobs.extend(found)
            await browser.close()
        return jobs

    # ── Fast path: public logged-out listing endpoint (10 cards per call, ~0.5s each) ─────────────────────────
    _LIST_API = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                 "?keywords={kw}&geoId={geo}&f_TPR=r86400&start={start}")
    _CARD_RE = re.compile(r"<li>(.*?)</li>", re.S)

    @staticmethod
    def _clean(fragment: str) -> str:
        return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()

    def _parse_cards(self, page_html: str) -> list[dict]:
        cards = []
        for block in self._CARD_RE.findall(page_html):
            t = re.search(r"base-search-card__title[^>]*>(.*?)</h3>", block, re.S)
            href = re.search(r'<a[^>]*class="base-card__full-link[^"]*"[^>]*href="([^"]+)"', block, re.S) \
                or re.search(r'href="([^"]+)"[^>]*class="base-card__full-link', block, re.S)
            if not t or not href:
                continue
            c = re.search(r"base-search-card__subtitle[^>]*>(.*?)</h4>", block, re.S)
            loc = re.search(r"job-search-card__location[^>]*>(.*?)</span>", block, re.S)
            cards.append({
                "title": self._clean(t.group(1)),
                "company": self._clean(c.group(1)) if c else "",
                "location": self._clean(loc.group(1)) if loc else "",
                "href": html.unescape(href.group(1)).split("?")[0],
            })
        return cards

    def _http_listing(self, query: str, geo_id: str, start: int):
        """Returns list of cards, or None if LinkedIn refused after retries (caller falls back to the browser)."""
        url = self._LIST_API.format(kw=quote_plus(query), geo=geo_id, start=start)
        for attempt in (1, 2, 3):
            try:
                r = requests.get(url, headers=self._HTTP_HEADERS, timeout=20)
            except requests.RequestException:
                r = None
            if r is not None and r.status_code == 200:
                return self._parse_cards(r.text)
            if r is not None and r.status_code not in (429, 500, 502, 503, 999):
                return None
            import time as _t
            _t.sleep(3 * attempt + random.random())
        return None

    async def async_scrape(self, queries: list[str]) -> list[Job]:
        per_query = getattr(config, "LINKEDIN_RESULTS_PER_QUERY", 60)
        jobs: list[Job] = []
        for query in queries:
            for geo_name, geo_id in config.LINKEDIN_GEOS.items():
                country = self._GEO_TO_COUNTRY.get(geo_name, geo_name.replace("_", " ").title())
                log.info("LinkedIn (HTTP): '%s' in %s", query[:60], geo_name)
                for start in range(0, per_query, 10):
                    cards = await asyncio.to_thread(self._http_listing, query, geo_id, start)
                    if cards is None:
                        log.warning("LinkedIn HTTP listing refused; falling back to the browser for this run")
                        return await self._browser_scrape(queries)
                    if not cards:
                        break
                    page_jobs, seen_ratio = await self._process_cards(None, cards, lambda c, cc=country: cc)
                    jobs.extend(page_jobs)
                    if seen_ratio >= config.EARLY_STOP_THRESHOLD:
                        log.info("linkedin: early stop (%.0f%% seen)", seen_ratio * 100)
                        break
                    await asyncio.sleep(random.uniform(0.3, 0.9))
        return jobs
