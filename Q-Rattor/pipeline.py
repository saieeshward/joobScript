"""
Resume Pipeline (v2)

For every unprocessed job in jobs.jsonl (newest first):
  1. Screen it (no LLM): skip senior titles, internships, 3+ years required, non-Ireland jobs that
     don't offer sponsorship/relocation, and jobs that aren't AI/software roles.
  2. Classify it, pick the relevant experiences/projects (a part-time job that isn't relevant moves to
     a one-line "Additional" entry instead of a full job).
  3. Ask the LLM (Claude Code headless first, Ollama second, curated bullets last) to rewrite the
     wording only. Every number it writes must exist in resume_data.yaml, otherwise it retries once and
     then falls back to the curated bullets.
  4. Render LaTeX -> PDF and shrink step by step until it is exactly ONE page.

Tuning knobs are environment variables (see llm.py) and the constants below.
"""

import os, re, json, subprocess, yaml
from datetime import datetime, date, timedelta

import guards
import llm

# ── Config ────────────────────────────────────────────────────────────────────
JOBS_PER_SESSION  = int(os.environ.get("JOOB_JOB_LIMIT", "40"))   # resumes written per run
MAX_AGE_DAYS      = int(os.environ.get("JOOB_MAX_AGE_DAYS", "7"))   # ignore jobs scraped longer ago than this
SKIP_INTERNSHIPS  = os.environ.get("JOOB_SKIP_INTERNSHIPS", "1") != "0"
REUSE_THRESHOLD   = 0.95
INPUT_JSONL       = "../jobs.jsonl"
OUTPUT_DIR        = "output"
RESUME_DATA_FILE  = "resume_data.yaml"
JOB_PROFILES_FILE = "job_profiles.yaml"
TEMPLATE_FILE     = "resume_template.tex.j2"
CACHE_FILE        = os.path.join(OUTPUT_DIR, "resume_cache.json")
SKIPPED_FILE      = os.path.join(OUTPUT_DIR, "skipped.json")
FINAL_JSON        = "final_apply.json"
DRY_RUN          = os.environ.get("JOOB_DRY_RUN", "0") == "1"   # screen only: no LLM, no files written
FOOD_WORDS        = ["food", "restaurant", "hospitality", "culinary", "kitchen", "catering", "agritech"]

# Shrink ladder: tried in order until the PDF is one page.
LADDER = [
    dict(n_exp=5, bullets=[4, 2, 2, 2, 2], n_proj=4, pubs=True,  certs=False,  additional=True),
    dict(n_exp=5, bullets=[4, 2, 2, 2, 2], n_proj=4, pubs=True,  certs=False,  additional=False),
    dict(n_exp=5, bullets=[4, 2, 2, 2, 2], n_proj=3, pubs=True,  certs=False,  additional=False),
    dict(n_exp=5, bullets=[3, 2, 2, 2, 1], n_proj=3, pubs=True,  certs=False, additional=False),
    dict(n_exp=4, bullets=[3, 2, 2, 1],    n_proj=3, pubs=False, certs=False, additional=False),
    dict(n_exp=4, bullets=[3, 1, 1, 1],    n_proj=2, pubs=False, certs=False, additional=False),
    dict(n_exp=3, bullets=[3, 1, 1],       n_proj=2, pubs=False, certs=False, additional=False),
]


