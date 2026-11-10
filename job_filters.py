"""
Title/location rules shared by the scrapers and the resume pipeline (Q-Rattor/guards.py),
so both sides agree on what counts as a wanted job.
"""
from __future__ import annotations

import re

SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head of|director|manager|architect|vp|vice president)\b", re.I)
INTERN_TITLE = re.compile(
    r"\b(intern|interns|internship|co-?op|work placement|placement|apprentice(ship)?|volunteer|unpaid)\b", re.I)
TITLE_AI = re.compile(
    r"\b(ai|a\.i\.|ml|machine learning|deep learning|llm|llms|nlp|genai|gen ai|generative|mlops|computer vision|"
    r"data scientist|data engineer|data analyst|applied scientist|research (?:engineer|scientist)|agentic|prompt)\b", re.I)
TITLE_GENERIC = re.compile(
    r"\b(software (?:engineer|developer)|developer|back-?end|full[- ]?stack|python)\b", re.I)

IRELAND_TERMS = ["ireland", "dublin", "cork", "limerick", "galway", "waterford", "kilkenny",
                 "athlone", "sligo", "drogheda", "maynooth", "belfast", "leixlip", "naas", "louth", "kildare"]

_KNOWN_COUNTRIES = {
    "canada": "Canada", "united states": "United States", "usa": "United States", "us": "United States",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "england": "United Kingdom",
    "india": "India", "germany": "Germany", "netherlands": "Netherlands", "france": "France",
    "spain": "Spain", "poland": "Poland", "sweden": "Sweden", "singapore": "Singapore",
    "united arab emirates": "UAE", "uae": "UAE", "saudi arabia": "Saudi Arabia", "australia": "Australia",
}


def title_wanted(title: str, extra_keywords=()) -> bool:
    """Cheap title screen applied before any description is fetched."""
    if TITLE_AI.search(title) or TITLE_GENERIC.search(title):
        return True
    t = title.lower()
    return any(k.lower() in t for k in extra_keywords)


def infer_country(location: str, default: str = "") -> str:
    """The country of the job itself. `default` (the region that was searched) is only used when the
    location is blank, remote, or already points at that country, because LinkedIn happily returns
    Canadian jobs for an Ireland search."""
    loc = (location or "").lower().strip()
    if not loc or "remote" in loc:
        return default
    if any(t in loc for t in IRELAND_TERMS):
        return "Ireland"
    last = loc.split(",")[-1].strip()
    if last in _KNOWN_COUNTRIES:
        return _KNOWN_COUNTRIES[last]
    if default and default.lower() in loc:
        return default
    return "Unknown"
