from __future__ import annotations
import logging
from urllib.parse import quote_plus

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

    async def dismiss_popups(self, page: Page) -> None:
        await self._click_if_present(page,
            "button.modal__dismiss",
            "button[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
            "button.sign-in-modal__outlet-btn--light",
        )

    async def async_scrape(self, queries: list[str]) -> list[Job]:
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
