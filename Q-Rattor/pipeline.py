"""
Resume Pipeline
- Reads jobs.jsonl (first JOBS_PER_SESSION entries)
- Classifies job type against job_profiles.yaml
- Scores and selects relevant experiences / projects
- Single Qwen2.5:7b call per job -> returns JSON with all resume sections
- Reuses existing resume if similarity score >= REUSE_THRESHOLD
- Renders LaTeX -> pdflatex -> PDF
"""

import os, re, json, copy, subprocess, yaml, requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
JOBS_PER_SESSION  = 50
REUSE_THRESHOLD   = 0.82
USE_OLLAMA        = True
OLLAMA_MODEL      = "qwen2.5:7b"
OLLAMA_URL        = "http://localhost:11434/api/chat"
INPUT_JSONL       = "../jobs.jsonl"
OUTPUT_DIR        = "output"
RESUME_DATA_FILE  = "resume_data.yaml"
JOB_PROFILES_FILE = "job_profiles.yaml"
TEMPLATE_FILE     = "resume_template.tex.j2"
CACHE_FILE        = os.path.join(OUTPUT_DIR, "resume_cache.json")
FINAL_JSON        = "final_apply.json"


# ── Loaders ───────────────────────────────────────────────────────────────────
def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def read_jobs(path, limit, skip_links: set = None):
    """Read up to `limit` unprocessed jobs from path, skipping already-processed apply_links."""
    skip_links = skip_links or set()
    jobs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            job = json.loads(line)
            if job.get("apply_link", "") in skip_links:
                continue
            jobs.append(job)
            if len(jobs) >= limit:
                break
    return jobs

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return []

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


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
    return primary, blend


# ── Scoring & selection ───────────────────────────────────────────────────────
def score_entry(entry, job_text_lower, profile_data):
    score = 0
    entry_tech = [t.lower() for t in entry.get("tech", [])]
    priority_list = [p.lower() for p in
                     profile_data.get("priority_projects", []) +
                     profile_data.get("priority_experiences", [])]
    for t in entry_tech:
        if t in job_text_lower:
            score += 3
    context = (entry.get("full_context", "") + " " + entry.get("desc", "")).lower()
    stopwords = {"and","the","for","with","in","of","to","a","an","is","are","as",
                 "our","you","we","will","be","by","on","or","at","this","that",
                 "have","has","their","its","role","job","team","required","years"}
    words = set(re.findall(r'\b[a-z][a-z+#./-]{2,}\b', job_text_lower)) - stopwords
    for kw in words:
        if kw in context:
            score += 1
    entry_id = (entry.get("name", "") + entry.get("title", "") + entry.get("company", "")).lower()
    for p in priority_list:
        if p in entry_id:
            score += 5
            break
    return score

def select_sections(resume_data, job, primary_type, blend, profiles):
    job_text_lower = job_raw_text(job).lower()
    profile = profiles.get(primary_type, profiles.get("other", {}))
    selected_exp = sorted(
        resume_data["work_experience"],
        key=lambda p: score_entry(p, job_text_lower, profile),
        reverse=True
    )
    
    sorted_exp = sorted(selected_exp, key=lambda p: p.get("end_date", "2025"), reverse=True)
    n_projects = 6 if blend.get(primary_type, 0) > 0.7 else 5
    selected_proj = sorted(
        resume_data["projects"],
        key=lambda p: score_entry(p, job_text_lower, profile),
        reverse=True
    )[:n_projects]
    sorted_proj = sorted(selected_proj, key=lambda p: p.get("end_date", "2025"), reverse=True)
    return sorted_exp, sorted_proj


