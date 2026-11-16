import sys
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from board import config, excel

NOW = datetime(2026, 10, 29, 9, 30)


def job(hid, title="Financial Analyst", company="Acme", score=80.0, location="San Jose, CA", metro="bay_area",
        approx=False, reasons=None, flags=None, years=2.0):
    return {
        "id": hid, "first_seen": "2026-10-29", "years_bachelors": None, "score": score, "company": company,
        "title": title, "location": location, "url": f"https://jobs.example/{hid}", "posted": "2026-10-28",
        "age_days": 1, "date_approx": approx, "metro": metro, "work_mode": "onsite", "company_priority": 1,
        "jd_source": "fetch-jd", "jd_dir": "", "flags": flags or [], "reasons": reasons or [],
        "years_required": years, "years_evidence": "", "degree_required": None, "degree_evidence": "",
        "certs": [], "score_parts": {},
    }


def result(shown=(), hidden=()):
    return {"run_date": "2026-10-29", "window_days": 4, "history_rows": 9, "selection": {}, "jd_fetch": {},
            "shown": list(shown), "hidden": list(hidden), "hidden_by_rule": {}}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    return tmp_path / "output", tmp_path / "state"


def run(res, dirs, now=NOW, receipt=None):
    out, state = dirs
    return excel.write_outputs(res, now=now, out_dir=out, state_dir=state, scan_receipt=receipt)


