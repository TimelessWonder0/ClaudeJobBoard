"""The negative-filter funnel: title rules, finance relevance, per-step counts, the safety nets, and the 17-row regression
run end to end through steps 1-7 (fakes for career-ops and Claude; no network)."""
import csv
import json
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from board import config, excel, funnel, ingest, pipeline
from board.models import HistoryRow

from .helpers import raw_job, run_jobs
from .test_regression_2026_10_29 import KEPT, ROWS, TODAY, jd_text

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


# ---- the 17-row regression, through the whole pipeline -----------------------------------------------------------------

EXTRA = [      # postings that must be dropped at specific steps
    ("Acme", "Financial Analyst", "San Jose, CA", "https://x.example/old", date(2026, 10, 20)),                     # posted 9 days ago
    ("Acme", "Financial Analyst", "London, UK", "https://x.example/london", TODAY),
    ("Acme", "Financial Analyst", "Remote", "https://x.example/rem", TODAY),
    ("Acme", "Software Engineer", "San Jose, CA", "https://x.example/eng", TODAY),
    ("Acme", "Tax Analyst", "San Jose, CA", "https://x.example/tax", TODAY),
    ("Acme", "Operations Coordinator", "San Jose, CA", "https://x.example/ops", TODAY),
    ("Acme", "Treasury Analyst", "San Jose, CA", "https://x.example/treas", TODAY),
]


@pytest.fixture
def fworld(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    jobs = [raw_job(r["company"], r["title"], r["location"], r["url"], jd_text(r["row"]), day=TODAY) for r in ROWS]
    jobs += [raw_job(c, t, loc, url, "Basic Qualifications:\n- 1 year of experience in finance.", day=d) for c, t, loc, url, d in EXTRA]
    return tmp_path, jobs


def run_funnel(fworld, monkeypatch, **kw):
    tmp_path, jobs = fworld
    return run_jobs(tmp_path, monkeypatch, jobs, TODAY, **kw)


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
    res = run_funnel(fworld, monkeypatch)
    where = {r["row"]: by_title(res).get(r["title"]) for r in ROWS}
    for row in (11, 17):                                                          # accounting PMO, Account Executive: dropped by title alone
        assert where[row][0][:2] == ("dropped", 4), (row, where[row])
    assert where[11][0][2].startswith("Wrong function") and "Account Executive" in where[17][0][2]
    assert where[13][0][:3] == ("hidden", 5, "title not on list")                  # finance-ish title off the list: hidden WITH an ID
    shown_titles = {j["title"] for j in res["shown"]}
    assert {r["title"] for r in ROWS if r["row"] in KEPT} <= shown_titles
    assert {"Equity Research Associate, Software - Small & Mid-Cap", "Investment Analyst"} <= shown_titles
    assert "Benefits Financial Analyst" in shown_titles                             # 3 years is inside the limit now


def test_extra_postings_drop_at_the_right_step(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch)
    t = by_title(res)
    assert t["Software Engineer"][0][:2] == ("dropped", 4)
    assert t["Tax Analyst"][0][:2] == ("dropped", 4) and t["Tax Analyst"][0][2].startswith("Wrong function")
    assert t["Operations Coordinator"][0][:2] == ("dropped", 5) and t["Operations Coordinator"][0][2] == "title not on list"
    assert "Treasury Analyst" in {j["title"] for j in res["shown"]}
    reasons = {d["reason"] for d in res["dropped"] if d["step"] == 3}
    assert "Outside the target metros" in reasons and "Fully remote" in reasons
    steps = {s["step"]: s for s in res["funnel"]["steps"]}
    assert steps[2]["in"] - steps[2]["out"] == 1                                   # the posting from 9 days ago, counted not listed


def test_counts_are_consistent_and_never_increase(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch)
    f = res["funnel"]
    for name, row in f["companies"].items():
        seq = [row[s] for s in funnel.STAGES]
        assert seq == sorted(seq, reverse=True), (name, row)
    tot = f["totals"]
    assert tot["pulled"] == len(ROWS) + len(EXTRA) and tot["after_fit"] == len(res["shown"])
    steps = {s["step"]: s for s in f["steps"]}
    assert steps[4]["in"] - steps[4]["out"] == f["dropped_by_step"][4]
    # every posting is either standing at the end, dropped on its date (counted), or recorded as dropped / hidden with a reason
    date_dropped = steps[2]["in"] - steps[2]["out"]
    assert tot["pulled"] == len(res["shown"]) + len(res["dropped"]) + len(res["hidden"]) + date_dropped
    assert all(d["reason"] and d["step"] for d in res["dropped"])


def test_finance_ish_title_not_on_the_list_is_hidden_with_an_id_but_others_are_just_dropped(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch)
    hidden = {j["title"]: j for j in res["hidden"]}
    assert "Senior Business Intelligence Analyst - Corporate Finance" in hidden        # has "analyst" and "finance"
    assert "Operations Coordinator" not in hidden                                       # no finance-ish word: dropped, in the CSV only
    assert hidden["Senior Business Intelligence Analyst - Corporate Finance"]["id"]


def test_default_run_makes_no_claude_calls(fworld, monkeypatch):
    import shutil
    import subprocess

    def boom(*a, **k):
        raise AssertionError(f"the default pipeline called an external command: {a[:1]}")

    seen = []
    real_run = subprocess.run
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: seen.append(name) or None)
    monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: boom(args) if "claude" in str(args).lower() else real_run(args, *a, **k))
    monkeypatch.setattr(subprocess, "Popen", boom)
    res = run_funnel(fworld, monkeypatch)
    assert res["gate"] == {"enabled": False} and res["shown"]
    assert "claude" not in seen


