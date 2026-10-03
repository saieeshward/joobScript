# joobScript — Handoff Guide

**Repo:** https://github.com/saieeshward/joobScript (private — accept the invite first)

Every night it scrapes fresh job listings (LinkedIn, Indeed, Glassdoor), filters them to the roles and
countries you care about, then writes a **tailored one-page resume PDF for each job** using a local LLM.
Each morning you get a list of apply links, each paired with its own resume.

```
 personalize.py ─► user_config.py   (your roles, countries, keywords)
                        │
 run_daily.py ──► main.py ──────────► jobs.jsonl          scrape last-24h jobs, dedupe, filter, rank
              ├─► resort_jobs.py ───► jobs.jsonl          sort by priority + your preferred locations
              └─► Q-Rattor/pipeline.py                    per job: pick your best experience/projects,
                        │                                 Ollama (qwen2.5:7b) rewrites bullets, pdflatex → PDF
                        ▼
                  Q-Rattor/final_apply.json  +  Q-Rattor/output/*.pdf
```

---

## 1. Install (one command)

Clone, then run the setup script. It **checks your system and installs anything missing**: Homebrew/apt
packages, Python 3.10+, git, a virtualenv with all Python packages, headless Chromium, LaTeX + the exact
packages the resume template needs, Ollama + the `qwen2.5:7b` model (~4.7GB). Then it **verifies**
everything (launches Chromium, compiles a test PDF, pings the model) and walks you through picking your roles.

**macOS / Linux (Ubuntu/Debian)**
```bash
git clone https://github.com/saieeshward/joobScript.git
cd joobScript
chmod +x setup.sh && ./setup.sh
```

**Windows 10/11** (PowerShell)
```powershell
git clone https://github.com/saieeshward/joobScript.git   # no git yet? download the ZIP from GitHub instead
cd joobScript
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Low on disk / slow machine? Add `--no-llm` (mac/linux) or `-NoLLM` (Windows) to skip Ollama — resumes then
use your own pre-written bullets instead of LLM-tailored ones.

It's safe to re-run setup any time; finished steps are skipped.

---

## 2. Make it yours (do this before the first run)

| What | File | Notes |
|---|---|---|
| **Roles + countries** | `personalize.py` | Runs automatically at the end of setup. Type your roles, **or press Enter and give it your CV (PDF/txt)** — the local LLM suggests roles, priority keywords and your seniority. Re-run any time. |
| **Your resume** | `Q-Rattor/resume_data.yaml` | Created from the template. This is the source of truth — the LLM only rephrases what's here and is told never to invent facts. Put real metrics in `full_context`. |
| **Your summaries** | `Q-Rattor/job_profiles.yaml` | ⚠️ The `summary_focus` text is **mine** — rewrite it for you (it's used as your summary whenever the LLM is off). Also update `priority_experiences` / `priority_projects` to your own entry names. |
| **Header location** | `Q-Rattor/resume_template.tex.j2` | Change the hardcoded `Dublin, Ireland`. |

> Keep at least one `publications` and one `certifications` entry in `resume_data.yaml`, or delete those
> two sections from the template — an empty list makes pdflatex fail.

Advanced knobs (pages per query, max jobs per night, delays) live in `config.py`; anything in
`user_config.py` overrides it.

---

## 3. Run it

```bash
.venv/bin/python run_daily.py            # macOS / Linux
.venv\Scripts\python.exe run_daily.py    # Windows
```

Then open **`Q-Rattor/final_apply.json`** — one entry per job:

```json
{ "job_name": "Data Engineer", "company": "Acme", "apply_link": "https://...",
  "pdf_path": "output/01_data_engineer.pdf", "reused": false, "applied": false }
```

Open the link, attach the PDF, apply, and flip `"applied"` to `true` to keep track.
`reused: true` means a near-identical job already had a resume, so it shares that PDF.

Already-seen jobs are skipped automatically (`seen_jobs.db`, 30-day window), so every night only adds new ones.

---

## 4. Run it every night (optional)

**macOS / Linux** — `crontab -e`, add (10pm daily):
```
0 22 * * * cd /full/path/to/joobScript && .venv/bin/python run_daily.py >> logs/cron.log 2>&1
```
On macOS, keep Ollama running at login (`brew services start ollama`, or open the Ollama app).

**Windows** — in PowerShell:
```powershell
schtasks /Create /SC DAILY /ST 22:00 /TN joobScript /TR "cmd /c cd /d C:\full\path\to\joobScript && .venv\Scripts\python.exe run_daily.py >> logs\cron.log 2>&1"
```
The Ollama app starts with Windows by default.

The machine has to be awake at that time.

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| A site returns 0 jobs | Sites change their HTML. Update the CSS selectors in `scrapers/<site>.py` (inspect the live page in DevTools). |
| Getting blocked / CAPTCHAs | Lower `MAX_CONCURRENT_SCRAPERS`, raise the delays in `config.py`. Don't scrape more than once a day. |
| `Ollama failed ... using fallback` | Ollama isn't running — open the app or run `ollama serve`. Resumes still get built, just untailored. |
| `pdflatex failed` | Read `Q-Rattor/output/<job>.log`. Usually an unescaped special character or an empty section in `resume_data.yaml`. |
| Resumes skipped as "senior-level" | `pipeline.py` skips senior/lead roles on purpose. Remove that check in `main()` if you're senior. |
| Anything after an update | Re-run `setup.sh` / `setup.ps1`. |

Logs: `logs/run.log` (scraper) and `logs/daily.log` (whole pipeline).

---

## What's *not* in the repo

Personal data stays local and is gitignored: `resume_data.yaml`, `user_config.py`, `jobs.jsonl`,
`seen_jobs.db`, `final_apply.json`, generated PDFs, logs. You start clean.

Respect each site's terms of service. This only reads public job listings, once a day.
