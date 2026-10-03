"""
Interactive personalisation — makes the whole pipeline yours from one CV.
    python personalize.py

Give it your CV (PDF/txt/md) and the local LLM (Ollama) will:
  1. build Q-Rattor/resume_data.yaml  — your master resume the pipeline tailors per job
  2. fill Q-Rattor/job_profiles.yaml  — your summaries + what to emphasise per job type
  3. suggest the roles to search for  — or type your own
Then you pick countries and seniority, and it writes user_config.py (overrides config.py).

No Ollama? You can still type roles; fill resume_data.yaml by hand from the template.
Re-run any time.
"""
from __future__ import annotations
import json, re, shutil, sys
from datetime import date
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
USER_CONFIG = ROOT / "user_config.py"
RESUME_YAML = ROOT / "Q-Rattor" / "resume_data.yaml"
RESUME_EXAMPLE = ROOT / "Q-Rattor" / "resume_data.example.yaml"
PROFILES_YAML = ROOT / "Q-Rattor" / "job_profiles.yaml"
PROFILES_EXAMPLE = ROOT / "Q-Rattor" / "job_profiles.example.yaml"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"

# label -> LinkedIn geoId, Indeed subdomain, Glassdoor code, location-priority terms
REGIONS = {
    "ireland":      dict(li="104738515", indeed="ie", glassdoor="ie", loc=["dublin", "cork", "galway", "limerick", "ireland"]),
    "uk":           dict(li="101165590", indeed="uk", glassdoor="uk", loc=["london", "manchester", "edinburgh", "united kingdom"]),
    "india":        dict(li="102713980", indeed="in", glassdoor="in", loc=["bengaluru", "bangalore", "hyderabad", "chennai", "pune", "india"]),
    "uae":          dict(li="104305776", indeed="ae", glassdoor="ae", loc=["dubai", "abu dhabi", "uae"]),
    "saudi_arabia": dict(li="101004422", indeed="sa", glassdoor=None, loc=["riyadh", "jeddah", "saudi"]),
    "us":           dict(li="103644278", indeed=None, glassdoor="us", loc=["united states"]),
    "europe":       dict(li="91000000",  indeed=None, glassdoor=None, loc=["germany", "netherlands", "france", "europe"]),
}

SENIOR_WORDS = ["senior", "Sr.", "Sr ", "staff", "principal", "lead", "manager",
                "director", "head of", "VP", "vice president"]
BASE_EXCLUDE = ["sales", "marketing", "recruiter", "HR"]


# ── Prompts ───────────────────────────────────────────────────────────────────
def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    ans = input(f"{prompt}{hint}: ").strip()
    return ans or default

def yes(prompt: str, default: bool = False) -> bool:
    ans = ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not ans else ans.startswith("y")

def split_list(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,\n;]", s) if x.strip()]


# ── Ollama ────────────────────────────────────────────────────────────────────
def ollama_up() -> bool:
    try:
        requests.get(OLLAMA_URL + "/api/tags", timeout=3).raise_for_status()
        return True
    except Exception:
        return False

def ollama_json(prompt: str, num_predict: int = 1500) -> dict | None:
    try:
        resp = requests.post(OLLAMA_URL + "/api/chat", json={
            "model": OLLAMA_MODEL, "stream": False, "format": "json",
            # Default context is 2048 tokens — far too small for a whole CV
            "options": {"temperature": 0.1, "num_ctx": 12288, "num_predict": num_predict},
            "messages": [{"role": "user", "content": prompt}],
        }, timeout=1800)
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])
    except Exception as exc:
        print(f"  LLM call failed ({exc}).")
        return None


