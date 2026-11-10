"""
Writes Q-Rattor/jobs_to_apply.csv: one row per job with its resume, ready to open in Excel / Numbers / Sheets.

    python export.py            (also called at the end of pipeline.py and run_nightly.py)

Columns you fill in (status, notes, applied) are kept when the file is regenerated, matched on the apply link.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys

import guards

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL_JSON = os.path.join(HERE, "final_apply.json")
JOBS_JSONL = os.path.join(HERE, "..", "jobs.jsonl")
RESUME_DATA = os.path.join(HERE, "resume_data.yaml")
OUT_CSV = os.path.join(HERE, "jobs_to_apply.csv")

COLUMNS = [
    "status", "applied", "notes",                                   # yours, kept between runs
    "job_title", "company", "location", "source", "scraped_on", "apply_link",
    "resume_pdf", "resume_link",
    "description",
    "ats_match_pct", "job_keywords_missing_from_resume", "job_keywords_found_in_resume",
    "years_required", "sponsorship", "salary_mentioned",
    "job_type", "resume_layout", "fit_score", "resume_written_by", "resume_date",
]

# Words an applicant-tracking system is likely to match literally. Kept short and tech-focused.
KEYWORDS = [
    "python", "pytorch", "tensorflow", "keras", "scikit-learn", "pandas", "numpy", "sql", "nosql", "postgres",
    "mongodb", "docker", "kubernetes", "aws", "azure", "gcp", "terraform", "ci/cd", "git", "linux", "rest",
    "api", "fastapi", "flask", "django", "react", "typescript", "javascript", "node", "java", "go", "rust", "c++",
    "llm", "llms", "rag", "nlp", "computer vision", "machine learning", "deep learning", "generative ai", "genai",
    "prompt engineering", "fine-tuning", "agents", "mlops", "spark", "kafka", "airflow", "dbt", "tableau",
    "power bi", "langchain", "vector database", "embeddings", "evaluation", "data pipelines", "etl",
]


def _has(term: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#])", text) is not None


def _pdf_text(path: str) -> str:
    try:
        return subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True, timeout=30).stdout.lower()
    except Exception:
        return ""


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _jobs_by_link() -> dict:
    out = {}
    try:
        with open(JOBS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    j = json.loads(line)
                    out[j.get("apply_link", "")] = j
    except FileNotFoundError:
        pass
    return out


def _sponsorship(job: dict, text: str) -> str:
    if guards.is_ireland(job):
        return "n/a (Ireland, Stamp 1G)"
    if guards.SPONSOR_NEG.search(text):
        return "no"
    return "yes/relocation mentioned" if guards.SPONSOR_POS.search(text) else "not mentioned"


def _salary(text: str) -> str:
    m = re.findall(r"(?:€|EUR\s?)\s?\d[\d,\.]*\s?[kK]?(?:\s?(?:-|to|–)\s?(?:€|EUR\s?)?\s?\d[\d,\.]*\s?[kK]?)?", text)
    return "; ".join(dict.fromkeys(s.strip() for s in m))[:120]


def build_rows() -> list[dict]:
    final = _load(FINAL_JSON, [])
    jobs = _jobs_by_link()
    rows = []
    for e in final:
        job = jobs.get(e["apply_link"], {})
        description = (job.get("description") or "").strip()
        text = (e["job_name"] + "\n" + description).lower()
        pdf_rel = e.get("pdf_path", "")
        pdf_abs = os.path.abspath(os.path.join(HERE, pdf_rel)) if pdf_rel else ""
        resume_text = _pdf_text(pdf_abs) if pdf_abs and os.path.exists(pdf_abs) else ""
        wanted = [k for k in KEYWORDS if _has(k, text)]
        found = [k for k in wanted if _has(k, resume_text)]
        missing = [k for k in wanted if k not in found]
        yrs = guards.required_years(e["job_name"] + "\n" + description)
        rows.append({
            "status": "to apply", "applied": "yes" if e.get("applied") else "no", "notes": "",
            "job_title": e["job_name"], "company": e.get("company", "").split("\n")[0].strip(),
            "location": e.get("location", ""), "source": job.get("source_site", ""),
            "scraped_on": job.get("scraped_on", ""), "apply_link": e["apply_link"],
            "resume_pdf": pdf_abs, "resume_link": ("file://" + pdf_abs.replace(" ", "%20")) if pdf_abs else "",
            "description": description,
            "ats_match_pct": round(100 * len(found) / len(wanted)) if wanted else "",
            "job_keywords_missing_from_resume": ", ".join(missing),
            "job_keywords_found_in_resume": ", ".join(found),
            "years_required": yrs if yrs is not None else "",
            "sponsorship": _sponsorship(job or {"location": e.get("location", "")}, text),
            "salary_mentioned": _salary(description),
            "job_type": e.get("job_type", ""), "resume_layout": e.get("layout", ""),
            "fit_score": e.get("fit_score", ""), "resume_written_by": e.get("generator", ""),
            "resume_date": e.get("generated_on", ""),
        })
    return rows


def write_csv(path: str = OUT_CSV) -> int:
    rows = build_rows()
    kept = {}
    if os.path.exists(path):                                     # keep what you typed
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                kept[r.get("apply_link", "")] = {k: r.get(k, "") for k in ("status", "applied", "notes")}
    for r in rows:
        if r["apply_link"] in kept:
            r.update({k: v for k, v in kept[r["apply_link"]].items() if v})
    rows.sort(key=lambda r: (r["resume_date"], r["fit_score"] or 0), reverse=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:         # BOM so Excel reads UTF-8 correctly
        w = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    n = write_csv()
    print("Wrote %d row(s) to %s" % (n, os.path.relpath(OUT_CSV)))
    sys.exit(0)
