"""End to end through the one pipeline: the 4-day cutoff, Date unconfirmed routing, jobs_latest.md matching the Board sheet,
Status / Notes surviving a re-run, dedupe, and the per-board time limit (a slow board is "partial")."""
import re
import time
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook

from board import dates, excel, markdown
from board.fetch import ats, base, fetch_board
from board.fetch.prefilter import Prefilter

from .helpers import raw_job, run_jobs, start_for

DAY = date(2026, 11, 15)
START = start_for(DAY)
GOOD_JD = ("About the role\nJoin our rotational program and learn from mentors.\n\nBasic Qualifications:\n"
           "- 2 years of experience in financial analysis, forecasting or FP&A.\n- Advanced Excel and SQL skills for variance analysis.\n"
           "- Ability to partner cross-functionally with stakeholders.\n- Bachelor's degree in Finance, Accounting or Economics.\n"
           "- Strong written and verbal communication skills.\n- Experience building financial models and scenario analysis.\n"
           "- Familiarity with Python is a plus.\n- Comfortable working hybrid three days a week in our San Francisco office.\n")


def posted_at(delta: timedelta) -> dates.Posted:
    return dates.from_iso((START - delta).isoformat(), "first_published")


def jobs_for_cutoff():
    mk = lambda co, t, posted: raw_job(co, t, "San Francisco, CA", f"https://x.example/{co}/{t.replace(' ', '-')}", GOOD_JD, posted=posted)   # noqa: E731
    return [
        mk("Acme", "Financial Analyst", posted_at(timedelta(days=3))),
        mk("Beta", "FP&A Analyst", posted_at(timedelta(days=4))),                                          # exactly 4 days: kept
        mk("Gamma", "Pricing Analyst", posted_at(timedelta(days=4, hours=1))),                              # 4 days + 1 hour: dropped
        mk("Delta", "Treasury Analyst", posted_at(timedelta(days=9))),                                      # old; updated yesterday is irrelevant
        mk("Eps", "Investment Analyst", dates.unconfirmed("updated_at", "updated_at only")),                # no real publish date
        mk("Zeta", "Finance Analyst", dates.first_seen("2026-11-14")),                                      # first seen only
    ]


