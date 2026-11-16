"""Regression: the 17 rows on the 2026-10-29 board (real postings, JD text saved under tests/fixtures).

Guards the tightened filters. Senior titles and roles needing 3+ years must never come back, off-track titles stay
hidden, and the roles a Bachelor's-only 1.5-year candidate can realistically get stay visible. The second half runs the
career-ops gate with fakes (no network, no Claude) to prove closed postings and low-fit scores are hidden, evaluation is
cached, and the Board sorts by career-ops score then Fit Score.
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

FIX = Path(__file__).parent / "fixtures" / "regression_2026_10_29"
ROWS = json.loads((FIX / "rows.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 29)

# row -> why it must be hidden (a substring of a hidden reason)
HIDDEN_BECAUSE = {
    2: "title", 3: "title", 4: "title", 6: "title", 7: "years_required", 9: "title", 10: "title", 11: "years_required",
    12: "title", 13: "title_offtrack", 17: "title_offtrack",
}
KEPT = {1, 5, 8, 14, 15, 16}


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


def test_senior_and_3_plus_year_roles_are_hidden_with_a_reason(jobs):
    for row, rule in HIDDEN_BECAUSE.items():
        j = jobs[row]
        assert j.hidden, f"row {row} {j.title} should be hidden"
        assert any(r.rule == rule for r in j.reasons), f"row {row}: expected a {rule} reason, got {[r.rule for r in j.reasons]}"
        assert all(r.text for r in j.reasons)


def test_expected_roles_stay_visible(jobs):
    for row in KEPT:
        assert not jobs[row].hidden, f"row {row} {jobs[row].title} hidden: {[r.text for r in jobs[row].reasons]}"


def test_nothing_senior_or_3_plus_years_is_ever_shown(jobs):
    for row, j in jobs.items():
        if j.hidden:
            continue
        assert not any(w in j.title.lower() for w in ("senior", "sr.", "lead", "principal", "staff", "manager", "director")), j.title
        assert (j.years_effective or 0) <= config.filters_config()["max_required_years"], (row, j.years_effective)


def test_years_flags(jobs):
    assert any(f.startswith("stretch: 2") for f in jobs[5].flags)                  # Google Strategic: 2 years
    assert any(f.startswith("stretch: 2") for f in jobs[15].flags)                 # Northern Trust: 2+ years
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
    certs = {c["cert"]: c["status"] for c in j.extraction["certs"]} if j.extraction else {}
    assert not any(r.rule == "license_required" for r in j.reasons)


def test_jpmorgan_series_86_87_are_flagged_not_hidden(jobs):
    j = jobs[16]
    assert not j.hidden and any("licenses listed as required" in f and "Series 86" in f for f in j.flags)
    assert any("CFA preferred" in f for f in j.flags)


def test_filter_reasons_carry_the_sentence(jobs):
    assert "3+ years" in next(r.evidence for r in jobs[7].reasons if r.rule == "years_required")
    assert "Account Executive" in next(r.evidence for r in jobs[17].reasons if r.rule == "title_offtrack")


@pytest.mark.parametrize("title,hidden", [
    ("Financial Analyst II", False), ("Financial Analyst III", True), ("Finance Analyst IV", True),
    ("Senior Financial Analyst", True), ("Sr. FP&A Analyst", True), ("Lead Pricing Analyst", True),
    ("Principal Financial Analyst", True), ("Staff Finance Analyst", True), ("FP&A Manager", True),
    ("Director of FP&A", True), ("VP, Finance", True), ("Head of Pricing", True),
    ("Pricing Analyst", False), ("Deal Desk Analyst", False), ("Equity Research Associate", False), ("Investment Analyst", False),
    ("Revenue Analyst", False), ("Analyst, FP&A", False), ("Finance Associate", False),
    ("Business Intelligence Analyst - Corporate Finance", True), ("Account Executive, Finance", True),
    ("Software Engineer, Finance Systems", True), ("Sales Analyst", True),
])
def test_title_rules(title, hidden):
    row = HistoryRow(url="u", title=title, company="Acme", location="San Jose, CA", status="added")
    j = Job(id="t", row=row, company="Acme", title=title, location="San Jose, CA", url="u", portal="Acme", date_used=TODAY,
            date_approx=False, age_days=0, jd_text="Basic Qualifications:\n- 1 year of experience.", jd_source="fetch-jd")
    apply_filters(j, extract(j.jd_text, j.location))
    assert bool([r for r in j.reasons if r.rule in ("title", "title_offtrack")]) == hidden, title


@pytest.mark.parametrize("text,hidden", [
    ("Basic Qualifications:\n- 3+ years of experience in finance.", True),
    ("Basic Qualifications:\n- 2-4 years of experience in finance.", False),         # 2-4 means a minimum of 2: stretch
    ("Basic Qualifications:\n- Bachelor's degree and 3 years of experience, or Master's degree and 1 year of experience.", True),
    ("Basic Qualifications:\n- 1 year of experience.\nPreferred Qualifications:\n- 5+ years of experience.", False),
    ("Basic Qualifications:\n- MBA required.", True),
    ("Basic Qualifications:\n- CPA license required.", True),
    ("Basic Qualifications:\n- CFA charterholder required.", True),
    ("Preferred Qualifications:\n- CFA or MBA preferred.", False),
    ("Basic Qualifications:\n- Active Series 7 and Series 63 licenses are required prior to start.", True),
    ("Basic Qualifications:\n- SIE and Series 86/87 must be obtained within 120 days of hire.", False),
])
def test_experience_degree_and_license_rules(text, hidden):
    row = HistoryRow(url="u", title="Financial Analyst", company="Acme", location="San Jose, CA", status="added")
    j = Job(id="t", row=row, company="Acme", title="Financial Analyst", location="San Jose, CA", url="u", portal="Acme",
            date_used=TODAY, date_approx=False, age_days=0, jd_text=text, jd_source="fetch-jd")
    apply_filters(j, extract(text, j.location))
    assert j.hidden == hidden, [r.text for r in j.reasons]


@pytest.mark.parametrize("text,hidden", [
    ("Basic Qualifications:\n- Graduating between December 2025 and August 2026 with a Bachelor's degree.", True),
    ("Basic Qualifications:\n- Class of 2026 graduates.", True),
    ("Basic Qualifications:\n- Graduated between January 2024 and August 2025.", False),
    ("Basic Qualifications:\n- Bachelor's degree. Open to recent graduates.", False),
    ("Basic Qualifications:\n- Must have graduated on or after June 2025.", True),
])
def test_new_grad_window_excludes_only_when_clear(text, hidden):
    title = "Associate Finance Analyst, University Graduate"
    row = HistoryRow(url="u", title=title, company="Acme", location="San Jose, CA", status="added")
    j = Job(id="t", row=row, company="Acme", title=title, location="San Jose, CA", url="u", portal="Acme", date_used=TODAY,
            date_approx=False, age_days=0, jd_text=text, jd_source="fetch-jd")
    apply_filters(j, extract(text, j.location))
    assert any(r.rule == "grad_window" for r in j.reasons) == hidden, [r.text for r in j.reasons]


# ---------------------------------------------------------------------------------------------------------------------
# the career-ops gate, with fakes
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    cols = ["url", "first_seen", "portal", "title", "company", "status", "location", "fingerprint", "posted_at", "trust_score",
            "trust_flags", "normalized_company", "requisition_id", "language", "listing_key"]
    lines = ["\t".join(cols)]
    by_row = {}
    for r in ROWS:
        lines.append("\t".join([r["url"], "2026-10-29", r["company"], r["title"], r["company"], "added", r["location"]] + [""] * 8))
        h = HistoryRow(url=r["url"])
        key = ingest.job_id(h)
        by_row[r["row"]] = key
        folder = tmp_path / "jobs" / f"{key}_x"
        folder.mkdir(parents=True)
        (folder / "JD.md").write_text("# t\n\n- Source: fetch-jd\n\n---\n" + jd_text(r["row"]), encoding="utf-8")
    hist = tmp_path / "scan-history.tsv"
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path, hist, by_row


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
    monkeypatch.setattr(pipeline.Gate, "classify", staticmethod(lambda items, cache, cfg: ({}, {"titles": 0, "calls": 0})))
    monkeypatch.setattr(pipeline.Gate, "liveness", staticmethod(lambda urls: {u: ("expired" if url_row.get(u) in expired else "active") for u in urls}))
    monkeypatch.setattr(pipeline.Gate, "keywords", staticmethod(lambda text: {"total": 5, "coveragePct": 60, "missing": []}))
    jd_row = {jd_text(r["row"]).strip(): r["row"] for r in ROWS}
    monkeypatch.setattr(pipeline.Gate, "years", staticmethod(lambda text: (years or {}).get(jd_row.get(text.strip()))))


def test_gate_hides_closed_and_low_fit_and_sorts_by_careerops_score(world, monkeypatch):
    tmp_path, hist, by_row = world
    calls: list[int] = []
    install_fakes(monkeypatch, tmp_path, by_row, scores={1: 4.2, 5: 3.6, 8: 3.4, 14: 2.6, 16: 3.0}, expired={15}, calls=calls)
    res = pipeline.run(today=TODAY, history=hist, fetch=False, out_dir=tmp_path / "out", state_dir=tmp_path / "state")

    shown = [(j["company"], j["title"][:20], j["careerops_score"]) for j in res["shown"]]
    assert [s[2] for s in shown] == [4.2, 3.6, 3.4, 3.0]                              # sorted by career-ops score
    assert sorted(calls) == [1, 5, 8, 14, 16]                                       # hidden and closed rows are never evaluated
    why = {j["title"]: [r["text"] for r in j["reasons"]] for j in res["hidden"]}
    assert any("low fit (2.6/5" in t for t in why["Equity Research Associate, Software - Small & Mid-Cap"])
    assert any("posting is closed" in t for t in why["Investment Analyst"])
    assert res["gate"]["closed"] == 1 and res["gate"]["low_fit"] == 1 and res["gate"]["evaluated"] == 5

    # a second run uses the cache: no evaluation calls
    calls.clear()
    res2 = pipeline.run(today=TODAY, history=hist, fetch=False, out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    assert calls == [] and res2["gate"]["cached"] == 5 and [j["careerops_score"] for j in res2["shown"]] == [4.2, 3.6, 3.4, 3.0]


def test_careerops_years_higher_than_ours_is_used_and_flagged(world, monkeypatch):
    tmp_path, hist, by_row = world
    install_fakes(monkeypatch, tmp_path, by_row, scores={1: 4.0, 5: 4.0, 14: 4.0, 15: 4.0, 16: 4.0},
                  years={8: {"status": "stated", "min": 4, "confident": True, "display": "4+"},       # we read 1; career-ops reads 4
                         5: {"status": "stated", "min": 1, "confident": True, "display": "1+"}},      # we read 2; keep the higher
                  calls=[])
    res = pipeline.run(today=TODAY, history=hist, fetch=False, out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    hidden = {j["title"]: j for j in res["hidden"]}
    assert any("4+ years required" in r["text"] for r in hidden["Finance Analyst, Advertising Finance"]["reasons"])
    assert any("years differ" in f for f in hidden["Finance Analyst, Advertising Finance"]["flags"])
    kept = {j["title"]: j for j in res["shown"]}
    five = kept["Strategic Financial Analyst, Pixel Product Finance"]
    assert any("years differ: board 2, career-ops 1" in f for f in five["flags"]) and five["years_required"] == 2


def test_excel_has_careerops_columns_and_orders_by_them(world, monkeypatch):
    tmp_path, hist, by_row = world
    install_fakes(monkeypatch, tmp_path, by_row, scores={1: 3.2, 5: 4.4, 14: 3.9, 15: 3.5, 16: 3.1, 8: 3.0}, calls=[])
    res = pipeline.run(today=TODAY, history=hist, fetch=False, out_dir=tmp_path / "out", state_dir=tmp_path / "state")
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
    tmp_path, hist, by_row = world
    install_fakes(monkeypatch, tmp_path, by_row, scores={}, calls=[])
    monkeypatch.setattr(pipeline.Gate, "evaluate", staticmethod(lambda job: {"error": "claude command not found on PATH"}))
    res = pipeline.run(today=TODAY, history=hist, fetch=False, out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    assert {r["title"] for r in res["shown"]} >= {"Investment Analyst", "Finance Analyst, Advertising Finance"}
    assert all("career-ops evaluation unavailable" in j["flags"] for j in res["shown"])
    assert len(res["gate"]["errors"]) == len(res["shown"])


def test_careerops_score_is_read_from_the_report_header(tmp_path):
    report = tmp_path / "001-acme-2026-10-29.md"
    report.write_text("# Evaluation\n\n**Date:** 2026-10-29\n**Score:** 3.8/5\n**Decision:** Consider\n", encoding="utf-8")
    assert careerops.read_report_score(report) == 3.8
    report.write_text("no score here", encoding="utf-8")
    assert careerops.read_report_score(report) is None