# ── Loaders ───────────────────────────────────────────────────────────────────
def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def read_jobs(path, skip_links):
    """All jobs not yet processed or skipped, newest scrape first, priority first within a day."""
    jobs, seen = [], set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            job = json.loads(line)
            link = job.get("apply_link", "")
            if not link or link in skip_links or link in seen:
                continue
            if MAX_AGE_DAYS and job.get("scraped_on", "9999") < (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat():
                continue                      # stale: the posting is probably closed
            seen.add(link)
            jobs.append(job)
    jobs.sort(key=lambda j: (j.get("scraped_on", ""), bool(j.get("priority"))), reverse=True)
    return jobs

def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Job text ──────────────────────────────────────────────────────────────────
def job_raw_text(job):
    reqs = job.get("requirements", [])
    if isinstance(reqs, list):
        reqs = " ".join(reqs)
    return "\n".join(filter(None, [job.get("job_name", ""), job.get("description", ""), reqs]))


# ── Classification ────────────────────────────────────────────────────────────
def classify_job(job, profiles):
    text = job_raw_text(job).lower()
    scores = {}
    for pname, pdata in profiles.items():
        if pname == "other":
            continue
        scores[pname] = sum(1 for kw in pdata.get("keywords", []) if kw.lower() in text)
    total = sum(scores.values()) or 1
    blend = {k: round(v / total, 3) for k, v in scores.items()}
    blend["other"] = 0.0
    primary = max(scores, key=scores.get) if any(v > 0 for v in scores.values()) else "other"
    if scores.get(primary, 0) == 0:
        blend["other"] = 1.0
    return primary, blend, sum(scores.values())


# ── Scoring & selection ───────────────────────────────────────────────────────
_STOP = {"and","the","for","with","in","of","to","a","an","is","are","as","our","you","we","will","be",
         "by","on","or","at","this","that","have","has","their","its","role","job","team","required","years"}

def score_entry(entry, job_text_lower, profile_data, job_words):
    score = 100 if entry.get("always_include") else 0
    for t in entry.get("tech", []):
        if t.lower() in job_text_lower:
            score += 3
    context = (entry.get("full_context", "") + " " + entry.get("desc", "")).lower()
    score += sum(1 for kw in job_words if kw in context)
    priority = [p.lower() for p in profile_data.get("priority_projects", []) +
                profile_data.get("priority_experiences", [])]
    entry_id = (entry.get("name", "") + entry.get("title", "") + entry.get("company", "")).lower()
    if any(p in entry_id for p in priority):
        score += 5
    return score

def select_sections(resume_data, job, primary_type, profiles):
    """Returns (experiences, projects, additional_lines). Experiences are visible ones, newest first."""
    text = job_raw_text(job).lower()
    profile = profiles.get(primary_type, profiles.get("other", {}))
    words = set(re.findall(r"\b[a-z][a-z+#./-]{2,}\b", text)) - _STOP

    visible, additional = [], []
    for e in resume_data["work_experience"]:
        kws = [k.lower() for k in e.get("show_when_keywords", [])]
        if kws and not any(k in text for k in kws):
            if e.get("additional_line"):
                additional.append(e["additional_line"])
        else:
            visible.append(e)

    def key(e):
        return score_entry(e, text, profile, words)

    # rank by relevance (used when the page must shrink), present newest first
    ranked = sorted(visible, key=key, reverse=True)
    exps = sorted(ranked, key=lambda e: str(e.get("end_date", "2025")), reverse=True)
    for i, e in enumerate(exps):
        e["_rank"] = ranked.index(e)

    projs = sorted(resume_data["projects"], key=key, reverse=True)
    for i, p in enumerate(projs):
        p["_rank"] = i
    return exps, projs, additional


# ── Fingerprint & reuse ───────────────────────────────────────────────────────
def make_fingerprint(job, primary_type, blend, exps, projs, layout):
    text = job_raw_text(job)
    tech = set(m.lower() for m in re.findall(
        r"\b(Python|Java|C\+\+|C#|JavaScript|TypeScript|Rust|Go|React|AWS|GCP|Azure|Docker|Kubernetes|SQL|"
        r"FastAPI|Django|Flask|PyTorch|TensorFlow|LLM|RAG|NLP|LangChain|Spark|Kafka)\b", text, re.I))
    return {"job_type": primary_type, "layout": layout, "type_blend": blend, "tech_set": sorted(tech),
            "exp_ids": sorted(e["title"] + "@" + e["company"] for e in exps),
            "proj_ids": sorted(p["name"] for p in projs[:4])}

def similarity(a, b):
    if a["job_type"] != b["job_type"] or a["layout"] != b["layout"]:
        return 0.0
    s1, s2 = set(a["tech_set"]), set(b["tech_set"])
    tech = len(s1 & s2) / max(len(s1 | s2), 1)
    p1, p2 = set(a["proj_ids"]), set(b["proj_ids"])
    proj = len(p1 & p2) / max(len(p1 | p2), 1)
    return 0.6 * tech + 0.4 * proj if (s1 or s2) else 0.0

def find_reusable(fp, cache):
    best, entry = 0.0, None
    for c in cache:
        s = similarity(fp, c["fingerprint"])
        if s > best:
            best, entry = s, c
    return (entry, best) if best >= REUSE_THRESHOLD else (None, best)


# ── LLM prompt ────────────────────────────────────────────────────────────────
def build_system_prompt(resume_data):
    exp = []
    for e in resume_data["work_experience"]:
        exp.append("- %s at %s (%s)\n  Tech: %s\n  Facts: %s" % (
            e["title"], e["company"], e["dates"], ", ".join(e.get("tech", [])),
            " ".join(e.get("full_context", "").split())))
    proj = []
    for p in resume_data["projects"]:
        proj.append("- %s (%s)\n  Tech: %s\n  Facts: %s" % (
            p["name"], p["dates"], ", ".join(p.get("tech", [])), " ".join(p.get("full_context", "").split())))
    edu = ["- %s, %s (%s). %s" % (e["degree"], e["institution"], e["dates"], e.get("extra", ""))
           for e in resume_data["education"]]
    return (
        "You are an expert resume writer for early-career AI/software engineers hiring in Ireland. "
        "You only REWRITE facts the candidate already has; you never add facts.\n\n"
        "CANDIDATE: " + resume_data["personal"]["name"] + "\n\nEXPERIENCE:\n" + "\n".join(exp) +
        "\n\nPROJECTS:\n" + "\n".join(proj) + "\n\nEDUCATION:\n" + "\n".join(edu) +
        "\n\nPUBLICATIONS: " + " | ".join(resume_data.get("publications", [])) +
        "\n\nALLOWED SKILLS: " + ", ".join(sorted(guards.skill_vocabulary(resume_data).values())) +
        "\n\nReturn ONLY a JSON object, no commentary."
    )

def build_user_prompt(job, primary_type, profile, exps, projs_sel, bullet_counts, base, feedback=""):
    roles = "\n".join("- %s at %s: write %d bullets" % (e["title"], e["company"], n)
                      for e, n in zip(exps, bullet_counts))
    approved = "\n".join("- %s: %s" % (e["company"], " | ".join(e.get("bullets", []))) for e in exps)
    pnames = "\n".join("- %s: %s" % (p["name"], p.get("desc", "")) for p in projs_sel)
    return (
        "JOB: " + job.get("job_name", "") + " at " + job.get("company", "") + "\n"
        "JOB TEXT:\n" + job_raw_text(job)[:3000] + "\n\n"
        "Job type: " + primary_type + "\nProfile hint: " + profile.get("summary_focus", "").strip() + "\n\n"
        "WORK EXPERIENCE to write (this order):\n" + roles + "\n\nAPPROVED WORDING per role (pick the most relevant, "
        "lightly reword for this job):\n" + approved + "\n\nPROJECTS (approved one-liners):\n" + pnames +
        "\n\nBASE SUMMARY (adapt lightly, keep every fact):\n" + base + "\n\n"
        'Return JSON: {"summary": "...", "skills": ["..."], '
        '"work_experience": [{"company": "...", "bullets": ["..."]}], '
        '"projects": [{"name": "...", "desc": "..."}]}\n\n'
        "RULES (this resume is read by recruiters who are not engineers, then by engineers):\n"
        "- Plain English. Each bullet = what was built or done, for whom, and the result (action then result). "
        "Max 20 words, one line if possible. Start with a strong verb.\n"
        "- No internal jargon or process words: no ADR, schema, bench, gates, pipeline stages, licences, ticket "
        "numbers. Keep at most one or two well-known keywords per bullet (LLM, RAG, Python, Rust) and say what they do "
        "in a few words when it is not obvious.\n"
        "- summary: max 40 words, no first person, no job title or company name; start from the BASE SUMMARY and "
        "emphasise what this job needs.\n"
        "- skills: 14-18 items ordered by relevance to the job, chosen ONLY from ALLOWED SKILLS.\n"
        "- projects: one line, max 22 words, plain English.\n"
        "- Use the job's own keywords only where the candidate genuinely has the skill.\n"
        "- Every number, percentage, count and metric MUST appear in the candidate facts or approved wording. "
        "Never invent numbers, tools, employers or outcomes.\n"
        "- Keep shared-credit wording (proposed, co-built, co-wrote, helped, with the team). Never upgrade shared work "
        "to sole ownership, never present a list-price comparison as a measured saving, say 'AI judges' not "
        "'users' for model-rated results, and a patent is an APPLICATION that was filed.\n"
        "- Facts from one role must not leak into another role.\n"
        + ("\nFIX THIS from your previous attempt: " + feedback + "\n" if feedback else "")
    )


def check_sections(llm_out, resume_data, exps, projs_sel, allowed):
    """Return (clean_dict_or_None, violations). Only returns data built from the selected sections."""
    texts = [str(llm_out.get("summary", ""))]
    by_company = {}
    for w in llm_out.get("work_experience", []) or []:
        by_company[str(w.get("company", "")).strip().lower()] = [str(b) for b in w.get("bullets", [])]
        texts += by_company[str(w.get("company", "")).strip().lower()]
    by_proj = {}
    for p in llm_out.get("projects", []) or []:
        by_proj[str(p.get("name", "")).strip().lower()] = str(p.get("desc", ""))
        texts.append(by_proj[str(p.get("name", "")).strip().lower()])
    bad = guards.number_violations(texts, allowed)
    return {"by_company": by_company, "by_proj": by_proj}, bad


def pick_skills(llm_skills, resume_data, job_text_lower):
    vocab = guards.skill_vocabulary(resume_data)
    groups = resume_data.get("skill_groups")
    if groups:   # only approved, grouped skills (keeps out jargon and claims nobody vouched for)
        approved = {s.lower(): s for items in groups.values() for s in items}
        vocab = {k: v for k, v in vocab.items() if k in approved}
    picked, seen = [], set()
    for s in llm_skills or []:
        canon = vocab.get(str(s).strip().lower())
        if canon and canon.lower() not in seen:
            picked.append(canon)
            seen.add(canon.lower())
    for s in resume_data.get("core_skills", []):      # always show the skills your current job uses
        canon = vocab.get(s.lower())
        if canon and canon.lower() not in seen:
            picked.append(canon)
            seen.add(canon.lower())
    # pad from the master list so ATS always has plenty to match
    master = [s for s in resume_data.get("skills", []) if not groups or s.lower() in vocab]
    master = sorted(master, key=lambda s: s.lower() in job_text_lower, reverse=True)
    for s in master:
        if len(picked) >= 16:
            break
        if s.lower() not in seen:
            picked.append(s)
            seen.add(s.lower())
    return picked[:20]


def build_content(resume_data, profile, exps, projs_sel, additional, layout, checked, llm_summary, llm_skills, job_text_lower):
    experiences = []
    for e in exps:
        bullets = checked["by_company"].get(e["company"].strip().lower()) if checked else None
        experiences.append({**e, "bullets": [b for b in (bullets or e.get("bullets", [])) if b.strip()]})
    projects = []
    for p in projs_sel:
        desc = checked["by_proj"].get(p["name"].strip().lower()) if checked else None
        projects.append({**p, "desc": desc or p.get("desc", "")})
    headline = profile.get("headline") or resume_data["personal"].get("headline", "")
    return {
        "headline": headline,
        "summary": llm_summary or " ".join(profile.get("summary_focus", "").split()),
        "skills": pick_skills(llm_skills, resume_data, job_text_lower),
        "experiences": experiences, "projects": projects, "additional": additional, "layout": layout,
    }


# ── LaTeX helpers ─────────────────────────────────────────────────────────────
_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_UNI = {"→": "->", "←": "<-", "≥": ">=", "≤": "<=", "×": "x", "•": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
        "\u00a0": " ", "…": "..."}

def esc(text):
    if not text:
        return ""
    s = re.sub(r"\\([&%$#_])", r"\1", str(text))   # tolerate already-escaped input
    for k, v in _UNI.items():
        s = s.replace(k, v)
    s = "".join(c for c in s if ord(c) < 0x2100 or c in "–—")   # drop emoji & exotic symbols
    return "".join(_TEX.get(c, c) for c in s)

def city(loc):
    return (loc or "").split(",")[0].strip()

def link(url, shown=None):
    shown = shown or re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return "\\href{" + url + "}{" + esc(shown) + "}"

def exp_tex(experiences, bullet_counts):
    blocks = []
    for e, n in zip(experiences, bullet_counts):
        bullets = "\n".join("  \\item " + esc(b) for b in e["bullets"][:n])
        where = ", " + esc(city(e.get("location", ""))) if e.get("location") else ""
        blocks.append(
            "\\noindent\\textbf{" + esc(e["title"]) + "} \\textit{" + esc(e["company"]) + where + "}"
            " \\hfill \\mbox{" + esc(e.get("dates", "")) + "}\\par\\vspace{-1pt}\n"
            "\\begin{itemize}\n" + bullets + "\n\\end{itemize}\n\\vspace{3pt}\n")
    return "\n".join(blocks)

def projects_tex(projects):
    blocks = []
    for p in projects:
        tech = ", ".join((p.get("tech_short") or p.get("tech", []))[:4])
        url = ("\\hfill " + link(p["link"], p.get("link_display"))) if p.get("link") else ""
        blocks.append(
            "\\noindent\\textbf{" + esc(p["name"]) + "}"
            + (" \\textit{(" + esc(tech) + ")}" if tech else "")
            + " \\hfill \\mbox{" + esc(p.get("dates", "")) + "}\\par\\vspace{-1pt}\n"
            "\\noindent " + esc(p.get("desc", "")) + (" " + link(p["link"], p.get("link_display")) if p.get("link") else "")
            + "\\par\\vspace{3pt}\n")
    return "\n".join(blocks)

def education_tex(education):
    blocks = []
    for ed in education:
        b = ("\\noindent\\textbf{" + esc(ed["degree"]) + "}, " + esc(ed["institution"])
             + " \\hfill \\mbox{" + esc(ed["dates"]) + "}\\par")
        if ed.get("extra"):
            b += "\\vspace{-1pt}\n\\noindent\\small\\textit{" + esc(ed["extra"]) + "}\\par"
        blocks.append(b + "\n\\vspace{3pt}\n")
    return "\n".join(blocks)

def skills_tex(skills, groups):
    if not groups:
        return "\\noindent " + esc(", ".join(skills))
    lookup = {s.lower(): g for g, items in groups.items() for s in items}
    buckets, other = {g: [] for g in groups}, []
    for s in skills:
        g = lookup.get(s.lower())
        (buckets[g] if g else other).append(s)
    lines = ["\\noindent\\textbf{" + esc(g) + ":} " + esc(", ".join(v)) + "\\par" for g, v in buckets.items() if v]
    if other:
        lines.append("\\noindent\\textbf{Other:} " + esc(", ".join(other)) + "\\par")
    return "\n".join(lines)

def header_tex(personal, headline):
    tel = re.sub(r"[^0-9+]", "", personal["phone"])
    labels = personal.get("link_style", "labels") != "urls"   # "labels" (clean) or "urls" (survives printing/ATS)
    row1 = " \\ \\textbar\\ ".join([esc(personal.get("location", "")),
        link("tel:" + tel, personal["phone"]), link("mailto:" + personal["email"], personal["email"])])
    row2 = " \\ \\textbar\\ ".join([
        link(personal["linkedin"], "LinkedIn" if labels else personal["linkedin_display"]),
        link(personal["github"], "GitHub" if labels else personal["github_display"]),
        link(personal["portfolio"], "Portfolio" if labels else re.sub(r"^https?://", "", personal["portfolio"]))])
    return (
        "\\begin{center}\n"
        "{\\sffamily\\bfseries\\LARGE\\color{accent} " + esc(personal["name"]) + "}\\\\[3pt]\n"
        + ("{\\small\\textit{" + esc(headline) + "}}\\\\[3pt]\n" if headline else "")
        + "{\\small " + row1 + "}\\\\[1pt]\n{\\small " + row2 + "}\n"
        "\\end{center}\n\\vspace{-6pt}\n")

def section(title, body):
    return "\\section{" + title + "}\n" + body + "\n" if body and body.strip() else ""

def render_body(content, resume_data, opts):
    n_exp, bc = opts["n_exp"], opts["bullets"]
    exps = sorted(content["experiences"], key=lambda e: e["_rank"])[:n_exp]       # keep most relevant
    exps = [e for e in content["experiences"] if e in exps]                       # back to newest-first
    counts = [bc[min(i, len(bc) - 1)] for i in range(len(exps))]
    projs = sorted(content["projects"], key=lambda p: p["_rank"])[:opts["n_proj"]]
    projs = sorted(projs, key=lambda p: str(p.get("end_date", "2025")), reverse=True)

    pubs = "\\begin{itemize}\n" + "\n".join("  \\item " + esc(p) for p in resume_data.get("publications", [])[:2]) \
           + "\n\\end{itemize}" if opts["pubs"] and resume_data.get("publications") else ""
    certs = "\\begin{itemize}\n" + "\n".join("  \\item " + esc(c) for c in resume_data.get("certifications", [])) \
            + "\n\\end{itemize}" if opts["certs"] and resume_data.get("certifications") else ""
    extra = "\\noindent " + esc("; ".join(content["additional"])) if opts["additional"] and content["additional"] else ""

    parts = {
        "summary": section("Summary", "\\noindent " + esc(content["summary"])),
        "experience": section("Experience", exp_tex(exps, counts)),
        "projects": section("Projects", projects_tex(projs)),
        "skills": section("Skills", skills_tex(content["skills"], resume_data.get("skill_groups"))),
        "education": section("Education", education_tex(resume_data["education"])),
        "publications": section("Publications", pubs),
        "certifications": section("Certification", certs),
        "additional": section("Additional Experience", extra),
    }
    order = (["summary", "education", "experience", "projects", "skills", "publications", "certifications", "additional"]
             if content["layout"] == "grad" else
             ["summary", "experience", "projects", "skills", "education", "publications", "certifications", "additional"])
    return "\n".join(parts[k] for k in order)

def pdf_pages(pdf_path):
    out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0

def compile_tex(tex, out_prefix):
    tex_path = out_prefix + ".tex"
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)
    res = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                          "-output-directory", OUTPUT_DIR, tex_path], capture_output=True, text=True)
    base = os.path.join(OUTPUT_DIR, os.path.splitext(os.path.basename(tex_path))[0])
    if res.returncode != 0:
        with open(out_prefix + ".log", "w") as lf:
            lf.write(res.stdout + "\n" + res.stderr)
        raise RuntimeError("pdflatex failed. Log: " + out_prefix + ".log")
    for ext in ("aux", "out", "log"):
        if os.path.exists(base + "." + ext):
            os.remove(base + "." + ext)
    return tex_path, out_prefix + ".pdf"

