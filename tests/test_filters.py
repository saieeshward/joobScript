from job_filters import INTERN_TITLE, SENIOR_TITLE, infer_country, title_wanted


def test_infer_country_trusts_the_job_location_not_the_search_region():
    assert infer_country("Dublin, County Dublin, Ireland", "Ireland") == "Ireland"
    assert infer_country("Toronto, Ontario, Canada", "Ireland") == "Canada"
    assert infer_country("Canada", "Ireland") == "Canada"          # the real bug: Canadian job under an Ireland search
    assert infer_country("Markham", "Ireland") == "Unknown"
    assert infer_country("Naas, County Kildare", "Ireland") == "Ireland"


def test_infer_country_uses_default_only_for_blank_or_remote():
    assert infer_country("", "Ireland") == "Ireland"
    assert infer_country("Remote", "Ireland") == "Ireland"


def test_title_wanted():
    assert title_wanted("Applied AI Engineer")
    assert title_wanted("Back-End Python Developer")
    assert title_wanted("Data Analyst")
    assert not title_wanted("Site Reliability Engineer")
    assert not title_wanted("Cloud Security Engineer")
    assert title_wanted("Restaurant Operations Lead", ["restaurant"])


def test_title_regexes():
    assert INTERN_TITLE.search("AI Engineer Intern/Co-op")
    assert INTERN_TITLE.search("Volunteer: AI Placement in India")
    assert not INTERN_TITLE.search("International Data Engineer")
    assert SENIOR_TITLE.search("Senior ML Engineer")
    assert SENIOR_TITLE.search("Solutions Architect")
    assert SENIOR_TITLE.search("Software Engineer 3, Data Pipeline")
    assert SENIOR_TITLE.search("Data Engineer IV")
    assert not SENIOR_TITLE.search("Software Engineer 2")          # mid-level: allowed, ranked lower
    assert not SENIOR_TITLE.search("Junior AI Engineer")
