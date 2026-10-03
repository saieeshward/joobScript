"""
JSONL (JSON Lines) storage — one job per line, append-only.
Fast to write, easy to parse, no dependencies beyond stdlib.
"""
from __future__ import annotations
import json, logging
from pathlib import Path
from scrapers.base import Job

log = logging.getLogger(__name__)


def load_seen_keys(filepath: str) -> set[str]:
    path = Path(filepath)
    if not path.exists():
        return set()
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                key = obj.get("apply_link", "").strip().lower().rstrip("/")
                if key:
                    seen.add(key)
            except json.JSONDecodeError:
                continue
    return seen


def append_jobs(jobs: list[Job], filepath: str) -> int:
    seen = load_seen_keys(filepath)
    written = 0
    with open(filepath, "a", encoding="utf-8") as f:
        for job in jobs:
            key = job.dedupe_key
            if key in seen:
                continue
            record = {
                "job_name":    job.job_name,
                "company":     job.company,
                "location":    job.location,
                "country":     job.country,
                "apply_link":  job.apply_link,
                "source_site": job.source_site,
                "scraped_on":  job.scraped_on,
                "description": job.description,
                "requirements":job.requirements,
                "priority":    job.priority,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            seen.add(key)
            written += 1
    if written:
        log.info("Appended %d new jobs to %s", written, filepath)
    else:
        log.info("No new jobs to append (all duplicates).")
    return written
