"""The 100-point score: role fit 35, level fit 20, skills 15, training signal 15, future-facing 10, location 5.
Company size and market cap are never used."""
from datetime import date

import pytest

from board import config, score
from board.extract import extract
from board.filters import apply_filters
from board.models import HistoryRow, Job

CFG = config.scoring_config()


def make(title="Financial Analyst", jd="Basic Qualifications:\n- 1 year of experience.", location="San Jose, CA", company="Acme"):
    row = HistoryRow(url="u", title=title, company=company, location=location, status="added")
    job = Job(id="t", row=row, company=company, title=title, location=location, url="u", portal=company,
              date_used=date(2026, 11, 15), date_approx=False, age_days=0, jd_text=jd, jd_source="api")
    ext = extract(jd, location)
    apply_filters(job, ext, skip_title=True)
    score.score_job(job, ext.to_dict())
    return job


def test_points_per_factor_add_up_to_the_score():
    j = make("Strategic Finance Analyst", "Basic Qualifications:\n- 2 years of experience.\nRotational program. We forecast and automate.",
             "San Francisco, CA", "Stripe")
    parts = j.score_parts
    assert j.score == round(sum(parts[k] for k in ("role_fit", "level_fit", "skills", "training", "future_facing", "location")), 1)
    assert set(CFG["points"]) == {"role_fit", "level_fit", "skills", "training", "future_facing", "location"}
    assert all(parts[k] <= CFG["points"][k] for k in CFG["points"] if k in parts)
    assert parts["role_fit"] == 35 and parts["level_fit"] == 20 and parts["location"] == 5


@pytest.mark.parametrize("title,letter,points", [
    ("FP&A Analyst", "A", 35), ("Financial Planning Analyst", "A", 35),
    ("Strategic Finance Analyst", "B", 35), ("Business Finance Analyst", "B", 35), ("Product Finance Analyst", "B", 35),
    ("Pricing Analyst", "C", 25), ("Deal Desk Analyst", "C", 25),
    ("Treasury Analyst", "D", 25), ("Corporate Development Analyst", "D", 25), ("Investor Relations Associate", "D", 25),
    ("Equity Research Associate", "D", 25), ("Financial Analyst", "D", 25),
])
def test_role_fit_by_track(title, letter, points):
    t = score.track_for(title)
    assert (t["letter"], t["points"]) == (letter, points)


@pytest.mark.parametrize("years,points", [(0, 20), (1, 20), (2, 20), (3, 14), (4, 8)])
def test_level_fit_table(years, points):
    j = make(jd=f"Basic Qualifications:\n- {years}+ years of experience in finance." if years else "Entry level role. No experience required.")
    assert j.score_parts["level_fit"] == points


def test_level_fit_unstated_years():
    assert make(jd="We are a fast growing company.").score_parts["level_fit"] == 15


@pytest.mark.parametrize("years,points", [(2, 15), (3, 9), (4, 3), (None, 10)])
def test_senior_titles_lose_5_more_level_points(years, points):
    jd = f"Basic Qualifications:\n- {years}+ years of experience in finance." if years else "We are a fast growing company."
    assert make("Senior Financial Analyst", jd).score_parts["level_fit"] == points
    assert make("Sr. FP&A Analyst", jd).score_parts["level_fit"] == points


def test_skills_are_3_points_each_capped_at_15():
    none = make(jd="Basic Qualifications:\n- 1 year of experience.")
    assert none.score_parts["skills"] == 0
    two = make(jd="Basic Qualifications:\n- 1 year of experience.\nYou will forecast revenue and run variance analysis.")
    assert two.score_parts["skills"] == 6 and set(two.score_parts["skills_matched"]) == {"forecasting", "variance_scenario"}
    allsix = make(jd="Forecast, scenario analysis, financial model, cross-functional, SQL, automate reporting. 1 year of experience.")
    assert allsix.score_parts["skills"] == 15 and len(allsix.score_parts["skills_matched"]) == 6


