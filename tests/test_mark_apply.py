import json
import shutil
import sys
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from board import applications, config, excel

NOW = datetime(2026, 10, 29, 9, 30)
DAY = date(2026, 10, 29)
HAS_RENDERER = bool(shutil.which("node")) and (config.career_ops_dir() / "node_modules" / "playwright").exists()

JD = ("# Financial Analyst II, Pricing — Acme\n\n- Source: fetch-jd\n\n---\n"
      "About the role\nAcme builds pricing tools for enterprise customers.\n"
      "Basic Qualifications:\n- 2 years of experience in FP&A or pricing.\n- Advanced Excel and SQL; variance analysis.\n"
      "Preferred Qualifications:\n- Tableau experience.\n")


def job(hid, title="Financial Analyst II", score=80.0):
    return {"id": hid, "first_seen": "2026-10-29", "years_bachelors": None, "score": score, "company": "Acme Robotics",
            "title": title, "location": "San Jose, CA", "url": f"https://jobs.example/{hid}", "posted": "2026-10-28",
            "age_days": 1, "date_approx": False, "metro": "bay_area", "work_mode": "onsite", "company_priority": 1,
            "jd_source": "fetch-jd", "jd_dir": "", "flags": [], "reasons": [], "years_required": 2.0, "years_evidence": "",
            "degree_required": None, "degree_evidence": "", "certs": [], "score_parts": {}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "applications_dir", lambda: tmp_path / "applications")
    monkeypatch.setattr(config, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(config, "data_root", lambda: tmp_path / "data")
    folder = tmp_path / "jobs" / "k0000001_Acme_Role"
    folder.mkdir(parents=True)
    (folder / "JD.md").write_text(JD, encoding="utf-8")
    hidden = job("k0000009", score=40)
    hidden["reasons"] = [{"rule": "years_required", "text": "5+ years required (limit 3)", "evidence": "5 years"}]
    res = {"run_date": "2026-10-29", "window_days": 4, "history_rows": 2, "selection": {}, "jd_fetch": {}, "hidden_by_rule": {},
           "shown": [job("k0000001")], "hidden": [hidden]}
    out, state = tmp_path / "output", tmp_path / "state"
    excel.write_outputs(res, now=NOW, out_dir=out, state_dir=state)
    return tmp_path, out


def board(out, path="JobBoard_Master.xlsx"):
    ws = load_workbook(out / path)["Board"]
    headers = [c.value for c in ws[1]]
    return [dict(zip(headers, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]


# ---- /mark ------------------------------------------------------------------------------------------

def test_mark_applied_sets_status_and_date_in_master_and_snapshot(env):
    _, out = env
    res = excel.mark_status(1, "applied", now=NOW, out_dir=out)
    assert res["status"] == "Applied" and res["before"]["Status"] == "New"
    for name in ("JobBoard_Master.xlsx", "JobBoard_2026-10-29.xlsx"):
        row = board(out, name)[0]
        assert row["Status"] == "Applied" and row["Applied Date"].date() == DAY


def test_mark_keeps_an_existing_applied_date_and_other_statuses_do_not_set_one(env):
    _, out = env
    excel.mark_status(1, "Applied", applied_date=date(2026, 10, 20), now=NOW, out_dir=out)
    excel.mark_status(1, "Interviewing", now=NOW, out_dir=out)
    row = board(out)[0]
    assert row["Status"] == "Interviewing" and row["Applied Date"].date() == date(2026, 10, 20)
    excel.mark_status(1, "Applied", now=datetime(2026, 10, 30), out_dir=out)
    assert board(out)[0]["Applied Date"].date() == date(2026, 10, 20)       # not overwritten


def test_mark_normalizes_status_and_rejects_bad_ones(env):
    _, out = env
    assert excel.mark_status(1, "docs ready", now=NOW, out_dir=out)["status"] == "Docs Ready"
    with pytest.raises(ValueError, match="unknown status"):
        excel.mark_status(1, "maybe", now=NOW, out_dir=out)


def test_mark_unknown_and_hidden_ids(env):
    _, out = env
    with pytest.raises(KeyError, match="not on the Board"):
        excel.mark_status(999, "Applied", now=NOW, out_dir=out)
    hidden_id = load_workbook(out / "JobBoard_Master.xlsx")["Hidden"]["A2"].value
    with pytest.raises(KeyError, match="Hidden sheet"):
        excel.mark_status(hidden_id, "Applied", now=NOW, out_dir=out)


def test_mark_keeps_notes_formatting_and_other_rows(env):
    _, out = env
    path = out / "JobBoard_Master.xlsx"
    wb = load_workbook(path)
    ws0 = wb["Board"]
    ws0.cell(row=2, column=[c.value for c in ws0[1]].index("Notes") + 1).value = "call recruiter"
    wb.save(path)
    excel.mark_status(1, "Interested", now=NOW, out_dir=out)
    ws = load_workbook(path)["Board"]
    assert ws.cell(row=2, column=[c.value for c in ws[1]].index("Notes") + 1).value == "call recruiter" and ws.freeze_panes == "A2" and ws["F2"].hyperlink is not None


@pytest.mark.skipif(sys.platform != "win32", reason="file locking semantics are Windows-specific")
def test_mark_with_master_open_saves_a_copy(env):
    _, out = env
    master = out / "JobBoard_Master.xlsx"
    before = master.read_bytes()
    with open(master, "r+b"):
        res = excel.mark_status(1, "Applied", now=NOW, out_dir=out)
    assert master.read_bytes() == before and any("open in Excel" in n for n in res["notes"])
    assert list(out.glob("JobBoard_Master_2026-10-29_*.xlsx"))


# ---- /apply: validation ---------------------------------------------------------------------------

GOOD = {
    "resume": {
        "summary": "Finance professional with a B.S. in Finance (3.88 GPA) who builds and defends pricing models and cost forecasts at Boeing, "
                   "with Excel, SQL and variance analysis experience.",
        "experience": {
            "boeing": [
                "Build and defend cost forecasts and pricing models totaling $500M+, running scenario and sensitivity analysis on cost, risk, and schedule drivers.",
                "Present pricing and labor-cost breakdowns to Program Management and Contracts leadership for sign-off on proposals ranging $2K-$50M.",
                "Consolidate fragmented data into unified financial models using Excel (XLOOKUP, Pivot Tables, Power Query, VBA), improving forecast accuracy.",
                "Author variance analysis documentation that passes formal external audit and internal peer review.",
            ],
            "forrest": ["Automated Excel-based forecasting and budgeting models, improving forecast accuracy by 20%."],
        },
        "skills": {"FP&A": ["Forecasting", "Variance Analysis", "Pricing and cost estimating"], "Tools": ["Excel (XLOOKUP, Pivot Tables, Power Query, VBA)", "SQL"]},
    },
    "cover_letter": {
        "salutation": "Dear Acme Robotics Hiring Team,",
        "paragraphs": [
            "I am applying for the Financial Analyst II, Pricing role at Acme Robotics. I am a finance professional with a B.S. in Finance from San Jose State "
            "University and I am completing a Master of Science in Computer Science at Johns Hopkins University.",
            "At Boeing I build and defend cost forecasts and pricing models totaling $500M+. I present pricing and labor-cost breakdowns to Program Management and "
            "Contracts leadership, and I run scenario and sensitivity analysis on cost, risk, and schedule drivers. That work maps directly to the pricing "
            "analysis Acme needs, and it relies on the same Excel and variance analysis skills your posting lists.",
            "I also automated Excel-based forecasting and budgeting models during my analyst work at Forrest Systems, improving forecast accuracy by 20%. "
            "I like turning operational data into financial models that leaders can act on, and I would bring that approach to your pricing team. "
            "At Boeing I partner with Engineering, Supply Chain, and Program Management to consolidate fragmented data into unified financial models, "
            "which improves forecast accuracy and shortens proposal turnaround. I document my assumptions so they pass internal peer review and formal audit, "
            "and I explain the drivers behind each number to the people who have to sign off on it.",
            "I would welcome the chance to discuss how my experience fits Acme Robotics. Thank you for your time and consideration.",
        ],
        "closing": "Sincerely,",
    },
}


def check(content, job_=None):
    return applications.validate_content(content, applications.load_candidate(), job_ or {"company": "Acme Robotics", "title": "Financial Analyst II"})


def mutate(path, value):
    c = json.loads(json.dumps(GOOD))
    node = c
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return c


def test_good_content_passes():
    assert check(GOOD) == []


def test_invented_number_is_rejected():
    errs = check(mutate(["resume", "experience", "boeing", 0], "Built pricing models totaling $900M+ across four product lines."))
    assert any("900" in e for e in errs)


def test_unheld_credentials_are_rejected_but_cfa_research_challenge_is_not():
    assert any("CFA" in e for e in check(mutate(["resume", "summary"], "Finance professional and CFA charterholder with Boeing pricing experience.")))
    assert any("Series" in e for e in check(mutate(["resume", "summary"], "Finance professional holding Series 7 and 63 licenses.")))
    ok = mutate(["resume", "experience", "forrest"], ["Led a 5-person team to a DCF-based investment thesis for the CFA Research Challenge."])
    assert check(ok) == []


def test_unsupported_skill_is_rejected():
    errs = check(mutate(["resume", "skills", "Tools"], ["Excel (XLOOKUP, Pivot Tables, Power Query, VBA)", "Tableau", "Hyperion"]))
    assert any("Tableau" in e for e in errs) and any("Hyperion" in e for e in errs)


def test_masters_must_not_be_written_as_completed():
    errs = check(mutate(["cover_letter", "paragraphs", 0], GOOD["cover_letter"]["paragraphs"][0].replace(
        "I am completing a Master of Science", "I earned a Master of Science")))
    assert any("in progress" in e for e in errs)


def test_unknown_job_missing_boeing_short_letter_and_company_mention():
    errs = check(mutate(["resume", "experience"], {"google": ["Did things."]}))
    assert any("unknown job id" in e for e in errs) and any("boeing" in e for e in errs)
    errs = check(mutate(["cover_letter", "paragraphs"], ["Short.", "Too short.", "Still short."]))
    assert any("words" in e for e in errs)
    errs = check(mutate(["cover_letter", "paragraphs", 3], "Thank you for your time."))
    assert errs == [] or any("words" in e for e in errs)
    errs = check(GOOD, {"company": "Zeta Corp", "title": "Analyst"})
    assert any("never mentions Zeta" in e for e in errs)


def test_html_has_fixed_facts_and_location():
    cand = applications.load_candidate()
    h = applications.resume_html(GOOD, cand, 10.5)
    assert "San Jose, CA" in h and "St. Louis County, MO" in h and "June 2025 - Present" in h
    assert "January 2026 - June 2028" in h and "Master of Science (MS), Computer Science" in h
    assert "Fintax" not in h                         # no bullets given for it, so the job is left out


# ---- /apply: prepare, render, finish -------------------------------------------------------------

def test_prepare_creates_folder_and_request(env):
    _, out = env
    req = applications.prepare(1, day=DAY, out_dir=out)
    folder = excel.application_folder(1)
    assert folder.name == "2026-10-29_Acme-Robotics_Financial-Analyst-II" and (folder / "job.json").exists()
    assert req["job"]["company"] == "Acme Robotics" and req["limits"]["job_ids"] == ["boeing", "fintax", "forrest"]
    groups = [k["group"] for k in req["keywords"]["mirror"]]
    assert "excel_vba" in groups and "sql" in groups and "variance_scenario" in groups
    assert any(k["group"] == "bi_tools" for k in req["keywords"]["do_not_claim"])      # Tableau: not supported
    assert applications.prepare(1, day=date(2026, 11, 2), out_dir=out)["folder"] == req["folder"]   # folder is reused


def test_render_rejects_bad_content_without_writing_pdfs(env):
    _, out = env
    applications.prepare(1, day=DAY, out_dir=out)
    folder = excel.application_folder(1)
    (folder / "content.json").write_text(json.dumps(mutate(["resume", "summary"], "CFA charterholder with $9M of experience.")), encoding="utf-8")
    with pytest.raises(applications.ApplyError, match="rejected"):
        applications.render(1, day=DAY)
    assert not list(folder.glob("*.pdf"))


@pytest.mark.skipif(not HAS_RENDERER, reason="node + career-ops Playwright not available")
def test_render_and_finish_end_to_end(env):
    _, out = env
    applications.prepare(1, day=DAY, out_dir=out)
    folder = excel.application_folder(1)
    (folder / "content.json").write_text(json.dumps(GOOD), encoding="utf-8")
    res = applications.render(1, day=DAY)
    assert res["ats"]["pages"] == 1 and res["ats"]["text_extractable"] and res["ats"]["name_and_location_present"]
    assert not res["ats"]["garbled_text"]
    assert applications._page_count(folder / "Shreyas_Kukanur_Cover_Letter_Acme_Robotics.pdf") == 1
    resume_text = applications.pdf_text(folder / "Shreyas_Kukanur_Resume_Acme_Robotics.pdf")
    assert "San Jose, CA" in resume_text and "Boeing" in resume_text and "$500M+" in resume_text
    letter_text = applications.pdf_text(folder / "Shreyas_Kukanur_Cover_Letter_Acme_Robotics.pdf")
    assert "Re: Financial Analyst II" in letter_text and "October 29, 2026" in letter_text
    assert "sql" not in res["jd_skill_groups_not_in_documents"]

    (folder / "GapCheck.md").write_text("gaps", encoding="utf-8")
    done = applications.finish(1, now=NOW, out_dir=out)
    row = board(out)[0]
    assert row["Status"] == "Docs Ready" and done["status"] == "Docs Ready"
    ws = load_workbook(out / "JobBoard_Master.xlsx")["Board"]
    hdr = [c.value for c in ws[1]]
    rc, lc = hdr.index("Resume") + 1, hdr.index("Cover Letter") + 1
    assert ws.cell(row=2, column=rc).hyperlink.target.endswith("Resume_Acme_Robotics.pdf")
    assert ws.cell(row=2, column=lc).hyperlink.target.endswith("Cover_Letter_Acme_Robotics.pdf")
    # the next board run keeps the links and the status
    assert excel._find_pdf(1, "resume").endswith("Resume_Acme_Robotics.pdf")


@pytest.mark.skipif(not HAS_RENDERER, reason="node + career-ops Playwright not available")
def test_finish_does_not_downgrade_an_applied_role(env):
    _, out = env
    applications.prepare(1, day=DAY, out_dir=out)
    folder = excel.application_folder(1)
    (folder / "content.json").write_text(json.dumps(GOOD), encoding="utf-8")
    applications.render(1, day=DAY)
    excel.mark_status(1, "Applied", now=NOW, out_dir=out)
    done = applications.finish(1, now=NOW, out_dir=out)
    assert board(out)[0]["Status"] == "Applied" and "left as Applied" in done["status_note"]


def test_overlong_resume_is_refused(env, monkeypatch):
    _, out = env
    applications.prepare(1, day=DAY, out_dir=out)
    monkeypatch.setattr(applications, "html_to_pdf", lambda h, p: p.write_bytes(b"x"))
    monkeypatch.setattr(applications, "_page_count", lambda p: 2)
    with pytest.raises(applications.ApplyError, match="one page"):
        applications.render_one_page(lambda fs: "<html></html>", applications.FONT_SIZES_RESUME, out / "x.pdf")
