"""
Application text per job, ready to paste: a short "Why are you a good fit?" note and a cold email to a recruiter.

    python fit_answers.py            writes both for every job in final_apply.json that does not have them yet
    python fit_answers.py --redo     rewrites all of them

Shaped by the SHORT_OUTREACH and COLD_MAIL prompts in "The Ultimate Resume Prompt System for Software Engineers"
(Mavani Ankit, Medium, Jan 2026), adapted to an early-career candidate. Same safety rules as the resume: only facts from
resume_data.yaml, every number must exist there (retry once, then a plain template), no claims about years of
experience or visa status, nothing the candidate could not defend in an interview.
Called automatically for each new resume by pipeline.py.
"""
from __future__ import annotations

import json
import os
import re
import sys

import yaml

import guards
import llm

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL_JSON = os.path.join(HERE, "final_apply.json")
JOBS_JSONL = os.path.join(HERE, "..", "jobs.jsonl")
RESUME_DATA = os.path.join(HERE, "resume_data.yaml")

RULES = (
    "Write TWO things for this job, using ONLY the candidate facts above. Everything must be interview-safe: if the "
    "candidate could not defend a line out loud, do not write it.\n\n"
    "1) short_note: the answer to 'Why are you a good fit?' for a text box (Wellfound / LinkedIn / application form).\n"
    "   - ONE paragraph, 3 to 5 short sentences, 45 to 90 words. Focus on role fit, skills match and impact.\n"
    "   - Do NOT repeat resume bullets word for word: say why the fit is real, using the job's own keywords only where "
    "the candidate genuinely has the skill.\n"
    "   - No fluff, no cliches, no exaggeration, no flattery of the company.\n\n"
    "2) cold_email_subject and cold_email_body: a cold email to a recruiter or hiring manager.\n"
    "   - 120 to 150 words in the body (count them). Confident, respectful, specific, non-generic. No buzzwords, no emojis, no "
    "desperation. Start 'Hello,' and end with the candidate's first name.\n"
    "   - Highlight only role-relevant experience from the facts and tie it to what the job asks for. One clear ask "
    "(a short conversation or a pointer to the right person).\n\n"
    "Plain English for a recruiter who is not an engineer: say what was built and why it mattered, not how. Never use "
    "internal terms such as fail-closed, schema tests, pinned cost table, blind-reviewed, grid export, ADR, pipeline "
    "stages. At most two well-known keywords (LLM, RAG, Python, Rust) per paragraph. Do not describe the candidate's "
    "stack as including infrastructure work (Terraform, AWS) unless the facts say they built it.\n"
    "Global rules: the candidate is early-career, so never claim years of experience or seniority; never mention visas or "
    "work permits; every number must appear in the candidate facts; keep shared-credit wording (co-built, proposed, with "
    "the team).\n"
    'Return ONLY JSON: {"short_note": "...", "cold_email_subject": "...", "cold_email_body": "..."}'
)


JARGON = ["fail-closed", "schema test", "cost table", "blind-review", "blind review", "grid export", " adr ", "terraform"]


def _system_prompt(resume: dict) -> str:
    exp = ["- %s at %s: %s" % (e["title"], e["company"], " ".join(e.get("full_context", "").split()))
           for e in resume["work_experience"] if not e.get("show_when_keywords")]
    proj = ["- %s: %s" % (p["name"], p.get("desc", "")) for p in resume["projects"]]
    edu = ["- %s, %s (%s). %s" % (e["degree"], e["institution"], e["dates"], e.get("extra", ""))
           for e in resume["education"]]
    return ("You write honest, specific application answers for " + resume["personal"]["name"] + ".\n\nEXPERIENCE:\n"
            + "\n".join(exp) + "\n\nPROJECTS:\n" + "\n".join(proj) + "\n\nEDUCATION:\n" + "\n".join(edu)
            + "\n\nReturn ONLY a JSON object.")


