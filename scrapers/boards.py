"""
Company job boards (Greenhouse, Ashby, Lever) over their public, documented JSON APIs.

Unlike Indeed/Glassdoor these need no browser and sit behind no bot check, and they return the full job
description, which the resume pipeline needs (required years, sponsorship, keywords).

Add companies in config.COMPANY_BOARDS. A token is the slug in the company's careers URL, e.g.
  boards.greenhouse.io/<token>    jobs.ashbyhq.com/<token>    jobs.lever.co/<token>
Only jobs located in Ireland whose title looks like AI/ML/data/software are fetched in full.
"""
from __future__ import annotations

import asyncio, html, logging, random, re, time

import requests

import config
from job_filters import INTERN_TITLE, IRELAND_TERMS, title_wanted
from storage.seen import filter_new, mark_seen_bulk
from .base import BaseScraper, Job

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (personal job search; contact via GitHub saieeshward)"}


def _html_to_text(fragment: str) -> str:
    t = html.unescape(fragment or "")                      # Greenhouse double-encodes its HTML
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(p|ul|ol|div|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
    t = re.sub(r"</li>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", t)).strip()


def _in_ireland(location: str) -> bool:
    loc = (location or "").lower()
    return any(t in loc for t in IRELAND_TERMS)


class BoardsScraper(BaseScraper):
    site_key = "boards"

    async def async_scrape(self, queries: list[str]) -> list[Job]:
        return await asyncio.to_thread(self._scrape_all)

    # ── driver ────────────────────────────────────────────────────────────────────────────────────────
    def _scrape_all(self) -> list[Job]:
        jobs: list[Job] = []
        for kind, tokens in getattr(config, "COMPANY_BOARDS", {}).items():
            fetch = {"greenhouse": self._greenhouse, "ashby": self._ashby, "lever": self._lever}.get(kind)
            if not fetch:
                log.warning("boards: unknown board type %r", kind)
                continue
            for token in tokens:
                try:
                    found = fetch(token)
                    if found:
                        log.info("boards: %s/%s -> %d new Irish job(s)", kind, token, len(found))
                    jobs.extend(found)
                except Exception as exc:          # one broken board must not stop the others
                    log.warning("boards: %s/%s failed: %s", kind, token, exc)
                time.sleep(random.uniform(0.2, 0.5))
        return jobs

    # ── shared filtering ──────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _wanted(title: str) -> bool:
        excl = [k.lower() for k in config.EXCLUDE_KEYWORDS]
        tl = title.lower()
        if any(k in tl for k in excl):
            return False
        if config.SKIP_INTERNSHIPS and INTERN_TITLE.search(title):
            return False
        return title_wanted(title, config.EXTRA_TITLE_KEYWORDS)

    def _new_only(self, cards: list[dict]) -> list[dict]:
        """Drop jobs already seen (DB) and remember every Irish job we looked at."""
        if not cards:
            return []
        new, _ratio = filter_new(cards)
        mark_seen_bulk([c["job_id"] for c in cards])
        return [c for c in new if self._wanted(c["title"])]

    # ── Greenhouse ────────────────────────────────────────────────────────────────────────────────────
    def _greenhouse(self, token: str) -> list[Job]:
        base = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        r = requests.get(base, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        cards = []
        for j in r.json().get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            if _in_ireland(loc):
                cards.append({"job_id": f"gh_{token}_{j['id']}", "title": j["title"], "location": loc,
                              "href": j["absolute_url"], "company": j.get("company_name") or token, "id": j["id"]})
        out = []
        for c in self._new_only(cards):
            try:
                d = requests.get(f"{base}/{c['id']}", headers=_HEADERS, timeout=30).json()
                desc = _html_to_text(d.get("content", ""))
            except Exception:
                desc = ""
            out.append(self._job(c, desc))
            time.sleep(random.uniform(0.1, 0.3))
        return out

    # ── Ashby ─────────────────────────────────────────────────────────────────────────────────────────
    def _ashby(self, token: str) -> list[Job]:
        r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", headers=_HEADERS, timeout=30)
        r.raise_for_status()
        cards = []
        for j in r.json().get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j.get("location", "")] + [s.get("location", "") if isinstance(s, dict) else str(s)
                                              for s in (j.get("secondaryLocations") or [])]
            loc = next((l for l in locs if _in_ireland(l)), "")
            if loc:
                cards.append({"job_id": f"ash_{token}_{j['id']}", "title": j["title"], "location": loc,
                              "href": j.get("jobUrl") or j.get("applyUrl", ""), "company": token.title(),
                              "desc": j.get("descriptionPlain") or _html_to_text(j.get("descriptionHtml", ""))})
        return [self._job(c, c["desc"]) for c in self._new_only(cards)]

    # ── Lever ─────────────────────────────────────────────────────────────────────────────────────────
    def _lever(self, token: str) -> list[Job]:
        r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json", headers=_HEADERS, timeout=30)
        r.raise_for_status()
        cards = []
        for j in r.json():
            loc = (j.get("categories") or {}).get("location", "")
            if _in_ireland(loc):
                cards.append({"job_id": f"lev_{token}_{j['id']}", "title": j["text"], "location": loc,
                              "href": j.get("hostedUrl", ""), "company": token.title(),
                              "desc": j.get("descriptionPlain", "")})
        return [self._job(c, c["desc"]) for c in self._new_only(cards)]

    def _job(self, card: dict, description: str) -> Job:
        return Job(job_name=card["title"], company=card["company"], location=card["location"],
                   apply_link=card["href"], description=description, requirements=[],
                   source_site=f"boards:{card['job_id'].split('_')[0]}", country="Ireland")