# ── Fingerprint & reuse ───────────────────────────────────────────────────────
def make_fingerprint(job, primary_type, blend, selected_exp, selected_proj):
    tech_in_job = set(re.findall(
        r'\b(Python|Java|C\+\+|C#|JavaScript|Spring Boot|MongoDB|NodeJs|React|AWS|GCP|'
        r'CI/CD|Git|Linux|GraphQL|REST|Microservices|OpenGL|Unity|Unreal Engine|Kernel|'
        r'Docker|Kubernetes|SQL|FastAPI|Django|Flask|Distributed Systems|AI|Gen AI)\b',
        job_raw_text(job), re.I
    ))
    text = job_raw_text(job).lower()
    seniority = ("senior" if re.search(r'\b(senior|lead|principal|staff)\b', text) else
                 "junior" if re.search(r'\b(junior|entry|graduate|intern)\b', text) else "mid")
    domain_hints = {"fintech": ["bank","finance","payment","wallet"],
                    "gaming":  ["game","gaming","studio"],
                    "saas":    ["saas","platform","b2b"],
                    "research":["research","academic","university","lab"]}
    domain = "general"
    for d, hints in domain_hints.items():
        if any(h in text for h in hints):
            domain = d
            break
    return {
        "job_type":  primary_type,
        "type_blend": blend,
        "tech_set":  sorted(tech_in_job),
        "seniority": seniority,
        "domain":    domain,
        "exp_ids":   sorted(e["title"] + "@" + e["company"] for e in selected_exp),
        "proj_ids":  sorted(p["name"] for p in selected_proj),
    }

def similarity(a, b):
    all_types = set(a["type_blend"]) | set(b["type_blend"])
    type_sim = 1 - sum(abs(a["type_blend"].get(t,0) - b["type_blend"].get(t,0))
                       for t in all_types) / max(len(all_types), 1)
    s1, s2 = set(a["tech_set"]), set(b["tech_set"])
    tech_sim = len(s1 & s2) / max(len(s1 | s2), 1)
    e1,e2 = set(a["exp_ids"]),  set(b["exp_ids"])
    p1,p2 = set(a["proj_ids"]), set(b["proj_ids"])
    sec_sim = (len(e1&e2)/max(len(e1|e2),1) + len(p1&p2)/max(len(p1|p2),1)) / 2
    return (type_sim * 0.30 + tech_sim * 0.30 + sec_sim * 0.25 +
            (0.10 if a["domain"] == b["domain"] else 0) +
            (0.05 if a["seniority"] == b["seniority"] else 0))

def find_reusable(fp, cache):
    best_score, best_entry = 0, None
    for entry in cache:
        s = similarity(fp, entry["fingerprint"])
        if s > best_score:
            best_score, best_entry = s, entry
    return (best_entry, best_score) if best_score >= REUSE_THRESHOLD else (None, best_score)


# ── Ollama ────────────────────────────────────────────────────────────────────
def build_system_prompt(resume_data):
    exp_lines = []
    for e in resume_data["work_experience"]:
        exp_lines.append(
            "- " + e["title"] + " at " + e["company"] + " (" + e["dates"] + ")\n" +
            "  Tech: " + ", ".join(e.get("tech", [])) + "\n" +
            "  Context: " + e.get("full_context", "").strip()
        )
    proj_lines = []
    for p in resume_data["projects"]:
        proj_lines.append(
            "- " + p["name"] + " (" + p["dates"] + ")\n" +
            "  Tech: " + ", ".join(p.get("tech", [])) + "\n" +
            "  Context: " + p.get("full_context", "").strip()
        )
    return (
        "You are a professional resume writer. You know this candidate well:\n\n"
        "NAME: " + resume_data["personal"]["name"] + "\n\n"
        "WORK EXPERIENCE:\n" + "\n\n".join(exp_lines) + "\n\n"
        "PROJECTS:\n" + "\n\n".join(proj_lines) + "\n\n"
        "SKILLS: " + ", ".join(resume_data.get("skills", [])) + "\n"
        "CERTIFICATIONS: " + ", ".join(resume_data.get("certifications", [])) + "\n\n"
        "Stay factually accurate. Never invent tools, metrics, or achievements.\n"
        "Write concise, professional resume content."
    )

