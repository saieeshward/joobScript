"""
Deterministic checks around the LLM: which jobs deserve a resume, and whether a generated
resume only says things that are true.
"""
from __future__ import annotations

import re

# ── Job screening (a graduate / junior AI engineer, Stamp 1G in Ireland) ─────────────────────────
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from job_filters import SENIOR_TITLE, INTERN_TITLE, TITLE_AI, TITLE_GENERIC, IRELAND_TERMS   # noqa: E402

MAX_YEARS_REQUIRED = 3          # skip jobs whose minimum required experience is >= this
SPONSOR_POS = re.compile(
    r"visa sponsorship|sponsor(?:s|ing)? (?:your |a |work )?(?:visa|permit)|we (?:can|will|do|are able to) sponsor"
    r"|sponsorship (?:is |may be |will be )?(?:available|provided|offered)|relocation (?:assistance|package|support|bonus|allowance)"
    r"|help(?:ing)? (?:you )?(?:with )?relocat|we (?:will )?(?:support|assist) (?:with )?relocation", re.I)
SPONSOR_NEG = re.compile(
    r"no (?:visa )?sponsorship|(?:unable|not able|not in a position|cannot|can't|will not|won't) (?:to )?(?:provide )?sponsor"
    r"|without (?:the need for )?sponsorship|must (?:already )?(?:have|hold|possess) (?:the )?(?:unrestricted )?right to work"
    r"|(?:eligible|authori[sz]ed) to work in (?:the )?(?:us|u\.s\.|uk|eu|germany|netherlands|canada)", re.I)

_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?)\b", re.I)


def required_years(text: str):
    """Smallest 'N+ years of experience' lower bound mentioned, or None."""
    lows = []
    for m in _YEARS.finditer(text):
        window = text[max(0, m.start() - 60): m.end() + 80].lower()
        if "experience" not in window and "exp." not in window:
            continue
        low = int(m.group(1))
        if 0 < low <= 15:
            lows.append(low)
    return min(lows) if lows else None


def is_ireland(job: dict) -> bool:
    """Trust the job's own location. `country` is only the search region the scraper used (LinkedIn returns
    Canadian jobs under an Ireland search), so it is a fallback for blank/remote locations only."""
    loc = (job.get("location") or "").lower().strip()
    if loc and "remote" not in loc:
        return any(t in loc for t in IRELAND_TERMS)
    return any(t in (loc + " " + (job.get("country") or "")).lower() for t in IRELAND_TERMS)


def screen_job(job: dict, text: str, skip_internships: bool = True):
    """Return (ok, reason). Cheap, no LLM."""
    title = job.get("job_name", "")
    if SENIOR_TITLE.search(title):
        return False, "senior-level title"
    if skip_internships and INTERN_TITLE.search(title):
        return False, "internship/co-op"
    yrs = required_years(text)
    if yrs is not None and yrs >= MAX_YEARS_REQUIRED:
        return False, "requires %d+ years" % yrs
    if not is_ireland(job):
        if SPONSOR_NEG.search(text):
            return False, "outside Ireland, no sponsorship"
        if not SPONSOR_POS.search(text):
            return False, "outside Ireland, no sponsorship/relocation offered"
    return True, ""


DESC_AI = re.compile(r"\b(ai|ml|machine learning|llms?|nlp|python|data science|deep learning|rag|generative)\b", re.I)


def is_target_role(job: dict, text: str) -> bool:
    """AI/ML/data titles always count; generic software titles only if the description is AI/Python-flavoured."""
    title = job.get("job_name", "")
    if TITLE_AI.search(title):
        return True
    return bool(TITLE_GENERIC.search(title) and DESC_AI.search(text))


GRAD_HINT = re.compile(r"\b(graduate|grad scheme|entry[- ]level|junior|early career|new grad|0\s*-\s*2 years)\b", re.I)


def layout_for(text: str) -> str:
    return "grad" if GRAD_HINT.search(text) else "experienced"


# ── Fact guard: no invented numbers, skills limited to what the candidate actually has ───────────
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(str(text))}


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif obj is not None and not isinstance(obj, bool):
        yield str(obj)


def allowed_numbers(resume_data: dict) -> set[str]:
    out = set()
    for s in _walk_strings(resume_data):
        out |= numbers_in(s)
    return out


def number_violations(texts, allowed: set[str]) -> list[str]:
    found = set()
    for t in texts:
        found |= numbers_in(t)
    return sorted(found - allowed)