def board_rows(path):
    ws = load_workbook(path)["Board"]
    headers = [c.value for c in ws[1]]
    return [dict(zip(headers, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]


def edit_master(dirs, sheet, match_id, **cells):
    path = dirs[0] / "JobBoard_Master.xlsx"
    wb = load_workbook(path)
    ws = wb[sheet]
    headers = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == match_id:
            for k, v in cells.items():
                row[headers.index(k)].value = v
    wb.save(path)


def test_first_run_writes_both_files_with_spec_layout(dirs):
    res = result([job("aaaa0001", score=62), job("aaaa0002", title="Senior FP&A Analyst", score=91, approx=True,
                                                 flags=["title: senior", "date approx"])],
                 [job("aaaa0003", score=40, reasons=[{"rule": "years_required", "text": "5+ years required (limit 3)",
                                                      "evidence": "5+ years of experience"}])])
    out = run(res, dirs)
    assert (dirs[0] / "JobBoard_2026-10-29.xlsx").exists() and (dirs[0] / "JobBoard_Master.xlsx").exists()
    wb = load_workbook(out["master"])
    assert wb.sheetnames == ["Board", "Hidden", "Funnel", "Check Manually", "Run Log"]
    ws = wb["Board"]
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:")
    rows = board_rows(out["master"])
    assert [r["Fit Score (0-100)"] for r in rows] == [91, 62]               # highest first
    top = rows[0]
    assert top["Track"] == "FP&A" and top["Status"] == "New" and top["Posted"] == "~2026-10-28"
    assert top["Location / Metro"] == "San Jose, CA — Bay Area" and top["Days Old"] == 1
    assert isinstance(top["ID"], int)
    title_cell = ws.cell(row=2, column=[c.value for c in ws[1]].index("Title") + 1)
    assert title_cell.hyperlink.target == "https://jobs.example/aaaa0002"
    hidden = load_workbook(out["master"])["Hidden"]
    hrow = {c.value: hidden.cell(row=2, column=i + 1).value for i, c in enumerate(hidden[1])}
    assert hrow["Hidden Reason"] == "5+ years required (limit 3)" and "5+ years" in hrow["Evidence"]
    log = load_workbook(out["master"])["Run Log"]
    lrow = {c.value: log.cell(row=2, column=i + 1).value for i, c in enumerate(log[1])}
    assert (lrow["Kept"], lrow["Hidden"], lrow["Roles Found"]) == (2, 1, 3)


def test_status_notes_and_ids_survive_the_next_run(dirs):
    run(result([job("aaaa0001"), job("aaaa0002", score=60)]), dirs)
    first = {r["Title"]: r["ID"] for r in board_rows(dirs[0] / "JobBoard_Master.xlsx")}
    rid = board_rows(dirs[0] / "JobBoard_Master.xlsx")[0]["ID"]
    edit_master(dirs, "Board", rid, Status="Applied", Notes="referral from Sam", **{"Applied Date": date(2026, 10, 29)})

    run(result([job("aaaa0001", score=70), job("aaaa0002", score=65), job("aaaa0009", score=50)]),
        dirs, now=datetime(2026, 10, 30, 8, 0))
    rows = {r["ID"]: r for r in board_rows(dirs[0] / "JobBoard_Master.xlsx")}
    assert rows[rid]["Status"] == "Applied" and rows[rid]["Notes"] == "referral from Sam"
    assert rows[rid]["Applied Date"].date() == date(2026, 10, 29)
    assert rows[rid]["ID"] == rid and {r["Title"] for r in rows.values()} == set(first)
    assert len(rows) == 3                                                    # the new role was added
    assert (dirs[0] / "JobBoard_2026-10-30.xlsx").exists()                   # new dated snapshot


def test_applied_role_that_left_the_window_is_kept_but_stale_new_role_is_dropped(dirs):
    run(result([job("aaaa0001"), job("aaaa0002", score=60)]), dirs)
    rows = board_rows(dirs[0] / "JobBoard_Master.xlsx")
    ids = {r["Key"]: r["ID"] for r in rows}
    edit_master(dirs, "Board", ids["aaaa0001"], Status="Applied")
    run(result([]), dirs, now=datetime(2026, 11, 20, 8, 0))                   # 22 days later, nothing in the window
    left = {r["Key"] for r in board_rows(dirs[0] / "JobBoard_Master.xlsx")}
    assert left == {"aaaa0001"}


def test_hidden_override_moves_role_to_board_and_sticks(dirs):
    hid = job("bbbb0001", score=45, reasons=[{"rule": "years_required", "text": "4+ years required (limit 3)",
                                              "evidence": "4 years"}])
    run(result([], [hid]), dirs)
    hidden_id = load_workbook(dirs[0] / "JobBoard_Master.xlsx")["Hidden"]["A2"].value
    edit_master(dirs, "Hidden", hidden_id, Override="Show")
    run(result([], [hid]), dirs, now=datetime(2026, 10, 30, 8, 0))
    rows = board_rows(dirs[0] / "JobBoard_Master.xlsx")
    assert [r["ID"] for r in rows] == [hidden_id] and "override: 4+ years" in rows[0]["Flags"]
    run(result([], [hid]), dirs, now=datetime(2026, 10, 31, 8, 0))             # still honored on later runs
    assert [r["ID"] for r in board_rows(dirs[0] / "JobBoard_Master.xlsx")] == [hidden_id]


def test_row_you_worked_is_not_buried_when_a_filter_later_hides_it(dirs):
    run(result([job("cccc0001")]), dirs)
    rid = board_rows(dirs[0] / "JobBoard_Master.xlsx")[0]["ID"]
    edit_master(dirs, "Board", rid, Status="Interested")
    hidden_now = job("cccc0001", reasons=[{"rule": "years_required", "text": "5+ years required", "evidence": ""}])
    run(result([], [hidden_now]), dirs, now=datetime(2026, 10, 30, 8, 0))
    rows = board_rows(dirs[0] / "JobBoard_Master.xlsx")
    assert rows[0]["ID"] == rid and rows[0]["Status"] == "Interested" and "now filtered" in rows[0]["Flags"]


def test_ids_are_stable_across_runs_and_never_reused(dirs):
    run(result([job("dddd0001")]), dirs)
    a = board_rows(dirs[0] / "JobBoard_Master.xlsx")[0]["ID"]
    run(result([job("dddd0002"), job("dddd0001")]), dirs, now=datetime(2026, 10, 30, 8, 0))
    ids = {r["Key"]: r["ID"] for r in board_rows(dirs[0] / "JobBoard_Master.xlsx")}
    assert ids["dddd0001"] == a and ids["dddd0002"] == a + 1


def test_new_rows_bold_old_rows_not(dirs):
    run(result([job("eeee0001")]), dirs)
    run(result([job("eeee0001"), job("eeee0002", score=60)]), dirs, now=datetime(2026, 10, 30, 8, 0))
    ws = load_workbook(dirs[0] / "JobBoard_Master.xlsx")["Board"]
    kc = excel.BOARD_COLS.index('Key') + 1
    keys = {ws.cell(row=r, column=kc).value: ws.cell(row=r, column=5).font.bold for r in range(2, 4)}
    assert not keys["eeee0001"] and keys["eeee0002"] is True


def test_formatting_rules_and_status_dropdown(dirs):
    run(result([job("ffff0001")]), dirs)
    ws = load_workbook(dirs[0] / "JobBoard_Master.xlsx")["Board"]
    dv = ws.data_validations.dataValidation[0]
    assert dv.formula1 == '"' + ",".join(excel.STATUSES) + '"'
    formulas = [r.formula[0] for rng in ws.conditional_formatting for r in rng.rules]
    assert any('"Applied"' in f and '"Skip"' in f for f in formulas)
    assert any(">=70" in f for f in formulas) and any(">=50" in f and "<70" in f for f in formulas) and any("<50" in f for f in formulas)


@pytest.mark.skipif(sys.platform != "win32", reason="file locking semantics are Windows-specific")
def test_locked_master_gets_a_timestamped_copy_and_is_left_untouched(dirs):
    run(result([job("gggg0001")]), dirs)
    rid = board_rows(dirs[0] / "JobBoard_Master.xlsx")[0]["ID"]
    edit_master(dirs, "Board", rid, Status="Applied")
    master = dirs[0] / "JobBoard_Master.xlsx"
    before = master.read_bytes()
    with open(master, "r+b"):                                                  # like Excel holding the file open
        out = run(result([job("gggg0001", score=77), job("gggg0002", score=50)]), dirs, now=datetime(2026, 10, 30, 8, 5, 7))
    assert out["locked"] is True and master.read_bytes() == before
    copies = sorted(dirs[0].glob("JobBoard_Master_2026-10-30_*.xlsx"))
    assert copies and out["master"] == str(copies[0])
    assert any("open in Excel" in n for n in out["notes"])
    rows = {r["Key"]: r for r in board_rows(copies[0])}
    assert rows["gggg0001"]["Status"] == "Applied" and "gggg0002" in rows        # merged state is in the copy
    assert not list(dirs[0].glob(".~tmp_*"))                                    # no temp litter


def test_corrupt_master_is_never_overwritten(dirs):
    run(result([job("hhhh0001")]), dirs)
    master = dirs[0] / "JobBoard_Master.xlsx"
    master.write_bytes(b"not an xlsx")
    out = run(result([job("hhhh0001")]), dirs, now=datetime(2026, 10, 30, 8, 0))
    assert master.read_bytes() == b"not an xlsx" and any("Master NOT updated" in n for n in out["notes"])
    assert (dirs[0] / "JobBoard_2026-10-30.xlsx").exists()


def test_scan_receipt_fills_run_log_and_check_manually_lists_unscannable(dirs, tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"scanned": 49, "found": 20519, "errors": [{"company": "Foo", "error": "HTTP 500"}]}', encoding="utf-8")
    out = run(result([job("iiii0001")]), dirs, receipt=receipt)
    wb = load_workbook(out["master"])
    log = wb["Run Log"]
    lrow = {c.value: log.cell(row=2, column=i + 1).value for i, c in enumerate(log[1])}
    assert lrow["Companies Scanned"] == 49 and "Foo (HTTP 500)" in lrow["Boards Errored"]
    manual = wb["Check Manually"]
    names = {manual.cell(row=r, column=1).value for r in range(2, manual.max_row + 1)}
    assert {"Meta", "Goldman Sachs", "OpenAI"} <= names
    assert manual.cell(row=2, column=6).hyperlink is not None


def test_track_of():
    assert excel.track_of("Pricing Analyst") == "Pricing"
    assert excel.track_of("Senior Financial Analyst, Deals Desk Finance") == "Pricing"
    assert excel.track_of("Equity Research Associate, Software") == "Equity Research"
    assert excel.track_of("Financial Analyst II, Cloud Financial Planning and Analysis") == "FP&A"
    assert excel.track_of("Finance Analyst, Advertising Finance") == "Financial Analyst"
