import json, os, shutil
from datetime import date, timedelta

import pytest
import yaml

import pipeline as P

QR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Q-Rattor")


@pytest.fixture(scope="module")
def resume():
    return yaml.safe_load(open(os.path.join(QR, "resume_data.example.yaml"), encoding="utf-8"))


@pytest.fixture(scope="module")
def profiles():
    return yaml.safe_load(open(os.path.join(QR, "job_profiles.example.yaml"), encoding="utf-8"))["profiles"]


def test_latex_escaping():
    assert P.esc("R&D 50% #1 a_b") == r"R\&D 50\% \#1 a\_b"
    assert P.esc(r"already \% escaped") == r"already \% escaped"
    assert P.esc("a → b ≥ c") == "a -> b >= c"
    assert P.esc("{x}") == r"\{x\}"


def test_unrelated_part_time_job_is_hidden_unless_job_is_food_related(resume, profiles):
    ai_job = {"job_name": "AI Engineer", "description": "Build LLM agents in Python"}
    exps, _projs, extra = P.select_sections(resume, ai_job, "ml_ai", profiles)
    assert "Local Cafe" not in [e["company"] for e in exps]
    assert extra and "Local Cafe" in extra[0]
    food_job = {"job_name": "Food Data Analyst", "description": "restaurant analytics"}
    exps, _projs, extra = P.select_sections(resume, food_job, "ml_ai", profiles)
    assert "Local Cafe" in [e["company"] for e in exps] and not extra


def test_check_sections_rejects_invented_numbers(resume):
    allowed = P.guards.allowed_numbers(resume)
    out = {"summary": "Improved latency by 73%", "work_experience": [], "projects": []}
    _checked, bad = P.check_sections(out, resume, [], [], allowed)
    assert bad == ["73"]
    out = {"summary": "Served 150+ customers", "work_experience": [], "projects": []}
    assert P.check_sections(out, resume, [], [], allowed)[1] == []


def test_skills_only_from_approved_groups(resume):
    skills = P.pick_skills(["Python", "Blender", "RAG"], resume, "python rag")
    assert "Blender" not in skills and "Python" in skills and "RAG" in skills


def test_read_jobs_skips_stale_and_seen(tmp_path):
    f = tmp_path / "jobs.jsonl"
    fresh, old = date.today().isoformat(), (date.today() - timedelta(days=30)).isoformat()
    rows = [{"apply_link": "https://a", "scraped_on": fresh}, {"apply_link": "https://b", "scraped_on": old},
            {"apply_link": "https://c", "scraped_on": fresh}, {"apply_link": "https://a", "scraped_on": fresh}]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    got = P.read_jobs(str(f), {"https://c"})
    assert [j["apply_link"] for j in got] == ["https://a"]


def test_header_links_labels_and_urls(resume):
    h = P.header_tex(resume["personal"], "Headline")
    assert r"\href{https://linkedin.com/in/your-handle}{LinkedIn}" in h and "tel:+000000000000" in h
    p2 = {**resume["personal"], "link_style": "urls"}
    assert "linkedin.com/in/your-handle}" in P.header_tex(p2, "") and "{LinkedIn}" not in P.header_tex(p2, "")


@pytest.mark.skipif(not shutil.which("pdflatex") or not shutil.which("pdfinfo"), reason="needs pdflatex + pdfinfo")
def test_resume_renders_to_exactly_one_page(resume, profiles, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(P, "OUTPUT_DIR", str(out))
    job = {"job_name": "AI Engineer", "description": "Python LLM agents"}
    exps, projs, extra = P.select_sections(resume, job, "ml_ai", profiles)
    content = P.build_content(resume, profiles["ml_ai"], exps, projs[:4], extra, "experienced", None, "",
                              [], "python llm")
    for p in projs[4:]:
        content["projects"].append(dict(p))
    tpl = open(os.path.join(QR, "resume_template.tex.j2"), encoding="utf-8").read()
    _tex, pdf, pages, _step = P.render_one_page(tpl, resume, content, str(out / "t"))
    assert pages == 1 and os.path.exists(pdf)


def test_fit_answers_template_guards_and_retry(resume, monkeypatch):
    import fit_answers, llm
    job = {"job_name": "AI Engineer", "company": "Acme", "description": "We use Python, RAG and LLMs daily. Docker too."}
    tpl = fit_answers._template(resume, job)
    assert "visa" not in (tpl["why_good_fit"] + tpl["cold_email"]).lower() and tpl["cold_email_subject"]
    allowed = P.guards.allowed_numbers(resume)
    note = " ".join(["Plain words fill this short sentence for the test run now."] * 5)       # 50 words, 5 sentences, no digits
    body = " ".join(["word"] * 130)
    calls = []

    def fake(system, user):
        calls.append(user)
        bad = {"short_note": note + " I cut costs by 73%.", "cold_email_subject": "S", "cold_email_body": body}
        good = {"short_note": note, "cold_email_subject": "S", "cold_email_body": body}
        return (bad if len(calls) == 1 else good), "fake"

    monkeypatch.setattr(llm, "generate_json", fake)
    out, by = fit_answers.answer_for(resume, "sys", job, allowed)
    assert by == "fake" and len(calls) == 2 and "73" not in out["why_good_fit"]      # invented 73% rejected, retried once

    monkeypatch.setattr(llm, "generate_json", lambda s, u: ({"short_note": note, "cold_email_subject": "S", "cold_email_body": "too short"}, "fake"))
    out, by = fit_answers.answer_for(resume, "sys", job, allowed)
    assert by == "template"                                    # still invalid after the retry -> safe template


def test_match_report_verdicts(resume):
    g = P.guards
    strong = g.match_report(resume, "AI Engineer", "Python, SQL, RAG and LLMs required")
    assert strong["verdict"] == g.STRONG and strong["gap"] == []
    weak = g.match_report(resume, "Engineer", "We need aws azure gcp kubernetes terraform spark")
    assert weak["verdict"] == g.WEAK and "skills gap" in weak["reason"]
    assert g.match_report(resume, "Engineer", "5+ years of experience with python sql rag llm")["verdict"] == g.WEAK
    assert g.match_report(resume, "Engineer", "Python")["verdict"] == g.UNCLEAR


def test_unsupported_skills_guard(resume):
    g = P.guards
    assert g.unsupported_skills(["Skilled in Python, Go, Rust and Kubernetes"], resume) == ["Go", "kubernetes", "rust"]   # example resume lists neither
    assert g.unsupported_skills(["Built a RAG tool in Python. Go to market with the team."], resume) == []
    out = {"summary": "Fluent in Kubernetes", "work_experience": [], "projects": []}
    assert any("kubernetes" in b for b in P.check_sections(out, resume, [], [], P.guards.allowed_numbers(resume))[1])