def test_samples_are_random_but_repeatable_and_capped_at_25(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch)
    s = res["funnel"]["samples"]
    assert 0 < len(s["4"]) <= 25 and len(s["5"]) == 1
    res2 = run_funnel(fworld, monkeypatch)
    assert res2["funnel"]["samples"] == s
    text = "\n".join(pipeline.format_samples(res))
    assert "25 RANDOM TITLES DROPPED AT STEP 4" in text and "STEP 5" in text


def test_dropped_csv_lists_every_dropped_row(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch)
    out = fworld[0] / "out" / "Dropped_2026-10-29.csv"
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert len(rows) == len(res["dropped"]) + len(res["hidden"]) and {"step", "reason", "title", "url"} <= set(rows[0])


def test_excel_funnel_and_hidden_sheets(fworld, monkeypatch):
    res = run_funnel(fworld, monkeypatch)
    receipt = {"scanned": 3, "found": 500, "filtered": 120, "added": 380, "errors": [{"company": "Netflix", "error": "HTTP 429"}]}
    res["funnel"]["scan"] = pipeline._scan_summary(receipt)
    out = excel.write_outputs(res, now=datetime(2026, 10, 29, 9, 0), out_dir=fworld[0] / "out", state_dir=fworld[0] / "state")
    wb = load_workbook(out["master"])
    assert wb.sheetnames[:3] == ["Board", "Hidden", "Funnel"]
    fv = [[c.value for c in r] for r in wb["Funnel"].iter_rows()]
    header = next(r for r in fv if r and r[0] == "Company")
    assert header == ["Company", "Pulled", "After date", "After location", "After titles", "After finance", "After years", "After fit"]
    total = next(r for r in fv if r and r[0] == "TOTAL")
    assert total[1] == len(ROWS) + len(EXTRA) and total[-1] == len(res["shown"])
    assert any(r and r[0] == "Boards that errored" and r[1] == 1 for r in fv) and any(r and "Netflix" in str(r[0]) for r in fv)
    hs = wb["Hidden"]
    hh = [c.value for c in hs[1]]
    assert "Step" in hh
    steps = {hs.cell(row=r, column=hh.index("Step") + 1).value for r in range(2, hs.max_row + 1)}
    assert {"3 location", "4 title", "5 finance relevance"} <= steps
    # rows dropped before the JD stage have no ID; rows with a JD (or a finance-ish unlisted title) do
    ids = {hs.cell(row=r, column=1).value for r in range(2, hs.max_row + 1)}
    assert None in ids and any(i is not None for i in ids)


def test_hidden_sheet_cap_points_to_the_csv(fworld, monkeypatch):
    monkeypatch.setattr(funnel, "funnel_config", lambda: {**config.load_yaml("funnel.yaml"), "hidden_sheet_max_rows": 5})
    res = run_funnel(fworld, monkeypatch)
    out = excel.write_outputs(res, now=datetime(2026, 10, 29, 9, 0), out_dir=fworld[0] / "out", state_dir=fworld[0] / "state")
    wb = load_workbook(out["master"])
    assert wb["Hidden"].max_row == 6
    assert any("Dropped_2026-10-29.csv" in str(c.value) for r in wb["Funnel"].iter_rows() for c in r)
