from __future__ import annotations
from .base import PlaywrightBaseScraper, country_from_gulf_location
from config import MAX_PAGES_UNFILTERED


class BaytScraper(PlaywrightBaseScraper):
    site_key = "bayt"
    _base_url = "https://www.bayt.com"
    fresh_context_per_query = True

    @property
    def list_selector(self) -> str:
        return "li[data-js-job]"

    def page_url(self, query: str, page_num: int) -> str:
        slug = query.replace(" ", "-")
        suffix = f"/{page_num}/" if page_num > 1 else "/"
        return f"{self._base_url}/en/international/jobs/{slug}-jobs{suffix}"

    def cards_js(self, base_url: str) -> str:
        return f"""() => Array.from(document.querySelectorAll('li[data-js-job]')).map(c => {{
            const titleEl   = c.querySelector('h2.jb-title a, a[data-js-aid="jobTitleLink"]');
            const companyEl = c.querySelector('b.jb-company, span[data-js-aid="jobCompanyName"]');
            const locEl     = c.querySelector('span.jb-loc, span[data-js-aid="jobLocation"]');
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
        return "div#the-job-description, div.t-break"

    def detail_js(self) -> str:
        return """() => {
            const d = document.querySelector('div#the-job-description, div.t-break');
            return {description: d ? d.innerText.trim() : ''};
        }"""

    def country_for_card(self, card: dict) -> str:
        return country_from_gulf_location(card.get("location", ""))

    @property
    def max_pages(self) -> int:
        return MAX_PAGES_UNFILTERED
