"""
NaukriGulf.com scraper — Approach 2 (Playwright).
Uses page.evaluate() for atomic card extraction.
Note: disabled by default in config — same Akamai stack as naukri.com.
"""
from __future__ import annotations
import asyncio, logging
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import BaseScraper, Job
from config import REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, MAX_PAGES_PER_QUERY, FETCH_DESCRIPTIONS

log = logging.getLogger(__name__)
_BASE = "https://www.naukrigulf.com"


class NaukriGulfScraper(BaseScraper):
    site_key = "naukrigulf"

    def scrape(self, queries: list[str]) -> list[Job]:
        return asyncio.run(self._run(queries))

    async def _run(self, queries: list[str]) -> list[Job]:
        jobs: list[Job] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            for query in queries:
                found = await self._scrape_query(page, query)
                jobs.extend(found)
            await browser.close()
        return jobs

    async def _scrape_query(self, page: Page, query: str) -> list[Job]:
        jobs: list[Job] = []
        slug = query.replace(" ", "-").lower()

        for page_num in range(1, MAX_PAGES_PER_QUERY + 1):
            url = f"{_BASE}/{slug}-jobs-{page_num}" if page_num > 1 else f"{_BASE}/{slug}-jobs"
            log.info("NaukriGulf: '%s' page %d", query, page_num)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector("div.ng-box.job-listing, article.jobTuple, div.job-tuple", timeout=15_000)
            except PWTimeout:
                log.warning("NaukriGulf: timeout '%s' page %d", query, page_num)
                break

            cards = await page.evaluate(f"""
                () => Array.from(document.querySelectorAll('div.ng-box.job-listing, article.jobTuple, div.job-tuple')).map(card => {{
                    const titleEl   = card.querySelector('a.desig, a.title, a.jobTitle');
                    const companyEl = card.querySelector('span.comp-name, a.subTitle, span.companyName');
                    const locEl     = card.querySelector('span.loc, li.location span, span.locWdth');
                    const href      = titleEl ? (titleEl.getAttribute('href') || '') : '';
                    return {{
                        title:   titleEl   ? titleEl.innerText.trim()   : '',
                        company: companyEl ? companyEl.innerText.trim() : '',
                        location:locEl     ? locEl.innerText.trim()     : '',
                        href:    href.startsWith('http') ? href : '{_BASE}' + href,
                    }};
                }})
            """)

            if not cards:
                break

            log.info("NaukriGulf: %d cards page %d", len(cards), page_num)
            for card in cards:
                if not card.get("title") or not card.get("href"):
                    continue
                description, requirements = "", []
                if FETCH_DESCRIPTIONS:
                    description, requirements = await self._fetch_detail(page, card["href"])
                jobs.append(Job(
                    job_name=card["title"],
                    company=card.get("company", ""),
                    location=card.get("location", ""),
                    apply_link=card["href"],
                    description=description,
                    requirements=requirements,
                    source_site=self.site_key,
                ))
                await page.wait_for_timeout(int(REQUEST_DELAY_MIN * 500))

            await page.wait_for_timeout(int(REQUEST_DELAY_MAX * 1000))

        return jobs

    async def _fetch_detail(self, page: Page, url: str) -> tuple[str, list[str]]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_selector("div.job-desc, section.job-desc", timeout=10_000)
        except PWTimeout:
            return "", []
        result = await page.evaluate("""
            () => {
                const desc = document.querySelector('div.job-desc, section.job-desc');
                return {
                    description: desc ? desc.innerText.trim() : '',
                    requirements: Array.from(document.querySelectorAll('ul.key-skill li, div.key-skill span'))
                                      .map(el => el.innerText.trim()).filter(t => t),
                };
            }
        """)
        return result.get("description", ""), result.get("requirements", [])