# ── Files ─────────────────────────────────────────────────────────────────────
def read_cv(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_dump(yaml.safe_load(path.read_text(encoding="utf-8")), sort_keys=False)
    return path.read_text(encoding="utf-8", errors="ignore")

def load_resume() -> dict:
    if not RESUME_YAML.exists():
        return {}
    return yaml.safe_load(RESUME_YAML.read_text(encoding="utf-8")) or {}

def resume_is_filled() -> bool:
    return load_resume().get("personal", {}).get("name", "Your Name") != "Your Name"

def load_profiles() -> dict:
    if not PROFILES_YAML.exists():
        shutil.copy(PROFILES_EXAMPLE, PROFILES_YAML)
    return yaml.safe_load(PROFILES_YAML.read_text(encoding="utf-8"))

def profiles_are_placeholder() -> bool:
    return any(str(p.get("summary_focus", "")).startswith("PLACEHOLDER")
               for p in load_profiles()["profiles"].values())

def dump_yaml(path: Path, header: str, data: dict) -> None:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(header + body, encoding="utf-8")


# ── 1. CV → resume_data.yaml ──────────────────────────────────────────────────
RESUME_SCHEMA = """{
  "personal": {"name": "", "phone": "", "email": "", "location": "City, Country",
               "github": "full URL or empty", "linkedin": "full URL or empty", "portfolio": "full URL or empty"},
  "work_experience": [
    {"title": "", "company": "", "location": "", "dates": "Mon YYYY--Mon YYYY or Mon YYYY--Present",
     "end_date": "YYYY-MM (2099-01 if current)",
     "tech": ["every tool, language, framework, method used in this role"],
     "full_context": "3-5 sentences: everything the CV says about this role, keeping all numbers",
     "bullets": ["the CV's bullet points for this role, verbatim or lightly cleaned"]}
  ],
  "education": [{"degree": "", "institution": "", "dates": "YYYY--YYYY", "extra": "grade/thesis or empty"}],
  "projects": [
    {"name": "", "dates": "", "end_date": "YYYY-MM", "tech": [""],
     "full_context": "everything the CV says about the project", "desc": "one-line description"}
  ],
  "publications": ["full citation"],
  "certifications": ["name (year)"],
  "skills": ["individual tools/languages/frameworks only"]
}"""

def _url(u: str | None) -> str:
    u = (u or "").strip()
    return u if not u or u.startswith("http") else "https://" + u

def _display(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", u).rstrip("/")

def _dates(d) -> str:
    return re.sub(r"\s*(?:–|—|-{1,2})\s*", "--", str(d or "")).strip()

def _end(d) -> str:
    d = str(d or "")
    return "2099-01" if not d or "present" in d.lower() else d[:7]

def _strs(xs) -> list[str]:
    return [str(x).strip() for x in (xs or []) if str(x).strip()]

def _bullets(e: dict) -> list[str]:
    bullets = _strs(e.get("bullets"))
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(e.get("full_context", ""))) if x.strip()]
    return bullets if len(bullets) >= min(2, len(sentences)) else sentences[:4]

def normalize_resume(r: dict) -> dict:
    """Coerce LLM output into exactly the shape pipeline.py expects."""
    p = r.get("personal", {}) or {}
    github, linkedin = _url(p.get("github")), _url(p.get("linkedin"))
    personal = {
        "name": str(p.get("name", "")).strip(),
        "phone": str(p.get("phone", "")).strip(),
        "email": str(p.get("email", "")).strip(),
        "github": github, "github_display": _display(github),
        "linkedin": linkedin, "linkedin_display": _display(linkedin),
        "portfolio": _url(p.get("portfolio")) or linkedin or github,
        "location": str(p.get("location", "")).strip(),
    }
    work = [{
        "title": str(e.get("title", "")).strip(),
        "company": str(e.get("company", "")).strip(),
        "location": str(e.get("location", "")).strip(),
        "dates": _dates(e.get("dates")),
        "end_date": _end(e.get("end_date")),
        "tech": _strs(e.get("tech")),
        "full_context": str(e.get("full_context", "")).strip(),
        "bullets": _bullets(e),
    } for e in r.get("work_experience", []) or [] if e.get("title") and e.get("company")]
    projects = [{
        "name": str(x.get("name", "")).strip(),
        "dates": _dates(x.get("dates")),
        "end_date": _end(x.get("end_date")),
        "tech": _strs(x.get("tech")),
        "full_context": str(x.get("full_context", "")).strip(),
        "desc": str(x.get("desc", "")).strip(),
    } for x in r.get("projects", []) or [] if x.get("name")]
    education = []
    for ed in r.get("education", []) or []:
        item = {"degree": str(ed.get("degree", "")).strip(),
                "institution": str(ed.get("institution", "")).strip(),
                "dates": _dates(ed.get("dates"))}
        if str(ed.get("extra", "")).strip():
            item["extra"] = str(ed["extra"]).strip()
        if item["degree"]:
            education.append(item)
    return {
        "personal": personal,
        "work_experience": work,
        "education": education,
        "publications": _strs(r.get("publications")),
        "projects": projects,
        "certifications": _strs(r.get("certifications")),
        "skills": _strs(r.get("skills")),
    }