def ollama_call(system_prompt, job, primary_type, selected_exp, selected_proj, profile):
    exp_titles = "\n".join("- " + e["title"] + " at " + e["company"] for e in selected_exp)
    proj_names = "\n".join("- " + p["name"] for p in selected_proj)
    user = (
        "Job Title: " + job.get("job_name", "") + "\n"
        "Job Text:\n" + job_raw_text(job)[:2000] + "\n\n"
        "Job Type: " + primary_type + "\n"
        "Profile hint: " + profile.get("summary_focus", "").strip() + "\n\n"
        "Tailor resume for these selected experiences:\n" + exp_titles + "\n\n"
        "And these selected projects:\n" + proj_names + "\n\n"
        'Return JSON with exactly this structure:\n'
        '{\n'
        '  "summary": "2-3 sentence summary tailored to this job",\n'
        '  "skills": ["skill1", "skill2"],\n'
        '  "work_experience": [\n'
        '    {"title": "...", "company": "...", "bullets": ["bullet1", "bullet2", "bullet3"]}\n'
        '  ],\n'
        '  "projects": [\n'
        '    {"name": "...", "desc": "one line tailored description"}\n'
        '  ]\n'
        '}\n\n'
        "RULES — follow exactly:\n"
        "summary: Do NOT use the job title. Write in first person (no 'I' — start with a noun or verb phrase, e.g. 'Experienced engineer with...' or 'Building production ML systems...'). Describe the candidate's actual experience level and strengths honestly. 2-3 sentences.\n"
        "skills: ONLY programming languages, frameworks, libraries, and tools (e.g. Python, PyTorch, RAG). "
        "Never include project names, job titles, descriptive phrases, or soft skills. Max 20, ordered by relevance to this job.\n"
        "work_experience: 3 bullets per role. Each bullet MUST follow: strong action verb + specific technical contribution + measurable result or impact. "
        "Never use vague phrases like 'worked on', 'contributed to', 'utilized', 'assisted with'. "
        "CRITICAL: only use facts from that specific role — do NOT mix details from other roles or projects.\n"
        "projects: only the selected projects. One line tailored to highlight relevance. Include the key tech used.\n"
        "100% factually accurate. Never invent metrics or achievements."
    )
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": 1400},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user}
        ]
    }, timeout=180)
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])


# ── LaTeX escape helpers ───────────────────────────────────────────────────────
def le(text):
    if not text:
        return ""
    out = str(text)
    out = out.replace("\\", "\\textbackslash{}")
    out = out.replace("&",  "\\&")
    out = out.replace("%",  "\\%")
    out = out.replace("$",  "\\$")
    out = out.replace("#",  "\\#")
    out = out.replace("_",  "\\_")
    out = out.replace("~",  "\\textasciitilde{}")
    out = out.replace("^",  "\\textasciicircum{}")
    return out

def lb(text):
    """Escape bullet text — preserve intentional \\& and \\%."""
    if not text:
        return ""
    out = str(text)
    out = out.replace("\\&", "\x00A\x00").replace("\\%", "\x00P\x00")
    out = out.replace("&", "\\&").replace("%", "\\%")
    out = out.replace("#", "\\#").replace("_", "\\_")
    out = out.replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}")
    out = out.replace("\x00A\x00", "\\&").replace("\x00P\x00", "\\%")
    return out


# ── LaTeX block builders (string concat — no f-string brace issues) ───────────
def work_exp_tex(experiences, resume_data):
    lookup = {e["title"] + "@" + e["company"]: e for e in resume_data["work_experience"]}
    blocks = []
    for e in experiences:
        key = str(e["title"]).split(sep=" at")[0] + "@" + e["company"]
        meta = lookup.get(key, e)
        bullets = "\n".join("  \\item " + lb(b) for b in e.get("bullets", meta.get("bullets", [])))
        title_str = le(str(e["title"]).split(sep=" at")[0])
        company_str = le(e["company"])
        location_str = le(meta.get("location", ""))
        dates_str = meta.get("dates", "")
        blocks.append(
            "\\noindent\\textit{" + title_str + "} at "
            "\\textbf{" + company_str + "}, "
            "\\textit{" + location_str + "} \\hfill " + dates_str + "\n"
            "\\vspace{-4pt}\n"
            "\\begin{itemize}[nosep, leftmargin=1.5em, topsep=2pt, itemsep=1pt]\n"
            + bullets + "\n"
            "\\end{itemize}\n"
            "\\vspace{4pt}\n"
        )
    return "\n".join(blocks)

def education_tex(education):
    blocks = []
    for ed in education:
        block = (
            "\\noindent\\textbf{" + le(ed["degree"]) + "} \\hfill " + ed["dates"] + "\\\\\n"
            "\\noindent " + le(ed["institution"])
        )
        if ed.get("extra"):
            block += "\\\\\n\\noindent\\textit{" + le(ed["extra"]) + "}"
        block += "\n\\vspace{4pt}\n"
        blocks.append(block)
    return "\n".join(blocks)

