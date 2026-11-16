"""The negative-filter funnel: title rules, finance relevance, per-step counts, the safety nets, and the 17-row regression
run end to end through steps 1-7 (fakes for career-ops and Claude; no network)."""
import csv
import json
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from board import config, excel, funnel, ingest, pipeline
from board.models import HistoryRow

from .test_regression_2026_10_29 import FIX, KEPT, ROWS, TODAY, install_fakes, jd_text

NEG = funnel.NegativeTitles()
REL = funnel.FinanceRelevance()


def posting(title="Financial Analyst", location="San Jose, CA", posted="2026-10-29", first_seen="2026-10-29", url=None,
            portal="Acme"):
    row = HistoryRow(url=url or f"https://x.example/{abs(hash((title, location)))}", first_seen=first_seen, portal=portal,
                     title=title, company=portal, status="added", location=location, posted_at=posted)
    return funnel.Posting(row=row, key=ingest.row_key(row), pid=ingest.job_id(row), company=portal)


# ---- step 4: negative titles ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Senior Financial Analyst", "Sr. Financial Analyst", "Sr Analyst, FP&A", "Lead FP&A Analyst", "Principal Pricing Analyst",
    "Staff Finance Analyst", "Finance Manager", "FP&A Mgr", "Director, Corporate Finance", "Head of Pricing", "VP Finance",
    "Vice President, Treasury", "President's Office Analyst", "Chief of Staff, Finance", "Compliance Officer",
    "Partner Success Analyst", "Financial Analyst III", "Financial Analyst IV", "Software Engineer, Finance Systems",
    "Finance Engineering Analyst", "Data Scientist, Finance", "Solutions Architect", "Fulfillment Associate",
    "Warehouse Associate", "Sales Representative", "Account Executive, Corporate Finance", "Account Manager",
    "Technical Recruiter", "Corporate Counsel", "Paralegal", "Finance Intern", "Summer Internship, FP&A", "Co-op, Finance",
    "Registered Nurse", "Delivery Driver", "Product Designer",
])
def test_step4_drops(title):
    assert NEG.hit(title), title


@pytest.mark.parametrize("title", [
    "Leadership Development Program, Finance",         # "lead" is a whole word only
    "Financial Analyst II", "Finance Business Partner", "Business Partner, Finance", "Investor Relations Associate",
    "Equity Research Associate, Software - Small & Mid-Cap",     # a sector, not the job
    "Warehouse Finance Analyst", "Ahead Analyst, Pricing", "Associate Finance Analyst, University Graduate",
    "Analyst, Corporate Development", "Treasury Analyst",
])
def test_step4_keeps(title):
    assert NEG.hit(title) is None, (title, NEG.hit(title))


# ---- step 5: finance relevance ---------------------------------------------------------------------------------------

UNUSUAL_FINANCE_TITLES = [
    "Analyst, Corporate Development", "Strategy & Operations Analyst", "Treasury Analyst", "Investor Relations Associate",
    "Business Operations Analyst, Finance", "Credit Analyst", "Valuation Analyst", "M&A Analyst",
    "Commercial Analyst", "Biz Ops Analyst", "Revenue Operations Analyst", "Capital Markets Analyst",
    "Deal Desk Analyst", "Risk Analyst, Enterprise", "Corp Dev Associate",
]


@pytest.mark.parametrize("title", UNUSUAL_FINANCE_TITLES)
def test_unusual_finance_titles_survive_steps_4_and_5(title):
    assert NEG.hit(title) is None
    assert REL.judge(posting(title)) == "keep"


@pytest.mark.parametrize("title", ["Staff Accountant", "Tax Analyst", "Payroll Specialist", "Internal Audit Analyst",
                                   "Accounts Payable Specialist", "Financial Accounting Analyst", "Assistant Controller"])
def test_accounting_track_goes_to_hidden_not_deleted(title):
    assert REL.judge(posting(title)) == "accounting"


def test_titles_with_no_finance_word_go_to_claude():
    assert REL.judge(posting("Operations Analyst")) == "unknown"
    assert REL.judge(posting("Program Coordinator")) == "unknown"


def test_apple_team_code_is_read_from_the_url():
    p = posting("Benefits Analyst", url="https://jobs.apple.com/en-us/details/200690667-0836/benefits-analyst?team=CORSV")
    assert REL.department_of(p) == "CORSV"


# ---- steps 2 and 3 ---------------------------------------------------------------------------------------------------

def test_step2_date_window_and_date_approx():
    assert funnel.step_date(posting(posted="2026-10-27"), TODAY, 3) is None
    d = funnel.step_date(posting(posted="2026-10-24"), TODAY, 3)
    assert d and d.step == 2 and "5 days ago" in d.reason
    p = posting(posted="", first_seen="2026-10-28")
    assert funnel.step_date(p, TODAY, 3) is None and "date approx" in p.flags
    assert funnel.step_date(posting(posted="", first_seen="2026-10-20"), TODAY, 3).step == 2


