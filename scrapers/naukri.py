"""
Naukri.com scraper — Approach 2 (Playwright).
Note: naukri.com uses Akamai bot protection — currently blocked, disabled in config.
Kept here in case a workaround is found later.
"""
from __future__ import annotations
import asyncio, logging
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import BaseScraper, Job
from config import REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, MAX_PAGES_PER_QUERY, FETCH_DESCRIPTIONS

log = logging.getLogger(__name__)
_BASE = "https://www.naukri.com"


class NaukriScraper(BaseScraper):
    site_key = "naukri"

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
            url = f"{_BASE}/{slug}-jobs?k={quote_plus(query)}&pg={page_num}"
            log.info("Naukri: '%s' page %d", query, page_num)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector("div.srp-jobtuple-wrapper, article.jobTuple", timeout=15_000)
            except PWTimeout:
                log.warning("Naukri: timeout '%s' page %d (likely blocked)", query, page_num)
                break

            cards = await page.evaluate("""
                () => Array.from(document.querySelectorAll('div.srp-jobtuple-wrapper, article.jobTuple')).map(card => {
                    const titleEl   = card.querySelector('a.title, a.jobTitle');
                    const companyEl = card.querySelector('a.subTitle, span.companyName');
                    const locEl     = card.querySelector('li.location span, span.locWdth');
                    return {
                        title:   titleEl   ? titleEl.innerText.trim()             : '',
                        company: companyEl ? companyEl.innerText.trim()           : '',
                        location:locEl     ? locEl.innerText.trim()               : '',
                        href:    titleEl   ? (titleEl.getAttribute('href') || '') : '',
                    };
                })
            """)
            if not cards:
                break

            log.info("Naukri: %d cards page %d", len(cards), page_num)
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
                if (!desc) return {description: '', requirements: []};
                return {
                    description: desc.innerText.trim(),
                    requirements: Array.from(document.querySelectorAll('ul.key-skill li, div.key-skill span'))
                                      .map(el => el.innerText.trim()).filter(t => t),
                };
            }
        """)
        return result.get("description", ""), result.get("requirements", [])