def projects_tex(projects):
    blocks = []
    for p in projects:
        dates = p.get("dates", "").replace("&", "\\&")
        tech = p.get("tech", [])
        tech_line = ""
        if tech:
            tech_str = le(", ".join(tech[:8]))
            tech_line = "\\textit{" + tech_str + "}\\\\[1pt]\n"
        blocks.append(
            "\\noindent\\textbf{" + lb(p["name"]) + "} \\hfill " + dates + "\n"
            "\\vspace{-2pt}\\\\\n"
            "\\noindent " + tech_line +
            "\\noindent " + lb(p.get("desc", "")) + "\\\\[2pt]\n"
        )
    return "\n".join(blocks)


# ── Render + compile ───────────────────────────────────────────────────────────
def render_and_compile(template_text, resume_data, llm_sections, selected_proj, out_prefix):
    personal = resume_data["personal"]

    proj_lookup = {p["name"]: p for p in resume_data["projects"]}
    projects_with_meta = []
    for p in llm_sections.get("projects", []):
        meta = proj_lookup.get(p["name"], {})
        projects_with_meta.append({
            "name":  p["name"],
            "desc":  p.get("desc", meta.get("desc", "")),
            "dates": meta.get("dates", ""),
            "tech":  meta.get("tech", []),
        })

    skills_line = ", ".join(llm_sections.get("skills", resume_data.get("skills", [])))
    skills_line = skills_line.replace("#", "\\#").replace("_", "\\_")

    summary_tex = lb(llm_sections.get("summary", ""))
    pubs = "\n".join("  \\item " + lb(p) for p in resume_data.get("publications", []))
    certs = "\n".join("  \\item " + le(c) for c in resume_data.get("certifications", []))

    tex = template_text
    tex = tex.replace("<< portfolio >>",         personal["portfolio"])
    tex = tex.replace("<< location >>",          le(personal.get("location", "")))
    tex = tex.replace("<< name >>",              le(personal["name"]))
    tex = tex.replace("<< phone >>",             personal["phone"])
    tex = tex.replace("<< email >>",             personal["email"])
    tex = tex.replace("<< github >>",            personal["github"])
    tex = tex.replace("<< github_display >>",    personal["github_display"])
    tex = tex.replace("<< linkedin >>",          personal["linkedin"])
    tex = tex.replace("<< linkedin_display >>",  personal["linkedin_display"])
    tex = tex.replace("<< summary_block >>",     summary_tex)
    tex = tex.replace("<< skills_line >>",       skills_line)
    tex = tex.replace("<< work_experience_block >>", work_exp_tex(llm_sections.get("work_experience", []), resume_data))
    tex = tex.replace("<< education_block >>",       education_tex(resume_data["education"]))
    tex = tex.replace("<< publications_block >>",    pubs)
    tex = tex.replace("<< projects_block >>",        projects_tex(projects_with_meta))
    tex = tex.replace("<< certifications_block >>",  certs)

    for section, items in (("Publications", pubs), ("Certification", certs)):
        if not items.strip():
            tex = re.sub(r"\\section\{" + section + r"\}.*?\\end\{itemize\}\n", "", tex, flags=re.S)

    tex_path = out_prefix + ".tex"
    log_path = out_prefix + ".log"

    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)

    result = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
         "-output-directory", OUTPUT_DIR, tex_path],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        with open(log_path, "w") as lf:
            lf.write(result.stdout + "\n" + result.stderr)
        raise RuntimeError("pdflatex failed. Log: " + log_path)

    base = os.path.splitext(os.path.basename(tex_path))[0]
    for ext in ["aux", "out"]:
        p = os.path.join(OUTPUT_DIR, base + "." + ext)
        if os.path.exists(p):
            os.remove(p)

    return tex_path, out_prefix + ".pdf"


# ── Helpers ────────────────────────────────────────────────────────────────────
def slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:55] or "resume"