@pytest.mark.parametrize("text,points,phrase", [
    ("Join our rotational program for new analysts.", 8, "rotational program"),
    ("This is a leadership development program.", 8, "leadership development program"),
    ("Title: Financial Analyst I", 8, "analyst i"),
    ("Open to new grad applicants.", 8, "new grad"),
    ("An early career role.", 8, "early career"),
    ("This is an entry level position.", 8, "entry level"),
    ("An entry-level analyst role.", 8, "entry-level"),
    ("Recent graduates are encouraged to apply. A recent graduate will thrive.", 8, "recent graduate"),
    ("We hire new graduates every year.", 8, "new graduate"),
    ("Looking for 0-2 years of experience.", 8, "0-2 years"),
    ("Looking for 0–2 years of experience.", 8, "0–2 years"),
    ("Join our analyst program.", 8, "analyst program"),
    ("Includes a formal training program.", 4, "training program"),
    ("You will get mentorship from senior partners.", 4, "mentorship"),
    ("We offer structured training and coaching.", 4, "structured training"),
    ("We will train you on our models.", 4, "we will train"),
    ("Career development and learn from the best.", 4, "career development"),
    ("Clear career ladder: Analyst I/II, path to Senior.", 3, "analyst i/ii"),
    ("Nothing about training here.", 0, None),
])
def test_training_signal_groups(text, points, phrase):
    got, phrases = score.training_signal(text)
    assert got >= points
    if phrase:
        assert phrase in phrases
    else:
        assert got == 0 and phrases == []


def test_training_signal_is_capped_at_15_and_lists_each_matched_phrase():
    got, phrases = score.training_signal("Rotational program with mentorship. Analyst I/II ladder. New grad welcome.")
    assert got == 15 and len(phrases) == 3 and phrases[0] == "rotational program"
    j = make(jd="Rotational program with mentorship and a career ladder: Analyst I/II.")
    assert j.score_parts["training"] == 15 and "rotational program" in j.score_parts["training_phrases"]


def test_training_patterns_live_in_scoring_yaml_so_they_are_editable(monkeypatch):
    cfg = {**CFG, "training_signal": [{"name": "custom", "points": 5, "patterns": ["bootcamp"]}]}
    assert score.training_signal("A paid bootcamp.", cfg) == (5, ["bootcamp"])
    assert score.training_signal("A rotational program.", cfg) == (0, [])


@pytest.mark.parametrize("company,tier,points", [("Stripe", "future", 10), ("Airbnb", "adjacent", 6), ("JPMorgan Chase", "traditional", 2),
                                                 ("Totally Unknown Co", None, 4)])
def test_future_facing_from_sector_tier(company, tier, points):
    j = make(company=company)
    assert j.score_parts["future_facing"] == points
    assert j.score_parts["sector_tier"] == (tier or "unknown")


@pytest.mark.parametrize("location,points", [("San Jose, CA", 5), ("Los Angeles, CA", 5), ("Seattle, WA", 4), ("New York, NY", 4),
                                             ("Chicago, IL", 3), ("Austin, TX", 3), ("United States", 2)])
def test_location_points(location, points):
    assert make(location=location).score_parts["location"] == points


def test_company_size_and_market_cap_never_change_the_score():
    companies = [{**c, "market_cap_usd_b": 5000, "employees": 900000} for c in config.companies_config()]
    base = make(company="Stripe").score
    j = make(company="Stripe")
    rec = score.company_record("Stripe", companies)
    assert rec["market_cap_usd_b"] == 5000 and j.score == base
    tiny = [{**c, "employees": 5} for c in config.companies_config()]
    assert make(company="Stripe").score == base and score.company_record("Stripe", tiny)["employees"] == 5


def test_tiers():
    assert [score.tier_for(p) for p in (80, 75, 74.9, 60, 59.9, 50, 49.9)] == ["Apply now", "Apply now", "Strong", "Strong", "Stretch", "Stretch", ""]