def render_one_page(template_text, resume_data, content, out_prefix):
    personal = resume_data["personal"]
    head = template_text.replace("<< name >>", esc(personal["name"])).replace(
        "<< header >>", header_tex(personal, content["headline"]))
    pages, tex_path, pdf_path = 0, None, None
    for step, opts in enumerate(LADDER):
        tex = head.replace("<< body >>", render_body(content, resume_data, opts))
        tex_path, pdf_path = compile_tex(tex, out_prefix)
        pages = pdf_pages(pdf_path)
        if pages == 1:
            break
    return tex_path, pdf_path, pages, step


# ── Orchestration per job ─────────────────────────────────────────────────────
def base_summary(resume_data, primary_type, layout, profile=None):
    """Approved summary wording from resume_data.yaml; falls back to the job profile's summary_focus."""
    s = resume_data.get("summaries", {})
    if layout == "grad" and s.get("graduate_data"):
        return s["graduate_data"]
    if primary_type == "backend" and s.get("software_backend"):
        return s["software_backend"]
    return s.get("ai_engineer") or " ".join((profile or {}).get("summary_focus", "").split())

def tailor(resume_data, system_prompt, job, primary_type, profile, exps, projs_sel, allowed, layout="experienced"):
    """Returns (checked, summary, skills, backend). checked=None means curated fallback."""
    counts = [LADDER[0]["bullets"][min(i, 4)] for i in range(len(exps))]
    feedback = ""
    for attempt in (1, 2):
        try:
            out, backend = llm.generate_json(system_prompt,
                                             build_user_prompt(job, primary_type, profile, exps, projs_sel, counts, base_summary(resume_data, primary_type, layout, profile), feedback))
        except llm.LLMError as ex:
            print("  LLM unavailable (" + str(ex)[:140] + "), using curated bullets")
            return None, "", [], "curated"
        checked, bad = check_sections(out, resume_data, exps, projs_sel, allowed)
        if not bad:
            return checked, str(out.get("summary", "")).strip(), out.get("skills", []), backend
        feedback = "these numbers are not in the candidate facts: %s. Remove them." % ", ".join(bad)
        print("  Fact check failed (" + backend + ", attempt " + str(attempt) + "): " + ", ".join(bad))
    return None, "", [], "curated"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "x"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    resume_data = load_yaml(RESUME_DATA_FILE)
    profiles = load_yaml(JOB_PROFILES_FILE)["profiles"]
    cache = load_json(CACHE_FILE, [])
    final_json = load_json(FINAL_JSON, [])
    skipped = load_json(SKIPPED_FILE, {})
    done_links = {e["apply_link"] for e in final_json} | set(skipped)
    jobs = read_jobs(INPUT_JSONL, done_links)
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        template_text = f.read()

    system_prompt = build_system_prompt(resume_data)
    allowed = guards.allowed_numbers(resume_data)
    print("Unprocessed jobs:", len(jobs), "| limit:", JOBS_PER_SESSION, "| LLM order:", llm.ORDER, "\n")

    produced = 0
    for job in jobs:
        if produced >= JOBS_PER_SESSION:
            break
        title, link = job.get("job_name", ""), job.get("apply_link", "")
        text = job_raw_text(job)

        ok, reason = guards.screen_job(job, text, SKIP_INTERNSHIPS)
        primary_type, blend, hits = classify_job(job, profiles)
        tl = text.lower()
        # food-tech exception: a food word in the text AND a tech word in the TITLE (cook / food-safety jobs don't qualify)
        food_job = any(w in tl for w in FOOD_WORDS) and re.search(
            r"\b(ai|data|software|digital|technology|technologist|automation|analytics|machine learning|python)\b",
            title.lower()) is not None
        if ok and not food_job and not guards.is_target_role(job, text):
            ok, reason = False, "not an AI/ML/data or Python/software role"
        if not ok:
            skipped[link] = reason
            print("  skip [" + reason + "]: " + title + " - " + job.get("company", ""))
            continue

        if DRY_RUN:
            print("  WOULD GENERATE: %s - %s (%s)" % (title, job.get("company", ""), job.get("location", "")))
            produced += 1
            continue

        profile = profiles.get(primary_type, profiles["other"])
        layout = guards.layout_for(text)
        exps, projs, additional = select_sections(resume_data, job, primary_type, profiles)
        projs_sel = projs[:LADDER[0]["n_proj"]]
        fp = make_fingerprint(job, primary_type, blend, exps, projs, layout)

        entry = {"apply_link": link, "job_name": title, "company": job.get("company", ""),
                 "location": job.get("location", ""), "country": job.get("country", ""),
                 "job_type": primary_type, "layout": layout, "fit_score": hits, "applied": False,
                 "generated_on": date.today().isoformat()}

        reuse, sim = find_reusable(fp, cache)
        if reuse:
            print("  Reusing (sim=%.2f): %s" % (sim, title))
            final_json.append({**entry, "pdf_path": reuse["pdf_path"], "tex_path": reuse["tex_path"],
                               "reused": True, "generator": reuse.get("generator", ""), "pages": 1})
            save_json(FINAL_JSON, final_json)
            produced += 1
            continue

        print("  Generating: %s - %s [%s, %s]" % (title, job.get("company", ""), primary_type, layout))
        checked, summary, skills, backend = tailor(resume_data, system_prompt, job, primary_type,
                                                   profile, exps, projs_sel, allowed, layout)
        content = build_content(resume_data, profile, exps, projs_sel, additional, layout, checked,
                                summary or base_summary(resume_data, primary_type, layout, profile), skills, text.lower())
        # curated bullets for all projects (not only the 4 the LLM saw) so the ladder can swap in others
        for p in projs[LADDER[0]["n_proj"]:]:
            content["projects"].append({**p, "desc": p.get("desc", "")})

        slug = str(len(final_json) + 1).zfill(3) + "_" + slugify(job.get("company", "")) + "_" + slugify(title)
        try:
            tex_path, pdf_path, pages, step = render_one_page(template_text, resume_data, content,
                                                              os.path.join(OUTPUT_DIR, slug))
        except RuntimeError as e:
            print("  FAILED:", e)
            continue
        print("  PDF: %s (%d page%s, shrink step %d, %s)" % (pdf_path, pages, "" if pages == 1 else "s", step, backend))
        cache.append({"fingerprint": fp, "pdf_path": pdf_path, "tex_path": tex_path, "job_title": title,
                      "generator": backend, "generated_on": datetime.now().isoformat()})
        final_json.append({**entry, "pdf_path": pdf_path, "tex_path": tex_path, "reused": False,
                           "generator": backend, "pages": pages})
        save_json(CACHE_FILE, cache)
        save_json(FINAL_JSON, final_json)
        produced += 1

    if not DRY_RUN:
        save_json(SKIPPED_FILE, skipped)
        try:
            import export
            print("CSV: %d row(s) in Q-Rattor/jobs_to_apply.csv" % export.write_csv())
        except Exception as ex:                     # the CSV is a convenience; never fail the run over it
            print("CSV export skipped:", ex)
    print("\nDone. %d resumes | %s" % (produced, llm.summary()))


if __name__ == "__main__":
    main()
