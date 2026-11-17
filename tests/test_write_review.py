"""board/write_review.py: Claude's verdicts become output\\reviews\\Claude_Review_<date>.xlsx with the same IDs as the master."""
import json
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from board import excel, markdown, write_review
from board.write_review import ReviewError

from .helpers import run_jobs
from .test_board_outputs import DAY, jobs_for_cutoff

NOW = datetime(2026, 11, 15, 12, 30, 5)


@pytest.fixture
def board(tmp_path, monkeypatch):
    res = run_jobs(tmp_path, monkeypatch, jobs_for_cutoff(), DAY)
    out = excel.write_outputs(res, now=datetime(2026, 11, 15, 10, 0), out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    latest, _ = markdown.write(res, out["ids"], out["board_nums"], out_dir=tmp_path / "out")
    parsed = markdown.parse(latest.read_text(encoding="utf-8"))
    return tmp_path, out, latest, parsed


def master_ids(out, sheet="Board"):
    ws = load_workbook(out["master"])[sheet]
    h = [c.value for c in ws[1]]
    return {ws.cell(row=r, column=h.index("ID") + 1).value for r in range(2, ws.max_row + 1)}


def verdicts_for(parsed, overrides=None):
    """One verdict per role: Board roles alternate Apply now / Skip, unconfirmed roles Stretch / Skip. `overrides` {id: dict}."""
    items = []
    for i, r in enumerate(parsed["board"]):
        items.append({"id": r["id"], "verdict": "Apply now" if i == 0 else "Skip", "why": f"fits {r['company']} work",
                      "gap": "no SaaS metrics" if i == 0 else ""})
    for i, r in enumerate(parsed["unconfirmed"]):
        items.append({"id": r["id"], "verdict": "Stretch" if i == 0 else "Skip", "why": "worth a look", "gap": "date"})
    for num, extra in (overrides or {}).items():
        items = [{**v, **extra} if v["id"] == num else v for v in items]
    return {"verdicts": items}


def test_the_parser_reads_back_what_the_writer_wrote(board):
    _, out, latest, parsed = board
    assert parsed["header"]["title"].endswith("2026-11-15") and "America/Chicago" in parsed["header"]["run"]
    assert {r["id"] for r in parsed["board"] + parsed["unconfirmed"]} == master_ids(out)
    r = parsed["board"][0]
    assert r["tier"] == "Apply now" and r["years"] == "2" and r["city"] == "San Francisco, CA" and r["link"].startswith("https://x.example/")
    assert r["requirements"] and len(r["requirements"]) <= 6 and r["training"].startswith("rotational program")
    assert all(u["date_flag"] for u in parsed["unconfirmed"]) and not any(b["date_flag"] for b in parsed["board"])
    assert {u["date_flag"] for u in parsed["unconfirmed"]} == {"updated_at only", "first seen 2026-11-14"}


def test_workbook_has_the_four_sheets_with_the_right_headers(board):
    tmp_path, out, latest, parsed = board
    res = write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    assert Path(res["path"]).name == "Claude_Review_2026-11-15.xlsx" and not res["used_copy"]
    wb = load_workbook(res["path"])
    assert wb.sheetnames == ["Picks", "Date Unconfirmed", "Passed Over", "Run Info"]
    assert [c.value for c in wb["Picks"][1]] == ["Rank", "ID", "Score", "Claude Tier", "Title", "Company", "City", "On-site/Hybrid", "Posted",
                                                 "Date Flag", "Years Required", "Pay", "Why it fits", "Gap to watch", "Link"]
    assert [c.value for c in wb["Date Unconfirmed"][1]][:3] == ["ID", "Score", "Title"] and "Date Flag (why unconfirmed)" in [c.value for c in wb["Date Unconfirmed"][1]]
    assert [c.value for c in wb["Passed Over"][1]] == ["ID", "Score", "Title", "Company", "City", "Reason"]
    assert [c.value for c in wb["Run Info"][1]] == ["Item", "Value"]


def test_ids_in_every_sheet_match_the_master_and_rows_are_ranked(board):
    tmp_path, out, latest, parsed = board
    res = write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    wb = load_workbook(res["path"])
    picks = [[c.value for c in r] for r in wb["Picks"].iter_rows(min_row=2)]
    unconf = [[c.value for c in r] for r in wb["Date Unconfirmed"].iter_rows(min_row=2)]
    passed = [[c.value for c in r] for r in wb["Passed Over"].iter_rows(min_row=2)]
    all_ids = {r[1] for r in picks} | {r[0] for r in unconf} | {r[0] for r in passed}
    assert all_ids == master_ids(out) and all_ids <= {r["id"] for r in parsed["board"] + parsed["unconfirmed"]}
    assert [r[0] for r in picks] == [1] and picks[0][3] == "Apply now" and picks[0][12].startswith("fits ") and picks[0][13] == "no SaaS metrics"
    # the data columns come from jobs_latest.md, not from Claude
    board_role = next(r for r in parsed["board"] if r["id"] == picks[0][1])
    assert picks[0][2] == board_role["score"] and picks[0][4] == board_role["title"] and picks[0][14] == board_role["link"]
    assert {r[0] for r in unconf} == {u["id"] for u in parsed["unconfirmed"]} and {r[7] for r in unconf} == {"yes", "no"}
    assert {r[6] for r in unconf} == {"updated_at only", "first seen 2026-11-14"}
    assert all(r[5] for r in passed)                                                  # every passed-over role has a reason


def test_formatting_frozen_header_autofilter_widths_wrap_and_links(board):
    tmp_path, _, latest, parsed = board
    wb = load_workbook(write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)["path"])
    for name in wb.sheetnames:
        ws = wb[name]
        assert ws.freeze_panes == "A2" and ws.auto_filter.ref and ws.column_dimensions["A"].width > 0, name
    ws = wb["Picks"]
    h = [c.value for c in ws[1]]
    assert ws.column_dimensions["M"].width >= 50 and ws.column_dimensions["N"].width >= 40               # Why / Gap are wide
    assert ws.cell(row=2, column=h.index("Why it fits") + 1).alignment.wrap_text and ws.cell(row=2, column=h.index("Gap to watch") + 1).alignment.wrap_text
    link = ws.cell(row=2, column=h.index("Link") + 1)
    assert link.hyperlink is not None and link.hyperlink.target == link.value and link.value.startswith("https://")
    assert wb["Date Unconfirmed"].cell(row=2, column=12).hyperlink is not None