def skill_vocabulary(resume_data: dict) -> dict[str, str]:
    """lower-case -> canonical spelling, for every skill/tech the candidate lists anywhere."""
    vocab = {}
    for s in resume_data.get("skills", []):
        vocab[s.lower()] = s
    for items in (resume_data.get("skill_groups") or {}).values():      # grouped skills are approved too
        for s in items:
            vocab.setdefault(s.lower(), s)
    for sec in ("work_experience", "projects"):
        for e in resume_data.get(sec, []):
            for t in e.get("tech", []):
                vocab.setdefault(t.lower(), t)
    return vocab


# ── Match report: how well the candidate's real skills cover what the job asks for ──────────────────
KEYWORDS = [
    "python", "pytorch", "tensorflow", "keras", "scikit-learn", "pandas", "numpy", "sql", "nosql", "postgres",
    "mongodb", "docker", "kubernetes", "aws", "azure", "gcp", "terraform", "ci/cd", "git", "linux", "rest",
    "api", "fastapi", "flask", "django", "react", "typescript", "javascript", "node", "java", "go", "rust", "c++",
    "llm", "llms", "rag", "nlp", "computer vision", "machine learning", "deep learning", "generative ai", "genai",
    "prompt engineering", "fine-tuning", "agents", "mlops", "spark", "kafka", "airflow", "dbt", "tableau",
    "power bi", "langchain", "vector database", "embeddings", "evaluation", "data pipelines", "etl",
]
_ALIASES = {"llm": "llms", "genai": "generative ai", "agents": "agentic ai", "rest": "api development",
            "api": "api development", "evaluation": "llm evaluation", "vector database": "qdrant",
            "embeddings": "rag", "fine-tuning": "fine-tuning"}
STRONG, PARTIAL, WEAK, UNCLEAR = "Strong match", "Partial match", "NOT a strong match", "Unclear (short description)"


def has_term(term: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#])", text) is not None


def match_report(resume_data: dict, title: str, description: str) -> dict:
    """Honest verdict, in the spirit of 'say so when a role is not a strong match'.
    have = keywords the job asks for that the candidate genuinely has; gap = ones the candidate does not list."""
    text = (title + "\n" + description).lower()
    want = [k for k in KEYWORDS if has_term(k, text)]
    vocab = skill_vocabulary(resume_data)
    have = [k for k in want if k in vocab or _ALIASES.get(k, "") in vocab]
    gap = [k for k in want if k not in have]
    yrs = required_years(text)
    cov = len(have) / len(want) if want else 0.0
    if yrs is not None and yrs >= MAX_YEARS_REQUIRED:
        verdict, reason = WEAK, "seniority mismatch: asks for %d+ years" % yrs
    elif len(want) < 4:
        verdict, reason = UNCLEAR, "too few recognisable skills in the description to judge"
    elif cov < 0.25:
        verdict, reason = WEAK, "skills gap: job asks for %s" % ", ".join(gap[:5])
    elif cov >= 0.6:
        verdict, reason = STRONG, "you cover %d of %d skills the job names" % (len(have), len(want))
    else:
        verdict, reason = PARTIAL, "you cover %d of %d skills; missing %s" % (len(have), len(want), ", ".join(gap[:4]))
    return {"verdict": verdict, "reason": reason, "coverage": round(100 * cov), "want": want, "have": have,
            "gap": gap, "years": yrs}


# ── Skills guard: the model may not mention a tech skill the candidate has not approved ─────────────
_GENERIC = {"go", "rest", "api", "agents", "evaluation", "node", "git", "linux", "embeddings"}   # too ambiguous as plain words


def unsupported_skills(texts, resume_data: dict) -> list[str]:
    """Tech names in the model's text that are not on the candidate's approved skill list (e.g. a 7B model adding
    'Go' or 'Kubernetes'). Returns the offending names."""
    text = " ".join(str(t) for t in texts)
    low = text.lower()
    vocab = skill_vocabulary(resume_data)
    bad = []
    for k in KEYWORDS:
        if k in _GENERIC or k in vocab or _ALIASES.get(k, "") in vocab:
            continue
        if has_term(k, low):
            bad.append(k)
    # "Go" the language: only flag it in list-like positions ("Python, Go, Rust"), never in ordinary sentences
    if "go" not in vocab and re.search(r"(?<=[a-z,] )Go(?=[ ,.;])", text) and re.search(r"[A-Za-z+#]+, Go\b|\bGo, [A-Za-z+#]+", text):
        bad.append("Go")
    return sorted(set(bad))