def fallback_sections(resume_data, selected_exp, selected_proj, profile):
    return {
        "summary": profile.get("summary_focus", "").strip(),
        "skills":  resume_data.get("skills", []),
        "work_experience": [
            {"title": e["title"], "company": e["company"], "bullets": e["bullets"]}
            for e in selected_exp
        ],
        "projects": [
            {"name": p["name"], "desc": p["desc"]} for p in selected_proj
        ]
    }

def load_final_json() -> list[dict]:
    if os.path.exists(FINAL_JSON):
        with open(FINAL_JSON) as f:
            return json.load(f)
    return []

def write_final_json(entries: list[dict]) -> None:
    with open(FINAL_JSON, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    resume_data = load_yaml(RESUME_DATA_FILE)
    profiles    = load_yaml(JOB_PROFILES_FILE)["profiles"]
    cache       = load_cache()
    final_json  = load_final_json()
    processed_links = {e["apply_link"] for e in final_json}
    jobs        = read_jobs(INPUT_JSONL, JOBS_PER_SESSION, processed_links)
    jobs        = read_jobs(INPUT_JSONL, JOBS_PER_SESSION, processed_links)
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        template_text = f.read()

    system_prompt = build_system_prompt(resume_data)
    print("Processing", len(jobs), "jobs | reuse threshold:", REUSE_THRESHOLD, "\n")

    for idx, job in enumerate(jobs, 1):
        title = job.get("job_name", "job_" + str(idx))
        apply_link = job.get("apply_link", "")

        primary_type, blend = classify_job(job, profiles)
        profile = profiles.get(primary_type, profiles["other"])
        active_blend = {k: v for k, v in blend.items() if v > 0}
        print("  Type:", primary_type, "| blend:", active_blend)

        if re.search(r'\b(senior|lead|principal|staff)\b', job_raw_text(job), re.I):
            print("  Skipping (senior-level):", title)
            processed_links.add(apply_link)
            continue

        selected_exp, selected_proj = select_sections(resume_data, job, primary_type, blend, profiles)
        fp = make_fingerprint(job, primary_type, blend, selected_exp, selected_proj)

        reuse_entry, best_sim = find_reusable(fp, cache)
        if reuse_entry:
            print("  Reusing (sim=" + str(round(best_sim, 2)) + "):", reuse_entry["pdf_path"])
            final_json.append({
                "apply_link": apply_link,
                "job_name":   title,
                "company":    job.get("company", ""),
                "location":   job.get("location", ""),
                "country":    job.get("country", ""),
                "pdf_path":   reuse_entry["pdf_path"],
                "tex_path":   reuse_entry["tex_path"],
                "reused":     True,
                "applied":    False,
            })
            processed_links.add(apply_link)
            write_final_json(final_json)
            continue

        print("  Generating new (best cache sim=" + str(round(best_sim, 2)) + ")")

        if USE_OLLAMA:
            try:
                llm_sections = ollama_call(system_prompt, job, primary_type, selected_exp, selected_proj, profile)
            except Exception as ex:
                print("  Ollama failed (" + str(ex) + "), using fallback")
                llm_sections = fallback_sections(resume_data, selected_exp, selected_proj, profile)
        else:
            llm_sections = fallback_sections(resume_data, selected_exp, selected_proj, profile)

        slug = str(idx).zfill(2) + "_" + slugify(title)
        out_prefix = os.path.join(OUTPUT_DIR, slug)

        try:
            tex_path, pdf_path = render_and_compile(template_text, resume_data, llm_sections, selected_proj, out_prefix)
            print("  PDF:", pdf_path)
            cache.append({
                "fingerprint":  fp,
                "pdf_path":     pdf_path,
                "tex_path":     tex_path,
                "job_title":    title,
                "generated_on": datetime.now().isoformat()
            })
            final_json.append({
                "apply_link": apply_link,
                "job_name":   title,
                "company":    job.get("company", ""),
                "location":   job.get("location", ""),
                "country":    job.get("country", ""),
                "pdf_path":   pdf_path,
                "tex_path":   tex_path,
                "reused":     False,
                "applied":    False,
            })
            processed_links.add(apply_link)
            save_cache(cache)
            write_final_json(final_json)
            
        except RuntimeError as e:
            print("  FAILED:", e)

    print("\nDone.")

if __name__ == "__main__":
    main()