def _template(resume: dict, job: dict) -> dict:
    """No model available: plain, safe wording built from the approved summary and the job's own keywords."""
    text = (job.get("job_name", "") + " " + job.get("description", "")).lower()
    have = guards.match_report(resume, job.get("job_name", ""), job.get("description", ""))["have"]
    core = [k for k in have if k.title() in set(resume.get("core_skills", [])) or k in ("python", "rag", "llms")] or have
    skills = ", ".join(core[:3])
    base = (resume.get("summaries", {}).get("ai_engineer") or "").rstrip(".")
    first = resume["personal"]["name"].split()[0]
    note = ("%s. %sI would like to bring that hands-on experience to this role and keep learning from the team."
            % (base, ("This role asks for %s, which I use in my current work. " % skills) if skills else ""))
    body = ("Hello,\n\nI am applying for the %s role at %s. %s\n\n%sIf it is useful, I would welcome a short "
            "conversation, or a pointer to the right person on the team.\n\nThank you,\n%s"
            % (job.get("job_name", "open"), job.get("company", "your company"), note + ".",
               "", first))
    return {"why_good_fit": note, "cold_email_subject": "%s: %s" % (job.get("job_name", "Application"), resume["personal"]["name"]),
            "cold_email": body}


def _wc(s: str) -> int:
    return len(s.split())


def answer_for(resume: dict, system_prompt: str, job: dict, allowed: set) -> tuple[dict, str]:
    """Returns ({why_good_fit, cold_email_subject, cold_email}, written_by)."""
    user = "JOB: %s at %s\nJOB TEXT:\n%s\n\n%s" % (job.get("job_name", ""), job.get("company", ""),
                                                  (job.get("description") or "")[:3000], RULES)
    feedback = ""
    for _ in (1, 2):
        try:
            out, backend = llm.generate_json(system_prompt, user + feedback)
        except llm.LLMError:
            break
        note = " ".join(str(out.get("short_note", "")).split())
        subject = " ".join(str(out.get("cold_email_subject", "")).split())
        body = str(out.get("cold_email_body", "")).strip()
        problems = []
        bad = guards.number_violations([note, subject, body], allowed)
        if bad:
            problems.append("remove these numbers, they are not in the facts: " + ", ".join(bad))
        if not note or not 2 <= len(re.findall(r"[.!?](?:\s|$)", note)) <= 5 or not 40 <= _wc(note) <= 100:
            problems.append("short_note must be one paragraph of 3 to 5 short sentences, 45 to 90 words (it was %d)" % _wc(note))
        if not body or not subject or not 115 <= _wc(body) <= 155:
            problems.append("cold_email_body must be 120 to 150 words (it was %d) and have a subject" % _wc(body))
        jargon = [w for w in JARGON if w in (note + " " + body).lower()]
        if jargon:
            problems.append("remove internal jargon: " + ", ".join(jargon))
        if not problems:
            return {"why_good_fit": note, "cold_email_subject": subject, "cold_email": body}, backend
        feedback = "\n\nFIX: " + "; ".join(problems)
    return _template(resume, job), "template"


def main(redo: bool = False) -> int:
    resume = yaml.safe_load(open(RESUME_DATA, encoding="utf-8"))
    final = json.load(open(FINAL_JSON, encoding="utf-8"))
    jobs = {}
    with open(JOBS_JSONL, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                j = json.loads(line)
                jobs[j.get("apply_link", "")] = j
    system_prompt, allowed = _system_prompt(resume), guards.allowed_numbers(resume)
    done = 0
    for e in final:
        if e.get("cold_email") and e.get("why_good_fit") and not redo:
            continue
        job = {**jobs.get(e["apply_link"], {}), "job_name": e["job_name"], "company": e.get("company", "").split("\n")[0]}
        texts, by = answer_for(resume, system_prompt, job, allowed)
        e.update(texts)
        e["why_good_fit_by"] = by
        done += 1
        print("  %s - %s [%s]" % (e["job_name"][:50], job["company"][:20], by))
        with open(FINAL_JSON, "w", encoding="utf-8") as fh:      # save as we go: a long run can be interrupted
            json.dump(final, fh, indent=2)
    print("Wrote %d answer set(s). %s" % (done, llm.summary()))
    return done


if __name__ == "__main__":
    main(redo="--redo" in sys.argv)