def build_resume_from_cv(cv_text: str) -> bool:
    print("  Extracting your CV into resume_data.yaml — a few minutes on a laptop without a GPU...")
    out = ollama_json(
        "Convert this CV into JSON with EXACTLY this structure:\n" + RESUME_SCHEMA + "\n\n"
        "Rules: copy facts only from the CV, never invent anything. Keep every number and metric. "
        "Include every job and every project. Use empty strings/lists for anything missing.\n\n"
        "CV:\n" + cv_text[:24000],
        num_predict=6000,
    )
    if not out:
        return False
    resume = normalize_resume(out)
    if not resume["personal"]["name"] or not (resume["work_experience"] or resume["projects"]):
        print("  The LLM's output was missing your name or experience — not saving it.")
        return False
    dump_yaml(RESUME_YAML,
              f"# resume_data.yaml — generated from your CV by personalize.py on {date.today()}.\n"
              "# CHECK IT: the AI can misread a PDF. Add real metrics to `full_context` —\n"
              "# the pipeline only rephrases what's here, it never invents facts.\n"
              "# Field guide: resume_data.example.yaml\n\n", resume)
    print(f"  ✓ Wrote {RESUME_YAML.relative_to(ROOT)} — {len(resume['work_experience'])} jobs, "
          f"{len(resume['projects'])} projects, {len(resume['skills'])} skills")
    return True


# ── 2. resume → job_profiles.yaml ────────────────────────────────────────────
def personalise_profiles(resume: dict) -> bool:
    data = load_profiles()
    profiles = data["profiles"]
    companies = {e["company"].lower(): e["company"] for e in resume.get("work_experience", [])}
    projects = {p["name"].lower(): p["name"] for p in resume.get("projects", [])}

    cv_summary = "\n".join(
        [f"JOB: {e['title']} at {e['company']} — {e.get('full_context', '')}" for e in resume.get("work_experience", [])] +
        [f"PROJECT: {p['name']} — {p.get('full_context', '')}" for p in resume.get("projects", [])] +
        ["SKILLS: " + ", ".join(resume.get("skills", []))] +
        ["EDUCATION: " + "; ".join(f"{e['degree']}, {e['institution']}" for e in resume.get("education", []))]
    )
    categories = "\n".join(f"- {name}: {', '.join(p.get('keywords', [])[:12]) or 'general / anything else'}"
                           for name, p in profiles.items())
    print("  Writing your job-profile summaries...")
    out = ollama_json(
        "Candidate:\n" + cv_summary[:12000] + "\n\n"
        "Job categories (name: typical keywords):\n" + categories + "\n\n"
        "For EVERY category return JSON:\n"
        '{"profiles": {"<category>": {"summary_focus": "2-3 sentence resume summary of THIS candidate angled at '
        'the category, no first-person pronouns, only true facts from above", '
        '"priority_experiences": ["company names from the JOB lines most relevant to the category"], '
        '"priority_projects": ["project names from the PROJECT lines most relevant to the category"]}}}\n'
        "Use names exactly as written above. Never invent facts.",
        num_predict=3000,
    )
    if not out or not isinstance(out.get("profiles"), dict):
        return False

    for name, prof in profiles.items():
        got = out["profiles"].get(name) or {}
        if str(got.get("summary_focus", "")).strip():
            prof["summary_focus"] = got["summary_focus"].strip()
        # Keep only names that really exist in the resume — the pipeline matches on them
        prof["priority_experiences"] = [companies[c.lower()] for c in _strs(got.get("priority_experiences")) if c.lower() in companies]
        prof["priority_projects"] = [projects[p.lower()] for p in _strs(got.get("priority_projects")) if p.lower() in projects]

    def usable(text) -> bool:
        t = str(text or "").strip()
        return bool(t) and not re.match(r"(?i)(n/?a|none|placeholder|no relevant)", t)
    general = next((p["summary_focus"] for p in [profiles.get("other", {})] + list(profiles.values())
                    if usable(p.get("summary_focus"))), "")
    for prof in profiles.values():
        if not usable(prof.get("summary_focus")) and general:
            prof["summary_focus"] = general

    header = PROFILES_EXAMPLE.read_text(encoding="utf-8").split("profiles:")[0]
    dump_yaml(PROFILES_YAML, header, data)
    print(f"  ✓ Wrote {PROFILES_YAML.relative_to(ROOT)}")
    return True