@pytest.mark.parametrize("location,expect", [
    ("Sunnyvale, CA, USA", None), ("Seattle, Washington, USA; Boston, Massachusetts, USA", None),
    ("Remote; San Francisco, CA", None),             # a multi-location posting that includes a metro
    ("London, UK", "metro"), ("Remote - US", "remote"), ("Remote", "remote"), ("Austin, TX", None), ("", None),
    ("United States", None),
])
def test_step3_location(location, expect):
    d = funnel.step_location(posting(location=location))
    assert (d.rule if d else None) == expect


# ---- the 17-row regression, through the whole funnel -------------------------------------------------------------------

@pytest.fixture
def fworld(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    cols = ["url", "first_seen", "portal", "title", "company", "status", "location", "fingerprint", "posted_at", "trust_score",
            "trust_flags", "normalized_company", "requisition_id", "language", "listing_key"]
    lines = ["\t".join(cols)]
    by_row = {}
    for r in ROWS:
        lines.append("\t".join([r["url"], "2026-10-29", r["company"], r["title"], r["company"], "added", r["location"]] + [""] * 8))
        key = ingest.job_id(HistoryRow(url=r["url"]))
        by_row[r["row"]] = key
        folder = tmp_path / "jobs" / f"{key}_x"
        folder.mkdir(parents=True)
        (folder / "JD.md").write_text("# t\n\n- Source: fetch-jd\n\n---\n" + jd_text(r["row"]), encoding="utf-8")
    # extra postings that must be dropped at specific steps
    extra = [
        ("https://x.example/old", "Financial Analyst", "Acme", "San Jose, CA", "2026-10-20"),
        ("https://x.example/london", "Financial Analyst", "Acme", "London, UK", "2026-10-29"),
        ("https://x.example/rem", "Financial Analyst", "Acme", "Remote", "2026-10-29"),
        ("https://x.example/eng", "Software Engineer", "Acme", "San Jose, CA", "2026-10-29"),
        ("https://x.example/tax", "Tax Analyst", "Acme", "San Jose, CA", "2026-10-29"),
        ("https://x.example/ops", "Operations Coordinator", "Acme", "San Jose, CA", "2026-10-29"),
        ("https://x.example/treas", "Treasury Analyst", "Acme", "San Jose, CA", "2026-10-29"),
    ]
    for url, title, comp, loc, posted in extra:
        lines.append("\t".join([url, "2026-10-29", comp, title, comp, "added", loc, "", posted] + [""] * 6))
    hist = tmp_path / "scan-history.tsv"
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path, hist, by_row


def run_funnel(fworld, monkeypatch, classify=None, **kw):
    tmp_path, hist, by_row = fworld
    scores = {r: 3.5 for r in KEPT}
    install_fakes(monkeypatch, tmp_path, by_row, scores=scores, calls=[])
    verdicts = classify or {}
    monkeypatch.setattr(pipeline.Gate, "classify", staticmethod(
        lambda items, cache, cfg: ({k: verdicts[d] for k, d in ((k, disp.split(" - ")[0]) for k, disp in items) if d in verdicts},
                                   {"titles": len(items), "calls": 1, "cached": 0, "failed_calls": 0, "uncapped_titles": 0})))
    return pipeline.run(today=TODAY, history=hist, fetch=False, out_dir=tmp_path / "out", state_dir=tmp_path / "state", **kw)


def by_title(res):
    out = {}
    for d in res["dropped"]:
        out.setdefault(d["title"], []).append(("dropped", d["step"], d["reason"]))
    for j in res["hidden"]:
        out.setdefault(j["title"], []).append(("hidden", j["step"], "; ".join(r["text"] for r in j["reasons"])))
    for j in res["shown"]:
        out.setdefault(j["title"], []).append(("shown", 0, ""))
    return out


def test_17_row_regression_through_the_funnel(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    where = {r["row"]: by_title(res).get(r["title"]) for r in ROWS}
    senior_or_offtrack = {2, 3, 4, 6, 9, 10, 12, 13, 17}
    for row in senior_or_offtrack:
        assert where[row][0][:2] == ("dropped", 4), (row, where[row])                 # all dropped by step 4, by title alone
    kind, step, why = where[7][0]
    assert (kind, step) == ("hidden", 6) and "3+ years required" in why                  # 3+ years: step 6, from the JD
    assert where[11][0][:3] == ("dropped", 5, "accounting track")                         # "Leo Accounting PMO": accounting track
    shown_titles = {j["title"] for j in res["shown"]}
    assert {r["title"] for r in ROWS if r["row"] in KEPT} - shown_titles == {"Equity Research Associate, Software - Small & Mid-Cap"} \
        or {r["title"] for r in ROWS if r["row"] in KEPT} <= shown_titles
    assert not any(any(w in t.lower() for w in ("senior", "sr.", "lead")) for t in shown_titles)
    assert {"Equity Research Associate, Software - Small & Mid-Cap", "Investment Analyst"} <= shown_titles


def test_extra_postings_drop_at_the_right_step(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    t = by_title(res)
    assert t["Software Engineer"][0][:2] == ("dropped", 4)
    assert t["Tax Analyst"][0][:2] == ("dropped", 5) and t["Tax Analyst"][0][2] == "accounting track"
    assert t["Operations Coordinator"][0][:2] == ("dropped", 5) and "Not finance-adjacent" in t["Operations Coordinator"][0][2]
    assert "Treasury Analyst" in {j["title"] for j in res["shown"]}
    reasons = {d["reason"] for d in res["dropped"] if d["step"] in (2, 3)}
    assert any("days ago" in r for r in reasons) and "Outside the 8 metros" in reasons and "Fully remote" in reasons


def test_counts_are_consistent_and_never_increase(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    f = res["funnel"]
    for name, row in f["companies"].items():
        seq = [row[s] for s in funnel.STAGES]
        assert seq == sorted(seq, reverse=True), (name, row)
    tot = f["totals"]
    assert tot["pulled"] == len(ROWS) + 7 and tot["after_fit"] == len(res["shown"])
    steps = {s["step"]: s for s in f["steps"]}
    assert steps[4]["in"] - steps[4]["out"] == f["dropped_by_step"][4]
    # every posting is either standing at the end or recorded as dropped with a step and a reason
    assert tot["pulled"] == len(res["shown"]) + len(res["dropped"]) + len(res["hidden"])
    assert all(d["reason"] and d["step"] for d in res["dropped"])


def test_claude_failure_keeps_the_title_with_a_flag(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={})                       # no verdicts returned
    assert "Operations Coordinator" in {j["title"] for j in res["shown"]}
    flags = next(j["flags"] for j in res["shown"] if j["title"] == "Operations Coordinator")
    assert "finance relevance unverified" in flags


def test_samples_are_random_but_repeatable_and_capped_at_25(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    s = res["funnel"]["samples"]
    assert 0 < len(s["4"]) <= 25 and len(s["5"]) == 3
    res2 = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    assert res2["funnel"]["samples"] == s
    text = "\n".join(pipeline.format_samples(res))
    assert "25 RANDOM TITLES DROPPED AT STEP 4" in text and "STEP 5" in text


def test_dropped_csv_lists_every_dropped_row(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    out = fworld[0] / "out" / "Dropped_2026-10-29.csv"
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert len(rows) == len(res["dropped"]) + len(res["hidden"]) and {"step", "reason", "title", "url"} <= set(rows[0])


def test_excel_funnel_and_hidden_sheets(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    receipt = {"scanned": 3, "found": 500, "filtered": 120, "added": 380, "errors": [{"company": "Netflix", "error": "HTTP 429"}]}
    res["funnel"]["scan"] = pipeline._scan_summary(receipt)
    out = excel.write_outputs(res, now=datetime(2026, 10, 29, 9, 0), out_dir=fworld[0] / "out", state_dir=fworld[0] / "state")
    wb = load_workbook(out["master"])
    assert wb.sheetnames[:3] == ["Board", "Hidden", "Funnel"]
    fv = [[c.value for c in r] for r in wb["Funnel"].iter_rows()]
    header = next(r for r in fv if r and r[0] == "Company")
    assert header == ["Company", "Pulled", "After date", "After location", "After titles", "After finance", "After years", "After fit"]
    total = next(r for r in fv if r and r[0] == "TOTAL")
    assert total[1] == len(ROWS) + 7 and total[-1] == len(res["shown"])
    assert any(r and r[0] == "Boards that errored" and r[1] == 1 for r in fv) and any(r and "Netflix" in str(r[0]) for r in fv)
    hs = wb["Hidden"]
    hh = [c.value for c in hs[1]]
    assert "Step" in hh
    steps = {hs.cell(row=r, column=hh.index("Step") + 1).value for r in range(2, hs.max_row + 1)}
    assert {"4 title", "5 finance relevance", "6 experience / degree / license"} <= steps
    # rows dropped before the JD stage have no ID; rows judged on the JD do
    ids = {hs.cell(row=r, column=1).value for r in range(2, hs.max_row + 1)}
    assert None in ids and any(i is not None for i in ids)


def test_hidden_sheet_cap_points_to_the_csv(fworld, monkeypatch):
    monkeypatch.setattr(funnel, "funnel_config", lambda: {**config.load_yaml("funnel.yaml"), "hidden_sheet_max_rows": 5})
    res = run_funnel(fworld, monkeypatch, classify={"Operations Coordinator": "drop"})
    out = excel.write_outputs(res, now=datetime(2026, 10, 29, 9, 0), out_dir=fworld[0] / "out", state_dir=fworld[0] / "state")
    wb = load_workbook(out["master"])
    assert wb["Hidden"].max_row == 6
    assert any("Dropped_2026-10-29.csv" in str(c.value) for r in wb["Funnel"].iter_rows() for c in r)
