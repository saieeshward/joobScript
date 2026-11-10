import guards as g


def job(title, location="Dublin, Ireland"):
    return {"job_name": title, "location": location}


def test_required_years():
    assert g.required_years("We need 3-5 years of experience in Python") == 3
    assert g.required_years("2+ years experience with ML, 5+ years cloud experience") == 2
    assert g.required_years("a 5 year old company with great people") is None
    assert g.required_years("10 years of the company's history") is None


def test_screen_job_titles_and_years():
    assert g.screen_job(job("Junior AI Engineer"), "junior", True)[0]
    assert g.screen_job(job("Senior ML Engineer"), "", True) == (False, "senior-level title")
    ok, why = g.screen_job(job("AI Engineer"), "5+ years of experience required", True)
    assert not ok and "5+" in why
    assert not g.screen_job(job("AI Engineer Intern"), "", True)[0]
    assert g.screen_job(job("AI Engineer Intern"), "", False)[0]           # internships allowed when switched off


def test_screen_job_outside_ireland_needs_sponsorship():
    assert not g.screen_job(job("AI Engineer", "Berlin"), "We offer free pizza", True)[0]
    assert g.screen_job(job("AI Engineer", "Berlin"), "We offer visa sponsorship and relocation support", True)[0]
    assert not g.screen_job(job("AI Engineer", "Berlin"),
                            "visa sponsorship available but you must already have the right to work in Germany", True)[0]
    assert not g.screen_job(job("AI Engineer", "Toronto, Canada"), "No visa sponsorship", True)[0]


def test_ireland_uses_location_not_search_country():
    assert g.is_ireland({"location": "Cork, County Cork, Ireland", "country": ""})
    assert not g.is_ireland({"location": "Canada", "country": "Ireland"})
    assert g.is_ireland({"location": "Remote", "country": "Ireland"})
    assert g.is_ireland({"location": "", "country": "Ireland"})


def test_target_role():
    assert g.is_target_role({"job_name": "Machine Learning Engineer"}, "")
    assert g.is_target_role({"job_name": "Software Engineer"}, "You will write Python services")
    assert not g.is_target_role({"job_name": "Software Engineer"}, "COBOL mainframes only")
    assert not g.is_target_role({"job_name": "Site Reliability Engineer"}, "python")


def test_layout_for():
    assert g.layout_for("Accenture AI & Data Graduate Programme") == "grad"
    assert g.layout_for("Applied AI Engineer") == "experienced"


def test_number_guard_flags_invented_metrics():
    allowed = g.numbers_in("5,493 campaigns, 34-2, 87% accuracy, 4K video")
    assert g.number_violations(["Cut cost by 40% across 7 services"], allowed) == ["40", "7"]
    assert g.number_violations(["Added 5,493 campaigns; preferred 34-2; 87% accuracy"], allowed) == []
