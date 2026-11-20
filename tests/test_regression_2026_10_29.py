"""Regression: the 17 rows on the 2026-10-29 board (real postings, JD text saved under tests/fixtures).

Re-baselined 2026-11-15 for the new rules: required years up to 3 are kept (3 is "stretch"), a Senior title is kept up to
4 years, and sales-like / wrong-function / off-track titles stay hidden. The 3-year Senior Amazon roles that the old
2-year rule hid are now expected on the Board. The second half runs the optional career-ops gate (`with_claude`) with
fakes (no network, no Claude) to prove closed postings and low-fit scores are hidden, evaluation is cached, and the
Board sorts by career-ops score then Fit Score.
"""
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from board import careerops, config, excel, ingest, pipeline
from board.extract import extract
from board.filters import apply_filters
from board.models import HistoryRow, Job

from .helpers import raw_job, run_jobs

FIX = Path(__file__).parent / "fixtures" / "regression_2026_10_29"
ROWS = json.loads((FIX / "rows.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 29)

# row -> the hidden reason rule it must carry (rows not listed here must stay visible)
HIDDEN_BECAUSE = {11: "wrong_function", 13: "title_offtrack", 17: "sales_like"}
KEPT = {r["row"] for r in ROWS} - set(HIDDEN_BECAUSE)
SENIOR_ROWS = {2, 3, 4, 6, 9, 10, 12}                      # all need 3 years, so a Senior title is fine (limit 4)


def jd_text(row: int) -> str:
    return (FIX / "jd" / f"{row:02d}.md").read_text(encoding="utf-8")


def evaluate_row(r: dict) -> Job:
    row = HistoryRow(url=r["url"], first_seen="2026-10-29", portal=r["company"], title=r["title"], company=r["company"],
                     status="added", location=r["location"])
    job = Job(id=ingest.job_id(row), row=row, company=r["company"], title=r["title"], location=r["location"], url=r["url"],
              portal=r["company"], date_used=TODAY, date_approx=True, age_days=0, jd_text=jd_text(r["row"]), jd_source="fetch-jd")
    apply_filters(job, extract(job.jd_text, job.location))
    return job


@pytest.fixture(scope="module")
def jobs() -> dict[int, Job]:
    return {r["row"]: evaluate_row(r) for r in ROWS}


def test_fixture_matches_the_rows_you_listed():
    assert len(ROWS) == 17 and {r["row"] for r in ROWS if r["expect"] == "kept"} == KEPT


def test_off_track_wrong_function_and_sales_roles_are_hidden_with_a_reason(jobs):
    for row, rule in HIDDEN_BECAUSE.items():
        j = jobs[row]
        assert j.hidden, f"row {row} {j.title} should be hidden"
        assert any(r.rule == rule for r in j.reasons), f"row {row}: expected a {rule} reason, got {[r.rule for r in j.reasons]}"
        assert all(r.text for r in j.reasons)


def test_expected_roles_stay_visible(jobs):
    for row in KEPT:
        assert not jobs[row].hidden, f"row {row} {jobs[row].title} hidden: {[r.text for r in jobs[row].reasons]}"


def test_senior_rows_with_3_years_are_kept_and_flagged(jobs):
    for row in SENIOR_ROWS:
        j = jobs[row]
        assert not j.hidden and j.years_effective == 3, (row, j.years_effective)
        assert any(f.startswith("senior title") for f in j.flags), (row, j.flags)


def test_nothing_shown_exceeds_the_years_limit_or_has_a_managerial_title(jobs):
    cfg = config.filters_config()
    for row, j in jobs.items():
        if j.hidden:
            continue
        assert not any(w in j.title.lower() for w in ("principal", "staff", "manager", "director", "lead")), j.title
        senior = any(w in j.title.lower() for w in ("senior", "sr."))
        limit = cfg["senior_max_required_years"] if senior else cfg["max_required_years"]
        assert (j.years_effective or 0) <= limit, (row, j.years_effective)


def test_years_flags(jobs):
    assert any(f.startswith("stretch: 3") for f in jobs[7].flags)                   # Amazon Benefits: 3+ years is the limit
    assert not any("stretch" in f for f in jobs[5].flags)                            # Google Strategic: 2 years is inside it
    assert jobs[8].years_effective == 1 and not any("stretch" in f for f in jobs[8].flags)      # Amazon Advertising: 1+ year
    assert "years not stated" in jobs[1].flags                                      # Google new grad
    assert jobs[11].years_effective == 3 and jobs[7].years_effective == 3


def test_new_grad_row_is_kept_with_an_unstated_window_flag(jobs):
    assert "new-grad window not stated" in jobs[1].flags        # the JD names no graduation window, so it cannot exclude you


def test_wells_fargo_licenses_are_sponsored_not_required_before_hire(jobs):
    j = jobs[14]
    assert not j.hidden
    sponsored = next(f for f in j.flags if f.startswith("licenses sponsored"))
    for lic in ("SIE", "Series 63", "Series 66", "Series 86", "Series 87"):
        assert lic in sponsored
    # Series 65/66 appear only as "prior to conducting Investment Advisory business", so Series 65 is not a hiring condition
    assert not any(r.rule == "license_required" for r in j.reasons)


def test_jpmorgan_series_86_87_are_flagged_not_hidden(jobs):
    j = jobs[16]
    assert not j.hidden and any("licenses listed as required" in f and "Series 86" in f for f in j.flags)
    assert any("CFA preferred" in f for f in j.flags)


def test_filter_reasons_carry_the_sentence(jobs):
    assert "Account Executive" in next(r.evidence for r in jobs[17].reasons if r.rule == "sales_like")
    assert "Accounting" in next(r.text for r in jobs[11].reasons if r.rule == "wrong_function")


TITLE_RULES = ("title", "title_offtrack", "sales_like", "moves_backward", "wrong_function", "client_title")


@pytest.mark.parametrize("title,hidden", [
    ("Financial Analyst II", False), ("Financial Analyst III", True), ("Finance Analyst IV", True),
    ("Senior Financial Analyst", False), ("Sr. FP&A Analyst", False),             # decided by the years rule now
    ("Lead Pricing Analyst", True),
    ("Principal Financial Analyst", True), ("Staff Finance Analyst", True), ("FP&A Manager", True),
    ("Director of FP&A", True), ("VP, Finance", True), ("Head of Pricing", True),
    ("Pricing Analyst", False), ("Deal Desk Analyst", False), ("Equity Research Associate", False), ("Investment Analyst", False),
    ("Revenue Analyst", False), ("Analyst, FP&A", False), ("Finance Associate", False),
    ("Business Intelligence Analyst - Corporate Finance", True), ("Account Executive, Finance", True),
    ("Software Engineer, Finance Systems", True), ("Sales Analyst", True),
    ("Asset Management - Everyday 401K Internal Client Advisor - Associate", True),                    # JPMorgan, from the 2026-11-15 smoke run
    ("Asset Management - Internal Client Advisor - Retirement Plan Specialist - Associate", True),     # JPMorgan, from the same run
    ("Wealth Advisor Associate", True), ("Client Service Associate", True), ("Relationship Manager, Private Bank", True),
    ("Cost Estimator", True), ("Proposal Pricing Analyst", True), ("EVM Analyst", True),
    ("Credit Underwriter I/Credit Officer I-Technology", True),            # Bank of America, from the 2026-11-15 trial run
    ("Credit Risk Analyst", True), ("Loan Underwriting Analyst", True), ("Credit Officer", True), ("Credit Analyst", True),
    ("Tax Analyst", True), ("Accounts Payable Analyst", True), ("AML Analyst", True), ("Staff Accountant", True),
    ("Insurance Financial Analyst", True),
    ("Asset Management Analyst", False), ("Wealth Analyst", False),
])
def test_title_rules(title, hidden):
    row = HistoryRow(url="u", title=title, company="Acme", location="San Jose, CA", status="added")
    j = Job(id="t", row=row, company="Acme", title=title, location="San Jose, CA", url="u", portal="Acme", date_used=TODAY,
            date_approx=False, age_days=0, jd_text="Basic Qualifications:\n- 1 year of experience.", jd_source="fetch-jd")
    apply_filters(j, extract(j.jd_text, j.location))
    assert bool([r for r in j.reasons if r.rule in TITLE_RULES]) == hidden, (title, [r.text for r in j.reasons])


def _job(title, text, company="Acme"):
    row = HistoryRow(url="u", title=title, company=company, location="San Jose, CA", status="added")
    j = Job(id="t", row=row, company=company, title=title, location="San Jose, CA", url="u", portal=company,
            date_used=TODAY, date_approx=False, age_days=0, jd_text=text, jd_source="fetch-jd")
    apply_filters(j, extract(text, j.location))
    return j


@pytest.mark.parametrize("text,hidden", [
    ("Basic Qualifications:\n- 3+ years of experience in finance.", False),         # 3 is the limit: kept, flagged stretch
    ("Basic Qualifications:\n- 4+ years of experience in finance.", True),
    ("Basic Qualifications:\n- 5+ years of experience in finance.", True),
    ("Basic Qualifications:\n- 2-4 years of experience in finance.", False),         # 2-4 means a minimum of 2
    ("Basic Qualifications:\n- Bachelor's degree and 3 years of experience, or Master's degree and 1 year of experience.", False),
    ("Basic Qualifications:\n- Bachelor's degree and 4 years of experience, or Master's degree and 1 year of experience.", True),
    ("Basic Qualifications:\n- 1 year of experience.\nPreferred Qualifications:\n- 5+ years of experience.", False),
    ("Basic Qualifications:\n- MBA required.", True),
    ("Basic Qualifications:\n- CPA license required.", True),
    ("Basic Qualifications:\n- CFA charterholder required.", True),
    ("Preferred Qualifications:\n- CFA or MBA preferred.", False),
    ("Basic Qualifications:\n- Active Series 7 and Series 63 licenses are required prior to start.", True),
    ("Basic Qualifications:\n- SIE and Series 86/87 must be obtained within 120 days of hire.", False),
])
def test_experience_degree_and_license_rules(text, hidden):
    j = _job("Financial Analyst", text)
    assert j.hidden == hidden, [r.text for r in j.reasons]


@pytest.mark.parametrize("years,hidden", [(3, False), (4, False), (5, True), (6, True)])
def test_senior_titles_are_kept_only_up_to_4_years(years, hidden):
    j = _job("Senior Financial Analyst", f"Basic Qualifications:\n- {years}+ years of experience in finance.")
    assert j.hidden == hidden and any(r.rule == "years_required" for r in j.reasons) == hidden
    assert any(f.startswith("senior title") for f in j.flags)


def test_senior_title_with_years_not_stated_is_kept_and_flagged():
    j = _job("Senior Financial Analyst", "We are a fast growing company.")
    assert not j.hidden and "years not stated" in j.flags


@pytest.mark.parametrize("company,text,rule", [
    ("Robert Half", "Basic Qualifications:\n- 1 year of experience.", "staffing"),
    ("Acme", "Our client, a leading bank, is seeking an analyst.", "staffing"),
    ("Acme", "This is a contract-to-hire position.", "staffing"),
    ("Acme", "Basic Qualifications:\n- 1 year of experience.", None),
])
def test_staffing_agencies_and_contract_postings_are_hidden(company, text, rule):
    j = _job("Financial Analyst", text, company)
    assert (rule in [r.rule for r in j.reasons]) == bool(rule), [r.text for r in j.reasons]


@pytest.mark.parametrize("title,text,hidden", [
    ("Cost Analyst", "You will price proposals under FAR and DFARS for government contracts.", True),
    ("Pricing Analyst", "Support DCAA audits and government contract pricing.", True),
    ("Pricing Analyst", "Own SaaS pricing experiments and packaging for enterprise customers.", False),
    ("Financial Analyst", "You will support FAR compliant proposals.", False),         # only cost / pricing titles are judged on the JD
])
def test_moves_backward_government_contract_pricing_is_hidden(title, text, hidden):
    j = _job(title, text)
    assert any(r.rule == "moves_backward" for r in j.reasons) == hidden, [r.text for r in j.reasons]


@pytest.mark.parametrize("text,hidden", [
    ("Basic Qualifications:\n- Graduating between December 2025 and August 2026 with a Bachelor's degree.", True),
    ("Basic Qualifications:\n- Class of 2026 graduates.", True),
    ("Basic Qualifications:\n- Graduated between January 2024 and August 2025.", False),
    ("Basic Qualifications:\n- Bachelor's degree. Open to recent graduates.", False),
    ("Basic Qualifications:\n- Must have graduated on or after June 2025.", True),
])
def test_new_grad_window_excludes_only_when_clear(text, hidden):
    j = _job("Associate Finance Analyst, University Graduate", text)
    assert any(r.rule == "grad_window" for r in j.reasons) == hidden, [r.text for r in j.reasons]


# ---------------------------------------------------------------------------------------------------------------------
# the optional career-ops gate (--with-claude), with fakes
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    postings = [raw_job(r["company"], r["title"], r["location"], r["url"], jd_text(r["row"]), day=TODAY) for r in ROWS]
    by_row = {r["row"]: ingest.job_id(HistoryRow(url=r["url"])) for r in ROWS}
    return tmp_path, postings, by_row


def run_gate(world, monkeypatch, **kw):
    tmp_path, postings, _by_row = world
    return run_jobs(tmp_path, monkeypatch, postings, TODAY, with_claude=True, **kw)


def install_fakes(monkeypatch, tmp_path, by_row, scores, expired=(), years=None, calls=None):
    key_to_row = {v: k for k, v in by_row.items()}

    def evaluate(job):
        row = key_to_row.get(job["key"], 0)                 # 0: a posting that is not one of the 17 fixture rows
        calls.append(row)
        report = tmp_path / f"report_{row}_{job['key']}.md"
        report.write_text("**Score:** x/5", encoding="utf-8")
        return {"score": scores.get(row, 3.5), "report": str(report)}

    url_row = {r["url"]: r["row"] for r in ROWS}
    monkeypatch.setattr(pipeline.Gate, "evaluate", staticmethod(evaluate))
    monkeypatch.setattr(pipeline.Gate, "liveness", staticmethod(lambda urls: {u: ("expired" if url_row.get(u) in expired else "active") for u in urls}))
    monkeypatch.setattr(pipeline.Gate, "keywords", staticmethod(lambda text: {"total": 5, "coveragePct": 60, "missing": []}))
    jd_row = {jd_text(r["row"]).strip(): r["row"] for r in ROWS}
    monkeypatch.setattr(pipeline.Gate, "years", staticmethod(lambda text: (years or {}).get(jd_row.get(text.strip()))))


def test_gate_hides_closed_and_low_fit_and_sorts_by_careerops_score(world, monkeypatch):
    tmp_path, _postings, by_row = world
    calls: list[int] = []
    install_fakes(monkeypatch, tmp_path, by_row, scores={1: 4.2, 5: 3.6, 8: 3.4, 14: 2.6, 16: 3.0}, expired={15}, calls=calls)
    res = run_gate(world, monkeypatch)

    scores = [j["careerops_score"] for j in res["shown"]]
    assert scores == sorted(scores, reverse=True) and scores[0] == 4.2                # sorted by career-ops score
    assert sorted(calls) == sorted(KEPT - {15})                                      # hidden and closed rows are never evaluated
    why = {j["title"]: [r["text"] for r in j["reasons"]] for j in res["hidden"]}
    assert any("low fit (2.6/5" in t for t in why["Equity Research Associate, Software - Small & Mid-Cap"])
    assert any("posting is closed" in t for t in why["Investment Analyst"])
    assert res["gate"]["closed"] == 1 and res["gate"]["low_fit"] == 1 and res["gate"]["evaluated"] == len(KEPT) - 1

    # a second run uses the cache: no evaluation calls
    calls.clear()
    res2 = run_gate(world, monkeypatch)
    assert calls == [] and res2["gate"]["cached"] == len(KEPT) - 1 and [j["careerops_score"] for j in res2["shown"]] == scores


def test_careerops_years_higher_than_ours_is_used_and_flagged(world, monkeypatch):
    tmp_path, _postings, by_row = world
    install_fakes(monkeypatch, tmp_path, by_row, scores={1: 4.0, 5: 4.0, 14: 4.0, 15: 4.0, 16: 4.0},
                  years={8: {"status": "stated", "min": 4, "confident": True, "display": "4+"},       # we read 1; career-ops reads 4
                         5: {"status": "stated", "min": 1, "confident": True, "display": "1+"}},      # we read 2; keep the higher
                  calls=[])
    res = run_gate(world, monkeypatch)
    hidden = {j["title"]: j for j in res["hidden"]}
    assert any("4+ years required" in r["text"] for r in hidden["Finance Analyst, Advertising Finance"]["reasons"])
    assert any("years differ" in f for f in hidden["Finance Analyst, Advertising Finance"]["flags"])
    kept = {j["title"]: j for j in res["shown"]}
    five = kept["Strategic Financial Analyst, Pixel Product Finance"]
    assert any("years differ: board 2, career-ops 1" in f for f in five["flags"]) and five["years_required"] == 2


def test_excel_has_careerops_columns_and_orders_by_them(world, monkeypatch):
    tmp_path, _postings, by_row = world
    install_fakes(monkeypatch, tmp_path, by_row, scores={1: 3.2, 5: 4.4, 14: 3.9, 15: 3.5, 16: 3.1, 8: 3.0}, calls=[])
    res = run_gate(world, monkeypatch)
    out = excel.write_outputs(res, now=datetime(2026, 10, 29, 9, 0), out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    ws = load_workbook(out["master"])["Board"]
    headers = [c.value for c in ws[1]]
    assert headers.index("Career-ops score") == headers.index("Fit Score (0-100)") + 1 and "Report" in headers
    cs = headers.index("Career-ops score") + 1
    got = [ws.cell(row=r, column=cs).value for r in range(2, ws.max_row + 1)]
    assert got == sorted(got, reverse=True) and got[0] == 4.4
    rc = headers.index("Report") + 1
    assert ws.cell(row=2, column=rc).hyperlink.target.endswith(".md") and ws.cell(row=2, column=rc).value == "Report"
    hidden = load_workbook(out["master"])["Hidden"]
    hh = [c.value for c in hidden[1]]
    assert {"Career-ops score", "Report", "Hidden Reason"} <= set(hh)


def test_gate_failure_never_hides_a_row(world, monkeypatch):
    tmp_path, _postings, by_row = world
    install_fakes(monkeypatch, tmp_path, by_row, scores={}, calls=[])
    monkeypatch.setattr(pipeline.Gate, "evaluate", staticmethod(lambda job: {"error": "claude command not found on PATH"}))
    res = run_gate(world, monkeypatch)
    assert {r["title"] for r in res["shown"]} >= {"Investment Analyst", "Finance Analyst, Advertising Finance"}
    assert all("career-ops evaluation unavailable" in j["flags"] for j in res["shown"])
    assert len(res["gate"]["errors"]) == len(res["shown"])


def test_careerops_score_is_read_from_the_report_header(tmp_path):
    report = tmp_path / "001-acme-2026-10-29.md"
    report.write_text("# Evaluation\n\n**Date:** 2026-10-29\n**Score:** 3.8/5\n**Decision:** Consider\n", encoding="utf-8")
    assert careerops.read_report_score(report) == 3.8
    report.write_text("no score here", encoding="utf-8")
    assert careerops.read_report_score(report) is None


# ---- 2026-11-16 decisions: analyst combo rule, wrong function, investment banking track, Bay Area locations -----------------

from board.filters import classify_location, title_is_finance_role, title_blocked   # noqa: E402


@pytest.mark.parametrize("title,ok", [
    ("Revenue Operations Analyst", True), ("Valuation Associate", True), ("Capital Planning Analyst", True),
    ("Strategy & Finance Associate", True), ("Treasury Associate", True), ("Business Analyst, Finance", True),
    ("Analyst - Business Intelligence, Compliance and Risk", False),        # from the 2026-11-16 audit
    ("Senior Analyst, Global Research & Design", False),
    ("Associate, Global Swag & Event Operations - Marketing", False),
    ("Business Analyst", False), ("Analyst", False), ("Associate", False), ("Consultant", False), ("Investment Professional", False),
    ("Data Analyst, Finance Systems", False),
])
def test_analyst_combo_rule(title, ok):
    assert title_is_finance_role(title, config.filters_config()) == ok, title


@pytest.mark.parametrize("title", [
    "Credit Analyst - BMO Asset Management Corp", "Securitization & Capital Call Underwriting Analyst",       # from the 2026-11-16 top 10
    "Credit Analyst", "Credit Risk Analyst", "Loan Analyst", "Underwriting Analyst", "Senior Underwriter",
    "Credit Portfolio Analyst II",                                                                            # Citi, from the 2026-11-17 board
    "Commercial Associate – Global Commercial Banking - Dealer Financial Services - (Dallas, Chicago, Houston, Charlotte)",  # BofA, 2026-11-19 board
    "Commercial Banking Analyst", "Relationship Banking Associate",
])
def test_credit_securitization_and_underwriting_are_wrong_function(title):
    got = title_blocked(title)
    assert got and got[0] == "wrong_function", (title, got)


@pytest.mark.parametrize("title,blocked", [
    ("Prime Financial Services Client Onboarding Associate", True),             # JPMorgan, from the 2026-11-16 top 10
    ("Client Onboarding Analyst", True), ("Client Reporting Associate", True), ("Client Operations Analyst", True),
    ("Client Finance Analyst", False), ("Financial Analyst, Client Reporting", False), ("Client Pricing Analyst", False),
    ("Treasury Analyst, Client Solutions", False), ("Revenue Analyst", False),
])
def test_client_titles_are_blocked_unless_they_name_a_finance_function(title, blocked):
    got = title_blocked(title)
    assert (got is not None and got[0] == "client_title") == blocked, (title, got)
    if blocked:
        j = _job(title, "Basic Qualifications:\n- 1 year of experience.", "JPMorgan Chase")
        assert any(r.rule == "client_title" for r in j.reasons)


def test_investment_banking_analysts_stay_on_the_board_as_track_d():
    from board import score
    for title in ("Analyst - Restructuring & Special Situations", "M&A Analyst", "Investment Banking Analyst, Healthcare Coverage",
                  "Coverage Analyst, Technology"):
        assert title_is_finance_role(title, config.filters_config()), title
        t = score.track_for(title)
        assert (t["letter"], t["points"]) == ("D", 25), (title, t)
    j = _job("Analyst - Restructuring & Special Situations", "Basic Qualifications:\n- 1 year of experience.", "PJT Partners")
    score.score_job(j, extract(j.jd_text, j.location).to_dict())
    assert not j.hidden and j.score_parts["role_fit"] == 25 and j.score_parts["track_letter"] == "D"
    assert score.track_for("Strategic Finance Analyst")["letter"] == "B"                                       # others unchanged


@pytest.mark.parametrize("loc,metro", [
    ("San Francisco, CA", "bay_area"), ("SF Bay Area", "bay_area"), ("Hybrid - San Francisco", "bay_area"),
    ("Remote/San Francisco", "bay_area"), ("SF", "bay_area"), ("US-SF, US-NY", "bay_area"), ("SF, SEA, NY, Remote-US", "bay_area"),
    ("Daly City, CA", "bay_area"), ("San Rafael, CA", "bay_area"), ("Livermore, CA", "bay_area"), ("US-CA-Dublin", "bay_area"),
    ("Bay Point, CA; Petaluma, CA", "bay_area"), ("Hawthorne, CA", "los_angeles"), ("Long Beach, CA", "los_angeles"),
    ("Union City, GA", None), ("Brisbane, Australia", None), ("Dublin, Ireland", None), ("Newark, NJ", None), ("Toronto, ON, CA", None),
])
def test_bay_area_and_la_location_parsing(loc, metro):
    status, key, _ = classify_location(loc)
    assert (key if status == "match" else None) == metro, (loc, status, key)


@pytest.mark.parametrize("location,jd,hidden", [
    ("Hybrid - San Francisco", "This role is hybrid: three days a week in our San Francisco office.", False),
    ("Remote/San Francisco", "This position is hybrid, three days a week in the office.", False),
    ("Remote/San Francisco", "This is a fully remote position. You may work from anywhere in the US.", True),
    ("Remote", "This is a fully remote position.", True),
    ("SF Bay Area", "Basic Qualifications:\n- 1 year of experience.", False),
])
def test_remote_is_dropped_and_hybrid_is_kept_for_bay_area_postings(location, jd, hidden):
    row = HistoryRow(url="u", title="Financial Analyst", company="Acme", location=location, status="added")
    j = Job(id="t", row=row, company="Acme", title="Financial Analyst", location=location, url="u", portal="Acme", date_used=TODAY,
            date_approx=False, age_days=0, jd_text=jd, jd_source="api")
    apply_filters(j, extract(jd, location))
    assert j.hidden == hidden, [r.text for r in j.reasons]
