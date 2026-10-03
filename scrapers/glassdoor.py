from __future__ import annotations
import logging
from urllib.parse import quote_plus

from playwright.async_api import Page, async_playwright
from playwright_stealth import Stealth

from .base import PlaywrightBaseScraper, Job, _USER_AGENT
import config

_stealth = Stealth()
log = logging.getLogger(__name__)

# country_code -> (glassdoor subdomain, locType, locId)
_CC_MAP = {
    "ie": ("ie", "N", "3"),      # Ireland
    "in": ("co.in", "N", "115"), # India
    "ae": ("ae",   "N", "9"),    # UAE
    "uk": ("co.uk","N", "2"),    # UK
    "us": ("com",  "N", "1"),    # US
}


class GlassdoorScraper(PlaywrightBaseScraper):
    fresh_context_per_query = True

    def __init__(self, country_code: str = "ie"):
        self.country_code = country_code
        self.site_key = f"glassdoor_{country_code}"
        subdomain, loc_type, loc_id = _CC_MAP.get(country_code, ("com", "N", "1"))
        self._base_url  = f"https://www.glassdoor.{subdomain}"
        self._loc_type  = loc_type
        self._loc_id    = loc_id

    @property
    def list_selector(self) -> str:
        return "li[data-test='jobListing']"

    def page_url(self, query: str, page_num: int) -> str:
        return (
            f"{self._base_url}/Job/jobs.htm"
            f"?sc.keyword={quote_plus(query)}"
            f"&locT={self._loc_type}&locId={self._loc_id}"
            f"&fromAge=1&p={page_num}"
        )

    def cards_js(self, base_url: str) -> str:
        return f"""() => Array.from(document.querySelectorAll("li[data-test='jobListing']")).map(c => {{
            const titleEl   = c.querySelector("a[data-test='job-title'], a.jobLink, a[class*='jobTitle']");
            const companyEl = c.querySelector("div[data-test='employer-name'], span.employerName, [class*='employerName']");
            const locEl     = c.querySelector("div[data-test='emp-location'], span.location, [class*='location']");
            const href      = titleEl ? (titleEl.getAttribute('href') || '') : '';
            return {{
                title:    titleEl   ? titleEl.innerText.trim()   : '',
                company:  companyEl ? companyEl.innerText.trim() : '',
                location: locEl     ? locEl.innerText.trim()     : '',
                href:     href.startsWith('http') ? href : '{base_url}' + href,
            }};
        }})"""

    @property
    def detail_selector(self) -> str:
        return "div[id='JobDescriptionContainer'], div.jobDescriptionContent, div[class*='jobDescription']"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector(
                "div[id='JobDescriptionContainer'], div.jobDescriptionContent, div[class*='jobDescription']"
            );
            return {description: d ? d.innerText.trim() : ''};
        }"""

    async def dismiss_popups(self, page: Page) -> None:
        await self._click_if_present(page,
            "button[alt='Close']",
            "span[alt='Close']",
            "button.modal_closeIcon",
            "[data-test='modal-close-btn']",
        )

    def country_for_card(self, card: dict) -> str:
        names = {"ie": "Ireland", "in": "India", "ae": "UAE", "uk": "UK", "us": "US"}
        # Glassdoor IE sometimes cross-lists non-Irish jobs (e.g. fr.glassdoor.ca).
        # Detect the real country from the job listing URL's subdomain.
        href = card.get("href", "")
        _href_to_cc = {
            ".ca": "Canada", ".com.au": "Australia", ".co.uk": "UK",
            ".de": "Germany", ".fr": "France", ".sg": "Singapore",
        }
        for suffix, country_name in _href_to_cc.items():
            if f"glassdoor{suffix}" in href:
                return country_name
        return names.get(self.country_code, self.country_code.upper())

    async def async_scrape(self, queries: list[str]) -> list[Job]:
        jobs: list[Job] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            for query in queries:
                ctx = await browser.new_context(user_agent=_USER_AGENT)
                await _stealth.apply_stealth_async(ctx)
                page = await ctx.new_page()
                found = await self._scrape_query(
                    page, query,
                    url_fn=self.page_url,
                    country_fn=self.country_for_card,
                )
                jobs.extend(found)
                await ctx.close()
            await browser.close()
        return jobs