def test_run_info_has_the_run_window_review_time_and_counts(board):
    tmp_path, _, latest, parsed = board
    wb = load_workbook(write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)["path"])
    info = {r[0].value: r[1].value for r in wb["Run Info"].iter_rows(min_row=2)}
    assert "2026-11-15 10:00 America/Chicago" in info["Board run"] and info["Window"].startswith("4 days, posted on or after 2026-11-11")
    assert info["Review written"] == "2026-11-15 12:30" and info["Picks"] == 1 and info["Passed Over"] == 1
    assert info["Date Unconfirmed (worth checking / listed)"] == "1 / 2" and info["Apply now / Strong / Stretch"] == "1 / 0 / 0"
    assert info["Warnings"] == "none" and info["Source file"].endswith("jobs_latest.md")


def test_the_master_is_never_written(board):
    tmp_path, out, latest, parsed = board
    master = Path(out["master"])
    before = (master.read_bytes(), master.stat().st_mtime_ns)
    write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    assert (master.read_bytes(), master.stat().st_mtime_ns) == before
    assert not list((tmp_path / "reviews").glob("JobBoard_*"))


def test_an_existing_review_file_gets_a_timestamped_copy(board):
    tmp_path, _, latest, parsed = board
    first = write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    second = write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    assert Path(first["path"]).exists() and second["used_copy"] and second["path"] != first["path"]
    assert Path(second["path"]).name == "Claude_Review_2026-11-15_123005.xlsx"
    third = write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    assert len({first["path"], second["path"], third["path"]}) == 3


def test_a_file_open_in_excel_gets_a_timestamped_copy(board, monkeypatch):
    tmp_path, _, latest, parsed = board
    real_save = Workbook.save
    calls = []

    def locked_once(self, filename):
        calls.append(str(filename))
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied")        # Excel holds the file
        return real_save(self, filename)

    monkeypatch.setattr(Workbook, "save", locked_once)
    res = write_review.write_review(verdicts_for(parsed), latest, tmp_path / "reviews", NOW)
    assert res["used_copy"] and res["path"].endswith("Claude_Review_2026-11-15_123005.xlsx") and Path(res["path"]).exists()


def test_bad_input_is_rejected_or_reported(board):
    tmp_path, _, latest, parsed = board
    with pytest.raises(ReviewError):
        write_review.write_review({"verdicts": [{"id": 1, "verdict": "Maybe"}]}, latest, tmp_path / "r", NOW)
    with pytest.raises(ReviewError):
        write_review.write_review({"verdicts": [{"id": 1, "verdict": "Strong"}, {"id": 1, "verdict": "Skip"}]}, latest, tmp_path / "r", NOW)
    with pytest.raises(ReviewError):
        write_review.write_review({"picks": []}, latest, tmp_path / "r", NOW)
    # an unknown ID is ignored with a warning; a role with no verdict goes to Passed Over / not worth checking, also with a warning
    v = verdicts_for(parsed)
    dropped = v["verdicts"].pop(0)["id"]
    v["verdicts"].append({"id": 9999, "verdict": "Strong", "why": "x", "gap": "y"})
    res = write_review.write_review(v, latest, tmp_path / "r", NOW)
    assert any("9999" in w for w in res["warnings"]) and any(f"id {dropped}" in w and "no verdict" in w for w in res["warnings"])
    wb = load_workbook(res["path"])
    assert dropped in {r[0].value for r in wb["Passed Over"].iter_rows(min_row=2)}


