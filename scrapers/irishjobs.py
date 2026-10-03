from __future__ import annotations
from urllib.parse import quote_plus
from .base import PlaywrightBaseScraper
from config import MAX_PAGES_UNFILTERED


class IrishJobsScraper(PlaywrightBaseScraper):
    site_key = "irishjobs"
    _base_url = "https://www.irishjobs.ie"
    fresh_context_per_query = True

    @property
    def list_selector(self) -> str:
        return "div.job-card, article.job-result"

    def page_url(self, query: str, page_num: int) -> str:
        return f"{self._base_url}/Jobs/{quote_plus(query)}-jobs?pg={page_num}"

    def cards_js(self, base_url: str) -> str:
        return f"""() => Array.from(document.querySelectorAll('div.job-card, article.job-result')).map(c => {{
            const titleEl   = c.querySelector('h3 a, a.job-title, h2 a');
            const companyEl = c.querySelector('span.company-name, div.company, span.employer');
            const locEl     = c.querySelector('span.location, div.location, li.location');
            const href      = titleEl ? (titleEl.getAttribute('href') || '') : '';
            return {{
                title:    titleEl   ? titleEl.innerText.trim()   : '',
                company:  companyEl ? companyEl.innerText.trim() : '',
                location: locEl     ? locEl.innerText.trim()     : 'Ireland',
                href:     href.startsWith('http') ? href : '{base_url}' + href,
            }};
        }})"""

    @property
    def detail_selector(self) -> str:
        return "div.job-description, section.description"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector('div.job-description, section.description');
            return {description: d ? d.innerText.trim() : ''};
        }"""

    def country_for_card(self, _card: dict) -> str:
        return "Ireland"

    @property
    def max_pages(self) -> int:
        return MAX_PAGES_UNFILTERED