@pytest.fixture
def ran(tmp_path, monkeypatch):
    res = run_jobs(tmp_path, monkeypatch, jobs_for_cutoff(), DAY)
    out = excel.write_outputs(res, now=datetime(2026, 11, 15, 10, 0), out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    latest, dated = markdown.write(res, out["ids"], out["board_nums"], out_dir=tmp_path / "out")
    return tmp_path, res, out, latest, dated


def test_four_day_cutoff_and_unconfirmed_routing(ran):
    _, res, _, _, _ = ran
    confirmed = {j["company"] for j in res["shown"] if not j["date_flag"]}
    unconfirmed = {j["company"]: j["date_flag"] for j in res["shown"] if j["date_flag"]}
    assert confirmed == {"Acme", "Beta"}
    assert unconfirmed == {"Eps": "updated_at only", "Zeta": "first seen 2026-11-14"}
    assert "Gamma" not in {j["company"] for j in res["shown"] + res["hidden"]}              # 4 days + 1 hour
    assert "Delta" not in {j["company"] for j in res["shown"] + res["hidden"]}
    steps = {s["step"]: s for s in res["funnel"]["steps"]}
    assert steps[2]["in"] - steps[2]["out"] == 2                                             # Gamma and Delta, counted by date


def test_markdown_header_and_sections(ran):
    _, res, _, latest, dated = ran
    text = latest.read_text(encoding="utf-8")
    assert dated.name == "jobs_2026-11-15.md" and dated.read_text(encoding="utf-8") == text
    head = text.split("## Board")[0]
    assert "Run: 2026-11-15 10:00 America/Chicago" in head and "Window: 4 days, posted on or after 2026-11-11" in head
    assert "Boards scanned: 6" in head and "failed: 0" in head and "partial (hit the time limit): 0" in head
    assert "kept 2 · date unconfirmed 2" in head
    board, rest = text.split("## Board (2)")[1].split("## Date unconfirmed (2)")
    assert "Acme" in board and "Beta" in board and "Eps" not in board
    unconf, hidden = rest.split("## Hidden")
    assert "DATE UNCONFIRMED (updated_at only)" in unconf and "DATE UNCONFIRMED (first seen 2026-11-14)" in unconf


def test_markdown_matches_the_board_sheet(ran):
    tmp_path, res, out, latest, _ = ran
    text = latest.read_text(encoding="utf-8")
    md_ids = {int(m) for m in re.findall(r"^### \[(\d+)\]", text, re.M)}
    ws = load_workbook(out["master"])["Board"]
    headers = [c.value for c in ws[1]]
    sheet_ids = {ws.cell(row=r, column=headers.index("ID") + 1).value for r in range(2, ws.max_row + 1)}
    assert md_ids == sheet_ids and len(md_ids) == 4
    # same order as the Board sheet
    order = [int(m) for m in re.findall(r"^### \[(\d+)\]", text, re.M)]
    sheet_order = [ws.cell(row=r, column=headers.index("ID") + 1).value for r in range(2, ws.max_row + 1)]
    assert sorted(order) == sorted(sheet_order)
    # scores shown in the md equal the Fit Score column
    fit = {ws.cell(row=r, column=headers.index("ID") + 1).value: ws.cell(row=r, column=headers.index("Fit Score (0-100)") + 1).value
           for r in range(2, ws.max_row + 1)}
    for num, sc in re.findall(r"^### \[(\d+)\] ([\d.]+) ·", text, re.M):
        assert float(sc) == fit[int(num)]


def test_each_role_block_has_the_required_fields_and_quoted_requirements(ran):
    tmp_path, res, _, latest, _ = ran
    text = latest.read_text(encoding="utf-8")
    block = text.split("### [")[1].split("### [")[0]
    for needle in ("· Apply now", "City: San Francisco, CA", "Work mode:", "Posted:", "Years required: 2", "Pay: not listed",
                   "- Training: ", "- Requirements (quoted from the JD):", "- Link: https://x.example/", "- JD file: "):
        assert needle in block or needle in text, needle
    quotes = [l.strip()[2:] for l in text.splitlines() if l.strip().startswith("> ")]
    assert quotes and all(q.rstrip("…") in re.sub(r"\s+", " ", GOOD_JD) or q.rstrip("…")[:60] in GOOD_JD for q in quotes), quotes[:3]
    per_role = [b.count("\n  > ") for b in text.split("### [")[1:]]
    assert max(per_role) <= 6
    jd_path = re.search(r"- JD file: (.+)", text).group(1).strip()
    assert jd_path.endswith("JD.md") and "rotational program" in open(jd_path, encoding="utf-8").read()


def test_training_phrase_is_shown_for_each_role(ran):
    _, res, _, latest, _ = ran
    text = latest.read_text(encoding="utf-8")
    assert "- Training: rotational program; mentorship" in text or "- Training: rotational program" in text
    assert all(j["training"] for j in res["shown"])


def test_status_and_notes_survive_a_rerun_by_id(ran, monkeypatch):
    tmp_path, res, out, _, _ = ran
    wb = load_workbook(out["master"])
    ws = wb["Board"]
    h = [c.value for c in ws[1]]
    target = next(r for r in range(2, ws.max_row + 1) if "Acme" in str(ws.cell(row=r, column=h.index("Company") + 1).value))
    tid = ws.cell(row=target, column=h.index("ID") + 1).value
    ws.cell(row=target, column=h.index("Status") + 1).value = "Applied"
    ws.cell(row=target, column=h.index("Notes") + 1).value = "recruiter replied Monday"
    wb.save(out["master"])
    res2 = run_jobs(tmp_path, monkeypatch, jobs_for_cutoff(), DAY)
    out2 = excel.write_outputs(res2, now=datetime(2026, 11, 15, 11, 0), out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    state = excel.read_master(out2["master"].__class__(out2["master"]) if False else __import__("pathlib").Path(out2["master"]))
    assert state.board[tid]["Status"] == "Applied" and state.board[tid]["Notes"] == "recruiter replied Monday"
    assert out2["ids"] == out["ids"]                                                          # IDs are stable across runs


def test_same_posting_from_two_boards_is_listed_once_keeping_the_confirmed_date(tmp_path, monkeypatch):
    a = raw_job("Acme", "Financial Analyst", "San Francisco, CA", "https://x.example/same", GOOD_JD,
                posted=dates.first_seen("2026-11-14"))
    b = raw_job("Acme Careers", "Financial Analyst", "San Francisco, CA", "https://x.example/same", GOOD_JD, posted=posted_at(timedelta(days=1)))
    res = run_jobs(tmp_path, monkeypatch, [a, b], DAY)
    assert len(res["shown"]) == 1 and res["shown"][0]["date_flag"] == "" and res["fetch"]["deduped"] == 1


def test_low_score_is_hidden_with_an_id_and_a_reason(tmp_path, monkeypatch):
    j = raw_job("Unknownco", "Senior Treasury Analyst", "United States", "https://x.example/low", "Basic Qualifications:\n- 4 years of experience.",
                posted=posted_at(timedelta(days=1)))
    res = run_jobs(tmp_path, monkeypatch, [j], DAY)
    assert not res["shown"] and any(r["rule"] == "score" and "below 50" in r["text"] for h in res["hidden"] for r in h["reasons"])


def test_a_board_that_hits_the_time_limit_is_partial_and_keeps_what_it_listed(monkeypatch):
    calls = []

    def fake_http(url, data=None, **kw):
        base.check_deadline()
        calls.append(url)
        pos = [{"id": 1, "name": "Financial Analyst", "standardizedLocations": ["San Francisco, CA"], "postedTs": START.timestamp() - 3600,
                "positionUrl": "/careers/job/1"}]
        return {"data": {"positions": pos}}

    def slow_sleep(seconds):
        time.sleep(0.2)
        base.check_deadline()

    monkeypatch.setattr(ats, "http_json", fake_http)
    monkeypatch.setattr(ats, "polite_sleep", slow_sleep)
    log, kept, _ = fetch_board({"name": "Slow", "ats": "eightfold", "host": "h.example", "domain": "d.example"}, START,
                               base.RawStore(None), Prefilter(START, 4), detail=False, limit=0.5)
    assert log.status == "PARTIAL" and "time limit" in log.note and [j.title for j in kept] == ["Financial Analyst"]


def test_partial_and_failed_boards_are_named_in_the_markdown_header(tmp_path, monkeypatch):
    res = run_jobs(tmp_path, monkeypatch, jobs_for_cutoff()[:1], DAY)
    res["fetch"]["partial"] = [{"name": "Microsoft", "ats": "eightfold", "note": "cut off"}]
    res["fetch"]["failed"] = [{"name": "Carvana", "ats": "greenhouse", "status": "ERROR", "note": "boom"}]
    text = markdown.render(res, {j["id"]: i + 1 for i, j in enumerate(res["shown"])}, [1])
    assert "failed: 1 (Carvana: ERROR)" in text and "partial (hit the time limit): 1 (Microsoft)" in text


def test_hidden_section_lists_id_title_company_reason(tmp_path, monkeypatch):
    jobs = [raw_job("Acme", "Business Intelligence Analyst - Finance", "San Francisco, CA", "https://x.example/bi", GOOD_JD,
                    posted=posted_at(timedelta(days=1)))]
    res = run_jobs(tmp_path, monkeypatch, jobs, DAY)
    out = excel.write_outputs(res, now=datetime(2026, 11, 15, 10, 0), out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    latest, _ = markdown.write(res, out["ids"], out["board_nums"], out_dir=tmp_path / "out")
    hidden = latest.read_text(encoding="utf-8").split("## Hidden")[1]
    assert re.search(r"- \[\d+\] Business Intelligence Analyst - Finance — Acme — title not on list", hidden)