# ── 3. Roles ──────────────────────────────────────────────────────────────────
def suggest_roles(cv_text: str) -> dict | None:
    print("  Picking roles that fit your CV...")
    out = ollama_json(
        "Here is a candidate's CV:\n\n" + cv_text[:12000] + "\n\n"
        "Suggest what this person should search for on job boards. Return JSON:\n"
        '{"roles": ["6-10 specific, commonly used job titles they are a realistic fit for"],\n'
        ' "priority_keywords": ["8-15 skills/domains from the CV that make a job a strong match"],\n'
        ' "seniority": "entry" | "mid" | "senior"}\n'
        "Roles must be real job-board titles (e.g. 'Data Engineer'), not descriptions. "
        "Base everything strictly on the CV."
    )
    return out if out and out.get("roles") else None

def build_queries(roles: list[str], internships: bool) -> list[str]:
    # Group roles 4 per query — fewer queries = faster runs, less bot detection
    queries = [" OR ".join(f'"{r}"' for r in roles[i:i + 4]) for i in range(0, len(roles), 4)]
    if internships:
        core = " OR ".join(f'"{r}"' for r in roles[:6])
        queries.append(f'({core}) AND ("intern" OR "internship" OR "graduate")')
    return queries

def write_user_config(values: dict) -> None:
    lines = [
        "# Generated by personalize.py — re-run it to change, or edit by hand.",
        "# Anything set here overrides the same name in config.py.",
        "",
    ]
    for key, val in values.items():
        lines.append(f"{key} = {json.dumps(val, indent=4, ensure_ascii=False)}")
        lines.append("")
    USER_CONFIG.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    print("\n── joobScript: make it yours ──\n")
    if not RESUME_YAML.exists():
        shutil.copy(RESUME_EXAMPLE, RESUME_YAML)
    llm = ollama_up()
    if not llm:
        print("  (Ollama isn't running, so no CV reading — you'll type roles and fill\n"
              "   Q-Rattor/resume_data.yaml by hand. Start Ollama and re-run to automate it.)\n")

    # 0. CV
    cv_text = ""
    if llm:
        while True:
            cv_path = ask("Path to your CV — PDF, .txt or .md (drag the file in here), or Enter to skip").strip("\"' &")
            if not cv_path:
                break
            path = Path(cv_path).expanduser()
            if path.exists():
                cv_text = read_cv(path)
                if cv_text.strip():
                    break
                print("  Couldn't read any text from that file (scanned image PDF?). Try a .txt export.")
            else:
                print("  File not found — try again.")

    # 1. Resume
    if cv_text and (not resume_is_filled() or yes("resume_data.yaml already has details. Rebuild it from this CV?")):
        if not build_resume_from_cv(cv_text):
            print("  Couldn't build it automatically — fill Q-Rattor/resume_data.yaml by hand.")
    elif not resume_is_filled():
        print("  → Fill in Q-Rattor/resume_data.yaml by hand (it has a template in it).")

    # 2. Job profiles
    if llm and resume_is_filled() and (profiles_are_placeholder() or yes("Rewrite job-profile summaries from your resume?")):
        if not personalise_profiles(load_resume()):
            print("  Couldn't write them automatically — edit summary_focus in Q-Rattor/job_profiles.yaml.")
    elif profiles_are_placeholder():
        print("  → Replace the PLACEHOLDER summaries in Q-Rattor/job_profiles.yaml.")

    # 3. Roles — suggested from the CV/resume, or typed
    roles: list[str] = []
    priority: list[str] = []
    seniority = ""
    source = cv_text or (RESUME_YAML.read_text(encoding="utf-8") if resume_is_filled() else "")
    suggestion = suggest_roles(source) if llm and source else None
    if suggestion:
        print("\n  Suggested roles:    " + ", ".join(suggestion["roles"]))
        print("  Priority keywords:  " + ", ".join(suggestion.get("priority_keywords", [])))
        print("  Seniority guess:    " + suggestion.get("seniority", "?") + "\n")
        if yes("Use these?", default=True):
            roles = suggestion["roles"]
            priority = suggestion.get("priority_keywords", [])
            seniority = suggestion.get("seniority", "")
    while not roles:
        roles = split_list(ask("\nRoles you want to search for, comma-separated"))

    # 4. Internships / graduate roles
    internships = yes("Also search internships / graduate roles?", default=False)

    # 5. Countries
    labels = list(REGIONS)
    print("\nWhere do you want to work?")
    for i, label in enumerate(labels, 1):
        print(f"  {i}. {label.upper() if len(label) <= 3 else label.replace('_', ' ').title()}")
    picks = split_list(ask("Pick numbers, comma-separated", "1"))
    chosen = [labels[int(p) - 1] for p in picks if p.isdigit() and 1 <= int(p) <= len(labels)] or ["ireland"]

    # 6. Seniority → what gets filtered out
    levels = {"1": "entry", "2": "mid", "3": "senior"}
    default_level = {v: k for k, v in levels.items()}.get(seniority, "1")
    level = levels.get(ask("\nYour level: 1) entry/graduate  2) mid  3) senior", default_level), "entry")
    exclude = BASE_EXCLUDE + (SENIOR_WORDS if level == "entry" else
                              ["principal", "director", "head of", "VP", "vice president"] if level == "mid" else [])

    # 7. Priority keywords (★ + sorted to the top)
    if not priority:
        priority = split_list(ask("\nKeywords that make a job a top match (skills/domains), comma-separated",
                                  ", ".join(roles[:3])))
    if internships:
        priority += ["intern", "internship", "graduate"]

    values = {
        "SEARCH_QUERIES":      build_queries(roles, internships),
        "LINKEDIN_GEOS":       {c: REGIONS[c]["li"] for c in chosen},
        "INDEED_COUNTRIES":    [REGIONS[c]["indeed"] for c in chosen if REGIONS[c]["indeed"]],
        "GLASSDOOR_COUNTRIES": [REGIONS[c]["glassdoor"] for c in chosen if REGIONS[c]["glassdoor"]],
        "LOCATION_PRIORITY":   [t for c in chosen for t in REGIONS[c]["loc"]],
        "PRIORITY_KEYWORDS":   priority,
        "EXCLUDE_KEYWORDS":    exclude,
    }
    write_user_config(values)

    print(f"\nSaved to {USER_CONFIG.name}:")
    print("  Queries:   " + "\n             ".join(values["SEARCH_QUERIES"]))
    print("  Regions:   " + ", ".join(chosen))
    print("  Excluding: " + ", ".join(exclude))
    if level != "entry":
        print("\n  Note: Q-Rattor/pipeline.py skips resumes for senior/lead roles — remove that check"
              "\n  in main() (search 'Skipping (senior-level)') if you want them.")
    print("\nDone. Look over Q-Rattor/resume_data.yaml, then run run_daily.py.\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nCancelled.")
