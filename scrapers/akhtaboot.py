from __future__ import annotations
from urllib.parse import quote_plus
from .base import PlaywrightBaseScraper, country_from_gulf_location
from config import MAX_PAGES_UNFILTERED


class AkhtabootScraper(PlaywrightBaseScraper):
    site_key = "akhtaboot"
    _base_url = "https://www.akhtaboot.com"
    fresh_context_per_query = True

    @property
    def list_selector(self) -> str:
        return "div.job-card, li.job-item, div.job-listing"

    def page_url(self, query: str, page_num: int) -> str:
        return f"{self._base_url}/en/jobs?q={quote_plus(query)}&page={page_num}"

    def cards_js(self, base_url: str) -> str:
        return f"""() => Array.from(document.querySelectorAll('div.job-card, li.job-item, div.job-listing')).map(c => {{
            const titleEl   = c.querySelector('h2 a, a.job-title, h3 a');
            const companyEl = c.querySelector('span.company, div.company-name, span.employer');
            const locEl     = c.querySelector('span.location, div.location');
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
        return "div.job-description, section#description"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector('div.job-description, section#description');
            return {description: d ? d.innerText.trim() : ''};
        }"""

    def country_for_card(self, card: dict) -> str:
        return country_from_gulf_location(card.get("location", ""))

    @property
    def max_pages(self) -> int:
        return MAX_PAGES_UNFILTERED