def test_text_is_kept_to_one_line(board):
    tmp_path, _, latest, parsed = board
    v = verdicts_for(parsed, {parsed["board"][0]["id"]: {"why": "line one\nline two   spaced", "gap": "a\n\nb"}})
    wb = load_workbook(write_review.write_review(v, latest, tmp_path / "reviews", NOW)["path"])
    row = [c.value for c in wb["Picks"][2]]
    assert row[12] == "line one line two spaced" and row[13] == "a b"


def test_command_line_prints_the_path(board, capsys):
    tmp_path, _, latest, parsed = board
    rj = tmp_path / "review.json"
    rj.write_text(json.dumps(verdicts_for(parsed)), encoding="utf-8")
    assert write_review.main([str(rj), "--md", str(latest), "--out", str(tmp_path / "reviews")]) == 0
    text = capsys.readouterr().out
    assert "Review workbook:" in text and "Claude_Review_2026-11-15.xlsx" in text and "Picks 1, Date Unconfirmed 2, Passed Over 1" in text
    assert write_review.main([str(tmp_path / "missing.json")]) == 2


# ---- caps: 30 Board roles and 10 Date unconfirmed roles for Claude; the rest are "below cap" -------------------------------

def _role(i, score, unconfirmed=False):
    return {"id": i, "score": float(score), "tier": "Strong", "track": "Financial Analyst", "title": f"Role {i}", "company": f"Co{i}",
            "city": "San Jose, CA", "work_mode": "hybrid", "posted": "" if unconfirmed else "2026-11-14",
            "date_flag": "updated_at only" if unconfirmed else "", "years": "2", "pay": "not listed", "training": "-",
            "requirements": [], "flags": "", "link": f"https://x.example/{i}", "jd_path": ""}


def test_roles_past_the_caps_go_to_passed_over_as_below_cap_with_their_python_score():
    board = {"header": {}, "board": [_role(i, 90 - i) for i in range(1, 36)],               # 35 Board roles, best first
             "unconfirmed": [_role(100 + i, 80 - i, True) for i in range(1, 13)], "hidden": []}   # 12 unconfirmed
    verdicts = {i: {"verdict": "Strong", "why": "w", "gap": "g"} for i in list(range(1, 31)) + list(range(101, 111))}
    parts = write_review.build_rows(board, verdicts)
    assert write_review.BOARD_CAP == 30 and write_review.UNCONFIRMED_CAP == 10
    assert len(parts["picks"]) == 30 and len(parts["unconfirmed"]) == 10 and parts["warnings"] == []      # no warning for roles past a cap
    below = [r for r in parts["passed"] if r["reason"].startswith("below cap")]
    assert sorted(r["id"] for r in below) == [31, 32, 33, 34, 35, 111, 112]
    assert {r["id"]: r["reason"] for r in below}[31] == "below cap (Python score 59)" and {r["id"]: r["reason"] for r in below}[112] == "below cap (Python score 68)"


def test_a_role_inside_the_cap_without_a_verdict_still_warns():
    board = {"header": {}, "board": [_role(i, 90 - i) for i in range(1, 4)], "unconfirmed": [], "hidden": []}
    parts = write_review.build_rows(board, {1: {"verdict": "Strong", "why": "w", "gap": "g"}})
    assert len(parts["warnings"]) == 2 and all("no verdict" in w for w in parts["warnings"])
    assert {r["id"]: r["reason"] for r in parts["passed"]} == {2: "no verdict given", 3: "no verdict given"}


def test_the_cap_follows_the_python_score_not_the_file_order():
    board = {"header": {}, "board": [_role(i, 40 + i) for i in range(1, 32)], "unconfirmed": [], "hidden": []}   # id 31 has the best score
    parts = write_review.build_rows(board, {i: {"verdict": "Skip", "why": "w", "gap": ""} for i in range(2, 32)})
    below = [r["id"] for r in parts["passed"] if r["reason"].startswith("below cap")]
    assert below == [1]                                                                    # the lowest score is the one past the cap


def test_below_cap_rows_reach_the_workbook(board, tmp_path):
    t, _, latest, parsed = board
    res = write_review.write_review({"verdicts": [{"id": r["id"], "verdict": "Skip", "why": "no", "gap": ""} for r in parsed["board"]]},
                                    latest, t / "rv", NOW)
    assert res["counts"]["passed_over"] == len(parsed["board"]) + 0 and res["warnings"] != []         # unconfirmed roles had no verdicts: inside the cap, so they warn
