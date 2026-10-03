from __future__ import annotations
from urllib.parse import quote_plus

from .base import PlaywrightBaseScraper

_CC_TO_COUNTRY = {"ie": "Ireland", "in": "India", "ae": "UAE"}


class IndeedScraper(PlaywrightBaseScraper):
    fresh_context_per_query = True

    def __init__(self, country_code: str):
        self.country_code = country_code
        self.site_key = f"indeed_{country_code}"
        self._base_url = f"https://{country_code}.indeed.com"

    @property
    def list_selector(self) -> str:
        return "div.job_seen_beacon"

    def page_url(self, query: str, page_num: int) -> str:
        return f"{self._base_url}/jobs?q={quote_plus(query)}&start={(page_num - 1) * 10}&fromage=1"

    def cards_js(self, base_url: str) -> str:
        return f"""() => Array.from(document.querySelectorAll('div.job_seen_beacon')).map(c => {{
            const titleEl   = c.querySelector('h2.jobTitle a');
            const companyEl = c.querySelector('[data-testid="company-name"]')
                           || c.querySelector('span.companyName')
                           || c.querySelector('[class*="companyName"]');
            const locEl     = c.querySelector('[data-testid="text-location"]')
                           || c.querySelector('div.companyLocation')
                           || c.querySelector('[class*="companyLocation"]');
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
        return "div#jobDescriptionText"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector('div#jobDescriptionText');
            return {description: d ? d.innerText.trim() : ''};
        }"""

    def country_for_card(self, _card: dict) -> str:
        return _CC_TO_COUNTRY.get(self.country_code, self.country_code.upper())
