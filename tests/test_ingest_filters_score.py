from datetime import date

from board import config, ingest
from board.extract import extract
from board.filters import apply_filters, classify_location
from board.models import HistoryRow, Job
from board.score import company_priority, score_job

TODAY = date(2026, 10, 29)


def _row(**kw):
    base = dict(url="https://x.example/1", first_seen="2026-10-29", portal="Stripe", title="Financial Analyst",
                company="Stripe", status="added", location="San Francisco, CA", posted_at="")
    base.update(kw)
    return HistoryRow(**base)


def _job(row, jd=""):
    used = ingest.parse_date(row.posted_at) or ingest.parse_date(row.first_seen)
    return Job(id=ingest.job_id(row), row=row, company=row.company, title=row.title, location=row.location,
               url=row.url, portal=row.portal, date_used=used, date_approx=not row.posted_at,
               age_days=(TODAY - used).days, jd_text=jd, jd_source="fetch-jd" if jd else "none")


# ---- ingest ----------------------------------------------------------------------------------------

def test_parse_line_handles_short_rows_and_formula_escape():
    r = ingest.parse_line("https://a/b\t2026-10-29\tPortal\t'=SUM Analyst\tAcme\tadded")
    assert r.title == "=SUM Analyst" and r.status == "added" and r.location == "" and r.listing_key == ""
    assert ingest.unescape_cell("'abc") == "'abc"           # a real leading apostrophe is untouched


def test_job_id_is_stable_and_prefers_listing_key():
    a = _row(listing_key="listing_v1_abc", url="https://x/1")
    b = _row(listing_key="listing_v1_abc", url="https://x/2")
    assert ingest.job_id(a) == ingest.job_id(b)
    assert ingest.job_id(_row(url="https://x/1/")) == ingest.job_id(_row(url="https://X/1?utm=1"))


def test_select_rows_uses_posted_at_then_first_seen_with_approx_flag():
    rows = [
        _row(url="https://x/new", posted_at="2026-10-27"),
        _row(url="https://x/old", posted_at="2026-10-20", first_seen="2026-10-29"),
        _row(url="https://x/nodate", posted_at="", first_seen="2026-10-28"),
        _row(url="https://x/loc", status="skipped_location", location="London"),
        _row(url="https://x/title", status="skipped_title"),
    ]
    sel = ingest.select_rows(rows, TODAY, 4)
    by_url = {r.url: (d, approx) for r, d, approx in sel.candidates}
    assert by_url["https://x/new"] == (date(2026, 10, 27), False)
    assert by_url["https://x/nodate"] == (date(2026, 10, 28), True)
    assert "https://x/old" not in by_url
    assert any(r.url == "https://x/old" for r, _, _ in sel.prefiltered)   # old but newly seen: hidden with a reason
    assert any(r.url == "https://x/loc" for r, _, _ in sel.prefiltered)
    assert not any(r.url == "https://x/title" for r, _, _ in sel.prefiltered)


# ---- filters ---------------------------------------------------------------------------------------

def test_location_classification():
    assert classify_location("Sunnyvale, CA, USA")[:2] == ("match", "bay_area")
    assert classify_location("Seattle, Washington, USA · Boston, Massachusetts, USA")[0] == "match"
    assert classify_location("London, UK")[0] == "outside"
    assert classify_location("Cambridge, UK")[0] == "outside"      # Cambridge, MA only
    assert classify_location("United States")[0] == "unknown"
    assert classify_location("")[0] == "unknown"


def test_hard_filters_hide_with_reason_and_evidence():
    jd = "Qualifications:\nMBA required.\n6+ years of experience in corporate finance required.\nThis role is fully remote."
    j = _job(_row(title="Senior Finance Manager", location="London, UK"), jd)
    apply_filters(j, extract(jd, j.location))
    rules = {r.rule for r in j.reasons}
    assert {"title", "degree_required", "years_required", "metro", "remote"} <= rules
    assert all(r.text for r in j.reasons)
    assert any("6+ years" in r.evidence for r in j.reasons if r.rule == "years_required")


def test_ambiguous_cases_flag_but_do_not_hide():
    jd = "About the role\nWe like 5 years of experience.\nBachelor's degree. Master's preferred. Hybrid, 3 days in office."
    j = _job(_row(title="Senior Financial Analyst"), jd)
    apply_filters(j, extract(jd, j.location))
    assert not j.hidden
    assert any("master" in f for f in j.flags)
    assert any(f.startswith("title: senior") for f in j.flags)


def test_quant_title_hides():
    j = _job(_row(title="Quantitative Researcher"), "x" * 400)
    apply_filters(j, extract("x" * 400))
    assert any(r.rule == "quant" for r in j.reasons)


# ---- score -----------------------------------------------------------------------------------------

def test_company_priority_matches_portal_and_company_names():
    assert company_priority("Google", "YouTube") == 1
    assert company_priority("Amazon (financial analyst)", "Amazon.com Services LLC") == 1
    assert company_priority("Unknown Co", "Unknown Co") is None


def test_score_ranks_closer_fit_higher_and_stays_in_range():
    good_jd = ("Basic Qualifications:\n1+ years of finance experience. Bachelor's degree.\n"
               "Forecasting, variance analysis, pricing, Excel, SQL, P&L, financial models.")
    strong = _job(_row(title="Financial Analyst", location="San Jose, CA", posted_at="2026-10-29"), good_jd)
    weak = _job(_row(title="Senior Treasury Specialist", location="Miami, FL", posted_at="2026-10-25"),
                "Basic Qualifications:\n3+ years of experience. Bachelor's degree.")
    for j in (strong, weak):
        ext = extract(j.jd_text, j.location)
        j.company_priority = company_priority(j.portal, j.company)
        apply_filters(j, ext)
        score_job(j, ext.to_dict())
    assert 0 <= weak.score < strong.score <= 100


def test_weights_in_scoring_yaml_sum_to_100():
    assert sum(config.scoring_config()["weights"].values()) == 100
