"""Re-sorts jobs.jsonl by location priority defined in config."""
import json
from pathlib import Path
import config

_loc_ranks = [p.lower() for p in config.LOCATION_PRIORITY]

def _location_rank(obj: dict) -> int:
    haystack = (obj.get("location", "") + " " + obj.get("country", "")).lower()
    for i, term in enumerate(_loc_ranks):
        if term in haystack:
            return i
    return len(_loc_ranks)

path = Path(config.OUTPUT_FILE)
jobs = []
with open(path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            jobs.append(json.loads(line))

jobs.sort(key=lambda j: (
    j.get("scraped_on", "")[:10],          # date ASC — newest run at bottom
    not j.get("priority", False),           # priority jobs first within each day
    _location_rank(j),                      # then by location
))

with open(path, "w", encoding="utf-8") as f:
    for job in jobs:
        f.write(json.dumps(job, ensure_ascii=False) + "\n")

print(f"Re-sorted {len(jobs)} jobs in {path}")
