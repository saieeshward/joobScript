import sqlite3

from scrapers.boards import _html_to_text, _in_ireland
from scrapers.linkedin import LinkedInScraper

CARD = """<li><div class="base-card"><a class="base-card__full-link" href="https://ie.linkedin.com/jobs/view/ai-engineer-at-acme-123?trk=x">
<span class="sr-only">AI Engineer</span></a><h3 class="base-search-card__title">  AI Engineer </h3>
<h4 class="base-search-card__subtitle"><a>Acme &amp; Co</a></h4>
<span class="job-search-card__location">Dublin, County Dublin, Ireland</span></div></li>"""


def test_linkedin_card_parsing():
    cards = LinkedInScraper()._parse_cards(CARD)
    assert cards == [{"title": "AI Engineer", "company": "Acme & Co", "location": "Dublin, County Dublin, Ireland",
                      "href": "https://ie.linkedin.com/jobs/view/ai-engineer-at-acme-123"}]


def test_linkedin_description_html_to_text():
    t = LinkedInScraper._html_to_text("<p>Hello</p><ul><li>Python</li><li>SQL</li></ul>Line<br>two")
    assert "- Python" in t and "- SQL" in t and "Line\ntwo" in t


def test_greenhouse_double_encoded_html():
    assert _html_to_text("&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;li&gt;Python&lt;/li&gt;") == "Build & ship\n- Python"


def test_board_location_filter():
    assert _in_ireland("Dublin, Ireland; London, UK")
    assert not _in_ireland("Toronto, Canada")


def test_seen_db_unmark(tmp_path, monkeypatch):
    from storage import seen
    monkeypatch.setattr(seen, "_DB_PATH", str(tmp_path / "seen.db"))
    monkeypatch.setattr(seen, "_conn", None)
    seen.mark_seen_bulk(["a", "b", "c"])
    new, ratio = seen.filter_new([{"job_id": "a"}, {"job_id": "z"}])
    assert [c["job_id"] for c in new] == ["z"] and ratio == 0.5
    seen.unmark_bulk(["a"])
    new, _ = seen.filter_new([{"job_id": "a"}])
    assert len(new) == 1
    seen._conn.close()
    assert sqlite3.connect(str(tmp_path / "seen.db")).execute("select count(*) from seen_jobs").fetchone()[0] == 2
