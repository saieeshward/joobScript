from __future__ import annotations
from urllib.parse import quote_plus
from .base import PlaywrightBaseScraper, country_from_gulf_location
from config import MAX_PAGES_UNFILTERED


class GulfTalentScraper(PlaywrightBaseScraper):
    site_key = "gulfttalent"
    _base_url = "https://www.gulftalent.com"
    fresh_context_per_query = True

    @property
    def list_selector(self) -> str:
        return "div.job_listing"

    def page_url(self, query: str, page_num: int) -> str:
        suffix = f"/{page_num}" if page_num > 1 else ""
        return f"{self._base_url}/jobs/search/in-all-countries/for-{quote_plus(query)}{suffix}"

    def cards_js(self, base_url: str) -> str:
        return f"""() => Array.from(document.querySelectorAll('div.job_listing')).map(c => {{
            const titleEl   = c.querySelector('h3 a');
            const companyEl = c.querySelector('span.company_name');
            const locEl     = c.querySelector('span.location');
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
        return "div.job_description"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector('div.job_description');
            return {description: d ? d.innerText.trim() : ''};
        }"""

    def country_for_card(self, card: dict) -> str:
        return country_from_gulf_location(card.get("location", ""))

    @property
    def max_pages(self) -> int:
        return MAX_PAGES_UNFILTERED
