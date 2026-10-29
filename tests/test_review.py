import json
import sys
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from board import config, excel, review

NOW = datetime(2026, 10, 29, 9, 30)

JD_SALES = ("# Financial Analyst, Partner Pricing — Acme\n\n- Source: fetch-jd\n\n---\n"
            "About the role\nThis is a quota-carrying sales role supporting partner deals.\n"
            "Basic Qualifications:\n- 2 years of experience in finance.\n- Must hold an active CPA license.\n")


def job(hid, title="Financial Analyst", score=80.0, years=2.0):
    return {
        "id": hid, "first_seen": "2026-10-29", "years_bachelors": None, "score": score, "company": "Acme",
        "title": title, "location": "San Jose, CA", "url": f"https://jobs.example/{hid}", "posted": "2026-10-28",
        "age_days": 1, "date_approx": False, "metro": "bay_area", "work_mode": "onsite", "company_priority": 1,
        "jd_source": "fetch-jd", "jd_dir": "", "flags": [], "reasons": [], "years_required": years,
        "years_evidence": "", "degree_required": None, "degree_evidence": "", "certs": [], "score_parts": {},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    monkeypatch.setattr(config, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(config, "data_root", lambda: tmp_path / "data")
    out, state = tmp_path / "output", tmp_path / "state"
    for hid in ("k0000001", "k0000002", "k0000003", "k0000004"):
        folder = tmp_path / "jobs" / f"{hid}_Acme_Role"
        folder.mkdir(parents=True)
        (folder / "JD.md").write_text(JD_SALES, encoding="utf-8")
    res = {"run_date": "2026-10-29", "window_days": 4, "history_rows": 4, "selection": {}, "jd_fetch": {}, "hidden_by_rule": {},
           "shown": [job("k0000001", score=90), job("k0000002", score=80), job("k0000003", score=70),
                     job("k0000004", score=60)], "hidden": []}
    excel.write_outputs(res, now=NOW, out_dir=out, state_dir=state)
    return out, state, res


def rows(path):
    ws = load_workbook(path)["Board"]
    headers = [c.value for c in ws[1]]
    return [dict(zip(headers, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]


def mark(out, row_id, **cells):
    path = out / "JobBoard_Master.xlsx"
    wb = load_workbook(path)
    ws = wb["Board"]
    headers = [c.value for c in ws[1]]
    for r in ws.iter_rows(min_row=2):
        if r[0].value == row_id:
            for k, v in cells.items():
                r[headers.index(k)].value = v
    wb.save(path)


def prep(env, **kw):
    out, state, _ = env
    return review.prepare(day=date(2026, 10, 29), out_dir=out, state_dir=state, **kw)


def apply_json(env, reviews, tmp_path):
    out, state, _ = env
    f = tmp_path / "review_input.json"
    f.write_text(json.dumps({"reviews": reviews}), encoding="utf-8")
    return review.apply(f, out_dir=out, state_dir=state, now=NOW)


def test_prepare_takes_top_rows_skips_worked_and_reviewed(env):
    out = env[0]
    ids = {r["Key"]: r["ID"] for r in rows(out / "JobBoard_Master.xlsx")}
    packet = prep(env, top=2)
    assert [r["key"] for r in packet["rows"]] == ["k0000001", "k0000002"]
    assert packet["rows"][0]["jd_path"].endswith("JD.md") and packet["rows"][0]["rule_score"] == 90
    mark(out, ids["k0000001"], Status="Applied")
    excel.apply_reviews({}, now=NOW, out_dir=out, state_dir=env[1])             # refresh snapshot with the status
    packet = prep(env, top=2)
    assert [r["key"] for r in packet["rows"]] == ["k0000002", "k0000003"]       # Applied row skipped
    assert packet["skipped"]["status"] == 1


def test_apply_writes_why_gaps_without_touching_user_columns_or_order(env, tmp_path):
    out = env[0]
    ids = {r["Key"]: r["ID"] for r in rows(out / "JobBoard_Master.xlsx")}
    mark(out, ids["k0000002"], Status="Interested", Notes="call recruiter")
    prep(env, top=4)
    res = apply_json(env, [{"id": ids[k], "why": f"fits {k}", "gaps": f"gap {k}"} for k in ids], tmp_path)
    assert res["written"] == 4 and not res["changes"]
    got = {r["Key"]: r for r in rows(out / "JobBoard_Master.xlsx")}
    assert got["k0000001"]["Why it fits / Gaps"] == "Fits: fits k0000001\nGaps: gap k0000001"
    assert got["k0000001"]["Fit Score (0-100)"] == 90
    assert got["k0000002"]["Status"] == "Interested" and got["k0000002"]["Notes"] == "call recruiter"
    assert [r["Key"] for r in rows(out / "JobBoard_Master.xlsx")] == ["k0000001", "k0000002", "k0000003", "k0000004"]
    ws = load_workbook(out / "JobBoard_Master.xlsx")["Board"]
    assert ws.freeze_panes == "A2" and ws.cell(row=2, column=1).font.bold                # new-row bold survives


def test_valid_rerank_moves_row_and_records_reason(env, tmp_path):
    out, state, _ = env
    ids = {r["Key"]: r["ID"] for r in rows(out / "JobBoard_Master.xlsx")}
    prep(env, top=4)
    res = apply_json(env, [
        {"id": ids["k0000001"], "why": "pricing work matches Boeing", "gaps": "needs active CPA", "score": 70,
         "reason": "JD says a CPA license is mandatory", "evidence": "Must hold an active CPA license."},
        {"id": ids["k0000002"], "why": "ok", "gaps": "ok"}], tmp_path)
    assert res["changes"] == [{"id": ids["k0000001"], "company": "Acme", "title": "Financial Analyst", "from": 90.0,
                               "to": 70.0, "reason": "JD says a CPA license is mandatory",
                               "evidence": "Must hold an active CPA license."}]
    got = rows(out / "JobBoard_Master.xlsx")
    assert [r["Key"] for r in got] == ["k0000002", "k0000001", "k0000003", "k0000004"]      # 80, 70 (id 1), 70 (id 3), 60
    moved = next(r for r in got if r["Key"] == "k0000001")
    assert moved["Fit Score (0-100)"] == 70 and "reviewed: 90 -> 70" in moved["Flags"]
    saved = json.loads((state / "reviews.json").read_text(encoding="utf-8"))[str(ids["k0000001"])]
    assert saved["score"] == 70 and saved["rule_score"] == 90 and saved["evidence"].startswith("Must hold")


@pytest.mark.parametrize("change,problem", [
    ({"score": 80, "reason": "x", "evidence": "This quote is not in the JD."}, "not in JD.md"),
    ({"score": 60, "reason": "x", "evidence": "Must hold an active CPA license."}, "exceeds"),
    ({"score": 80, "reason": "", "evidence": "Must hold an active CPA license."}, "no reason"),
    ({"score": 80, "reason": "x", "evidence": ""}, "no JD quote"),
])
def test_unjustified_rerank_is_rejected_and_rule_score_kept(env, tmp_path, change, problem):
    out = env[0]
    rid = rows(out / "JobBoard_Master.xlsx")[0]["ID"]
    prep(env, top=1)
    res = apply_json(env, [{"id": rid, "why": "w", "gaps": "g", **change}], tmp_path)
    assert not res["changes"] and any(problem in w for w in res["warnings"])
    assert rows(out / "JobBoard_Master.xlsx")[0]["Fit Score (0-100)"] == 90
    assert rows(out / "JobBoard_Master.xlsx")[0]["Why it fits / Gaps"] == "Fits: w\nGaps: g"      # the lines still land


def test_lines_are_one_line_and_truncated(env, tmp_path):
    out = env[0]
    rid = rows(out / "JobBoard_Master.xlsx")[0]["ID"]
    prep(env, top=1)
    res = apply_json(env, [{"id": rid, "why": "line one\nline two " + "x" * 300, "gaps": "g"}], tmp_path)
    text = rows(out / "JobBoard_Master.xlsx")[0]["Why it fits / Gaps"]
    why = text.split("\nGaps:")[0]
    assert "\n" not in why.removeprefix("Fits: ") and len(why) <= len("Fits: ") + review.MAX_LINE
    assert any("truncated" in w for w in res["warnings"])


def test_unknown_id_and_missing_lines_are_skipped(env, tmp_path):
    prep(env, top=1)
    res = apply_json(env, [{"id": 999, "why": "w", "gaps": "g"}, {"id": 1, "why": "only why", "gaps": ""}], tmp_path)
    assert res["written"] == 0 and len(res["warnings"]) == 2


def test_review_survives_the_next_board_run(env, tmp_path):
    out, state, res = env
    ids = {r["Key"]: r["ID"] for r in rows(out / "JobBoard_Master.xlsx")}
    prep(env, top=4)
    apply_json(env, [{"id": ids["k0000001"], "why": "pricing fit", "gaps": "CPA", "score": 70,
                      "reason": "CPA mandatory", "evidence": "Must hold an active CPA license."}], tmp_path)
    excel.write_outputs(res, now=datetime(2026, 10, 30, 8, 0), out_dir=out, state_dir=state)
    row = next(r for r in rows(out / "JobBoard_Master.xlsx") if r["Key"] == "k0000001")
    assert row["Fit Score (0-100)"] == 70 and row["Why it fits / Gaps"] == "Fits: pricing fit\nGaps: CPA"
    assert "reviewed: 90 -> 70" in row["Flags"]


@pytest.mark.skipif(sys.platform != "win32", reason="file locking semantics are Windows-specific")
def test_apply_with_master_open_in_excel_saves_a_copy(env, tmp_path):
    out = env[0]
    rid = rows(out / "JobBoard_Master.xlsx")[0]["ID"]
    prep(env, top=1)
    master = out / "JobBoard_Master.xlsx"
    before = master.read_bytes()
    with open(master, "r+b"):
        res = apply_json(env, [{"id": rid, "why": "w", "gaps": "g"}], tmp_path)
    assert master.read_bytes() == before and any("open in Excel" in n for n in res["notes"])
    assert list(out.glob("JobBoard_Master_2026-10-29_*.xlsx"))
