# joobScript

Finds fresh graduate/junior AI and software jobs in Ireland, screens out the ones you shouldn't apply to, and writes a
**one-page tailored resume PDF plus an apply link** for each of the rest. Run it by hand or let it run every morning.

> **Setup and daily workflow: [HANDOFF.md](HANDOFF.md)** (one command on macOS, Linux, Windows).

```
 sources ──► main.py ──► jobs.jsonl ──► Q-Rattor/pipeline.py ──► Q-Rattor/final_apply.json + output/*.pdf
 LinkedIn (public HTTP)      screen: no senior / intern / 3+ yrs / non-Irish without sponsorship / non-AI roles
 Company boards (Greenhouse, Ashby, Lever)       tailor: Claude Code headless → Ollama → your approved wording
 Indeed (listings only)                           check: no invented numbers · render: exactly one page
```

## Run it

```bash
.venv/bin/python run_nightly.py            # scrape + resumes + Q-Rattor/today.md (what to apply to this morning)
.venv/bin/python run_nightly.py --dry-run  # screen already-scraped jobs, no model, no PDFs
.venv/bin/python main.py                   # scrape only
cd Q-Rattor && ../.venv/bin/python pipeline.py   # resumes only
```

Every morning at 06:00 on macOS: `./scripts/install_launchd.sh` (remove with `--uninstall`). Settings for the unattended run
(which LLM, usage caps, resumes per night) live in `nightly.env`, copy it from `nightly.env.example`.

## Your data (never committed)

`Q-Rattor/resume_data.yaml` is your master resume, `Q-Rattor/job_profiles.yaml` your job categories, `user_config.py` your
search overrides. Copy the `*.example.yaml` files and read the comments in them: write one-line, plain-English bullets with
real numbers, because the pipeline will not let the model print a number that is not in this file.

## Sources

| Source | How | Notes |
|---|---|---|
| LinkedIn | public logged-out HTTP endpoints (browser fallback) | about 10 s per query, full descriptions |
| Company boards | Greenhouse / Ashby / Lever public JSON APIs | full descriptions; add companies in `config.COMPANY_BOARDS` |
| Indeed | headless browser | listings only: job pages are bot-blocked, so no descriptions |
| Glassdoor | headless browser | **off by default**: human-check wall, wrong region |

Only public pages and documented public APIs are read, once a day. Respect each site's terms.

## Tests

```bash
.venv/bin/python -m pytest -q
```

CI runs the same suite on every pull request (`.github/workflows/tests.yml`).
