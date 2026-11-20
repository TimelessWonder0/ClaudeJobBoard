"""Changes after the 2026-11-19 run: locations from the JD, the Workday Finance facet, "Previously reviewed" in jobs_latest.md,
and the graduating-class eligibility line. No network."""
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from board import excel, filters, markdown, reviews, write_review
from board.fetch import ats, base, boards, workday_facets

from .helpers import run_jobs
from .test_board_outputs import DAY, jobs_for_cutoff
from .test_fetch import START

NOW = datetime(2026, 11, 15, 12, 30, 5)


# ---- unknown locations: the JD's own "Location:" line -----------------------------------------------------------------

@pytest.mark.parametrize("jd,expect,status", [
    ("Finance Analyst\n\nLocation: San Jose, California, US | RTP, North Carolina, US\n\nAbout", "San Jose, California, US; RTP, North Carolina, US", "match"),   # Cisco, 2026-11-19
    ("# T — PNC\n\n- Location: 3 Locations\n\n---\nLocation: PA - Pittsburgh (15222) | TX - Houston (77056) | OH - Cleveland\n",
     "PA - Pittsburgh (15222); TX - Houston (77056); OH - Cleveland", "outside"),                                                   # PNC, 2026-11-19
    ("# T\n\n- Location: Los Angeles, California; New York\n", "Los Angeles, California; New York", "match"),                       # Oaktree header
    ("- Location: 2 Locations\n\nNo city anywhere.", "", None),
    ("Location Expectations: three days a week onsite.", "", None),
])
def test_jd_location_reads_the_city_list(jd, expect, status):
    got = filters.jd_location(jd)
    assert got == expect
    if status:
        assert filters.classify_location(got)[0] == status


def test_a_vague_listing_location_is_replaced_from_the_jd(tmp_path, monkeypatch):
    from board import pipeline
    from board.fetch.base import RawJob
    from board.dates import from_iso

    rj = RawJob("Cisco", "workday", "/job/x", "Finance Analyst", "2 Locations", "https://x/job/x",
                from_iso(START.isoformat(), "postedOn"))
    rj.description = "Finance Analyst\nLocation: San Jose, California, US | RTP, North Carolina, US\n\nBasic Qualifications:\n- 2 years of experience."
    monkeypatch.setattr(pipeline.jd, "save_jd", lambda job, text, src: (setattr(job, "jd_text", text), setattr(job, "jd_source", src)))
    job = pipeline._job_from_raw(rj, START)
    pipeline.phase_a(job, rj, refresh=False, fetch_missing=False, with_claude=False)
    assert job.location.startswith("San Jose") and job.metro == "bay_area"
    assert "location unknown" not in job.flags and any(f.startswith("location from JD") for f in job.flags)


# ---- commercial banking and credit portfolio are wrong function ---------------------------------------------------------

@pytest.mark.parametrize("title,blocked", [
    ("Commercial Associate – Global Commercial Banking - Dealer Financial Services - (Dallas, Chicago, Houston, Charlotte)", True),  # BofA
    ("Credit Portfolio Analyst II", True),                                                                                         # Citi
    ("Relationship Banking Analyst", True),
    ("Commercial Finance Analyst", False), ("Financial Analyst, Commercial", False),
])
def test_commercial_banking_titles(title, blocked):
    got = filters.title_blocked(title)
    assert (got is not None and got[0] == "wrong_function") == blocked, (title, got)


# ---- Workday Finance facet --------------------------------------------------------------------------------------------

FACETS = [
    {"facetParameter": "jobFamilyGroup", "descriptor": "Job Category", "values": [
        {"descriptor": "Finance", "id": "fin1", "count": 14}, {"descriptor": "Sales", "id": "s1", "count": 40},
        {"descriptor": "Non-financial Risk", "id": "r1", "count": 3}, {"descriptor": "Capital Markets", "id": "cm1", "count": 6}]},
    {"facetParameter": "locationMainGroup", "values": [{"descriptor": "Locations", "values": []}]},
]


def test_pick_facet_takes_the_finance_categories_only():
    got = workday_facets.pick_facet(FACETS)
    assert got == {"param": "jobFamilyGroup", "ids": ["fin1", "cm1"], "names": ["Finance", "Capital Markets"], "jobs": 20}


def test_pick_facet_falls_back_to_job_family_then_none():
    fam = [{"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Operations", "id": "o", "count": 1}]},
           {"facetParameter": "jobFamily", "values": [{"descriptor": "Financial Planning & Strategy", "id": "f", "count": 2}]}]
    assert workday_facets.pick_facet(fam)["param"] == "jobFamily"
    assert workday_facets.pick_facet([{"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineering", "id": "e"}]}]) is None
    assert workday_facets.pick_facet([]) is None


def test_write_cache_edits_only_the_named_lines_and_replaces_an_old_value(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("companies:\n  # a comment\n"
                    "  - {name: NVIDIA, careers_url: 'https://nvidia.wd5.myworkdayjobs.com/X', ats: Workday, scan_status: OK}\n"
                    "  - {name: PNC, careers_url: 'https://pnc.wd5.myworkdayjobs.com/External', ats: workday, scan_status: OK, workday_facet: {param: none, as_of: '2026-01-01'}}\n"
                    "  - {name: Stripe, careers_url: 'https://job-boards.greenhouse.io/stripe', ats: greenhouse, scan_status: OK}\n",
                    encoding="utf-8")
    n = workday_facets.write_cache({"NVIDIA": {"param": "jobFamilyGroup", "ids": ["a1"], "names": ["Finance"], "jobs": 3, "as_of": "2026-11-20"},
                                    "PNC": {"param": "jobFamilyGroup", "ids": ["p1", "p2"], "names": ["Finance & Accounting", "Capital Markets"],
                                            "jobs": 9, "as_of": "2026-11-20"}}, path)
    text = path.read_text(encoding="utf-8")
    data = {c["name"]: c for c in yaml.safe_load(text)["companies"]}
    assert n == 2 and "# a comment" in text and text.count("workday_facet") == 2
    assert data["NVIDIA"]["workday_facet"]["ids"] == ["a1"] and data["PNC"]["workday_facet"]["ids"] == ["p1", "p2"]
    assert "workday_facet" not in data["Stripe"]
    assert boards.parse_board(data["PNC"])["facet"]["param"] == "jobFamilyGroup"


def _workday_fake(monkeypatch, by_query):
    """by_query: searchText -> list of externalPaths returned. Records every body sent."""
    sent = []

    def fake(url, data=None, **kw):
        sent.append(data)
        paths = by_query.get(data["searchText"], [])
        return {"total": len(paths), "jobPostings": [{"title": "Financial Analyst", "externalPath": p, "locationsText": "San Jose, CA",
                                                      "postedOn": "Posted Today"} for p in paths]}

    monkeypatch.setattr(ats, "http_json", fake)
    return sent


def test_a_facet_tenant_runs_the_facet_query_and_the_fallback_keywords_deduped(monkeypatch):
    from board import config
    fallback = config.filters_config()["fetch"]["workday_fallback_keywords"]
    # the facet finds /a and /b; "financial analyst" finds /b again plus /c, filed under another category (Cisco-style)
    sent = _workday_fake(monkeypatch, {"": ["/job/a", "/job/b"], fallback[0]: ["/job/b", "/job/c"]})
    b = {"name": "W", "ats": "workday", "tenant": "w", "wd": "5", "site": "ext",
         "facet": {"param": "jobFamilyGroup", "ids": ["fin1"], "names": ["Finance"]}}
    raw, jobs = ats.list_workday(b, START, base.RawStore(None))
    assert sent[0] == {"appliedFacets": {"jobFamilyGroup": ["fin1"]}, "limit": 20, "offset": 0, "searchText": ""}
    assert [(d["appliedFacets"], d["searchText"]) for d in sent[1:]] == [({}, kw) for kw in fallback]   # keywords without the facet
    assert raw == 3 and sorted(j.ext_id for j in jobs) == ["/job/a", "/job/b", "/job/c"]


def test_a_tenant_without_a_finance_facet_runs_keywords_only(monkeypatch):
    from board import config
    fallback = config.filters_config()["fetch"]["workday_fallback_keywords"]
    sent = _workday_fake(monkeypatch, {})
    b = {"name": "W", "ats": "workday", "tenant": "w", "wd": "5", "site": "ext", "facet": {"param": "none"}}
    ats.list_workday(b, START, base.RawStore(None))
    assert [(d["appliedFacets"], d["searchText"]) for d in sent] == [({}, kw) for kw in fallback]


def test_workday_keywords_without_a_facet():
    from board import config
    fcfg = config.filters_config()["fetch"]
    wd = {"ats": "workday"}
    assert ats._keywords(wd) == fcfg["workday_keywords"]                                          # never discovered: unchanged
    assert ats._keywords({**wd, "facet": {"param": "none"}}) == fcfg["workday_fallback_keywords"]  # no finance category: short list
    with_facet = {**wd, "facet": {"param": "jobFamilyGroup", "ids": ["x"]}}
    assert ats._workday_queries(with_facet) == [({"jobFamilyGroup": ["x"]}, "")] + [({}, k) for k in fcfg["workday_fallback_keywords"]]
    assert ats._workday_queries(wd) == [({}, k) for k in fcfg["workday_keywords"]]
    assert len(fcfg["workday_fallback_keywords"]) < len(fcfg["workday_keywords"])
    assert ats._keywords({**wd, "full": True, "facet": {"param": "jobFamilyGroup", "ids": ["x"]}}) == [""]
    assert workday_facets.facet_query({**wd, "full": True, "facet": {"param": "jobFamilyGroup", "ids": ["x"]}}) is None


def test_discovery_is_paced_and_keeps_going_after_a_failure(monkeypatch, tmp_path):
    entries = [({"name": n}, {"name": n, "ats": "workday", "tenant": n.lower(), "wd": "1", "site": "s"}) for n in ("A", "B", "C")]
    monkeypatch.setattr(workday_facets, "workday_entries", lambda only, refresh: entries)
    sleeps = []
    monkeypatch.setattr(workday_facets, "polite_sleep", lambda s: sleeps.append(s))
    written = {}
    monkeypatch.setattr(workday_facets, "write_cache", lambda found, path=None: written.update(found) or len(found))

    def fake(url, data=None, **kw):
        assert data["searchText"] == "" and data["appliedFacets"] == {}
        if "/b/" in url:
            raise base.NoBoard("HTTP 404")
        return {"facets": FACETS if "/a/" in url else []}

    monkeypatch.setattr(workday_facets, "http_json", fake)
    res = workday_facets.run(delay=2.5, raw_dir=None, say=lambda *_: None)
    assert sleeps == [2.5, 2.5]
    assert written["A"]["param"] == "jobFamilyGroup" and written["C"]["param"] == "none" and "B" not in written
    assert [n for n, _ in res["failed"]] == ["B"]


def test_dry_run_writes_nothing(monkeypatch):
    entries = [({"name": "A"}, {"name": "A", "ats": "workday", "tenant": "a", "wd": "1", "site": "s"})]
    monkeypatch.setattr(workday_facets, "workday_entries", lambda only, refresh: entries)
    monkeypatch.setattr(workday_facets, "http_json", lambda url, data=None, **kw: {"facets": FACETS})
    monkeypatch.setattr(workday_facets, "write_cache", lambda *a, **k: pytest.fail("dry run wrote the cache"))
    assert workday_facets.run(dry_run=True, say=lambda *_: None)["found"]["A"]["ids"] == ["fin1", "cm1"]


# ---- Previously reviewed --------------------------------------------------------------------------------------------

@pytest.fixture
def board(tmp_path, monkeypatch):
    res = run_jobs(tmp_path, monkeypatch, jobs_for_cutoff(), DAY)
    out = excel.write_outputs(res, now=datetime(2026, 11, 15, 10, 0), out_dir=tmp_path / "out", state_dir=tmp_path / "state")
    return tmp_path, res, out


def _md(t, res, out, seen):
    latest, _ = markdown.write(res, out["ids"], out["board_nums"], out_dir=t / "out", meta=out["meta"], reviews=seen)
    return latest, markdown.parse(latest.read_text(encoding="utf-8"))


def test_first_seen_and_previously_reviewed_lines_round_trip(board):
    t, res, out = board
    _, parsed = _md(t, res, out, {})
    roles = parsed["board"] + parsed["unconfirmed"]
    assert roles and all(r["first_seen"] == "2026-11-15" and r["previously_reviewed"] is False for r in roles)
    first = roles[0]["id"]
    _, parsed = _md(t, res, out, {first: {"date": "2026-11-14", "verdict": "Strong"}})
    r0 = next(r for r in parsed["board"] + parsed["unconfirmed"] if r["id"] == first)
    assert r0["previously_reviewed"] and r0["reviewed_note"] == "reviewed 2026-11-14 (Strong)"
    assert parsed["header"]["previously reviewed"].startswith("1 of ")


def test_a_status_other_than_new_counts_as_reviewed(board):
    t, res, out = board
    num = out["board_nums"][0]
    out["meta"][num]["status"] = "Applied"
    _, parsed = _md(t, res, out, {})
    r = next(r for r in parsed["board"] + parsed["unconfirmed"] if r["id"] == num)
    assert r["previously_reviewed"] and r["reviewed_note"] == "status Applied"


def test_write_review_records_verdicts_and_the_next_board_skips_them(board):
    t, res, out = board
    latest, parsed = _md(t, res, out, {})
    roles = parsed["board"] + parsed["unconfirmed"]
    v = {"verdicts": [{"id": r["id"], "verdict": "Strong", "why": "w", "gap": "g"} for r in roles]}
    write_review.write_review(v, latest, t / "rv", NOW, state_dir=t / "state")
    seen = reviews.load(t / "state", t / "rv")
    assert set(seen) == {r["id"] for r in roles} and all(s == {"date": "2026-11-15", "verdict": "Strong"} for s in seen.values())

    latest, parsed = _md(t, res, out, seen)                     # the next run
    assert all(r["previously_reviewed"] for r in parsed["board"] + parsed["unconfirmed"])
    res2 = write_review.write_review({"verdicts": [{"id": roles[0]["id"], "verdict": "Skip", "why": "x", "gap": ""}]},
                                     latest, t / "rv", NOW, state_dir=t / "state")
    assert any("already reviewed" in w for w in res2["warnings"])
    assert not any("no verdict" in w for w in res2["warnings"])               # reviewed roles need no verdict
    assert res2["counts"] == {"picks": 0, "date_unconfirmed": 0, "passed_over": len(roles)}
    assert reviews.load(t / "state", t / "rv")[roles[0]["id"]]["verdict"] == "Strong"   # the first review is kept


def test_reviewed_roles_do_not_use_up_the_cap():
    from .test_write_review import _role
    roles = [_role(i, 90 - i) for i in range(1, 36)]
    for r in roles[:5]:
        r.update(previously_reviewed=True, reviewed_note="reviewed 2026-11-19 (Strong)")
    board = {"header": {}, "board": roles, "unconfirmed": [], "hidden": []}
    parts = write_review.build_rows(board, {i: {"verdict": "Stretch", "why": "w", "gap": "g"} for i in range(6, 36)})
    assert len(parts["picks"]) == 30 and parts["warnings"] == []
    assert {r["id"]: r["reason"] for r in parts["passed"]} == {i: "reviewed 2026-11-19 (Strong)" for i in range(1, 6)}


def test_old_review_workbooks_are_folded_in_once(board):
    t, res, out = board
    latest, parsed = _md(t, res, out, {})
    roles = parsed["board"] + parsed["unconfirmed"]
    v = {"verdicts": [{"id": r["id"], "verdict": "Skip" if i else "Apply now", "why": "w", "gap": ""} for i, r in enumerate(roles)]}
    path = Path(write_review.write_review(v, latest, t / "rv", NOW, state_dir=t / "other")["path"])    # as if written before the ledger
    seen = reviews.load(t / "state", t / "rv")
    assert seen[roles[0]["id"]]["verdict"] == "Apply now" and all(seen[r["id"]]["verdict"] == "Skip" for r in roles[1:])
    assert path.name in (t / "state" / "reviews.json").read_text(encoding="utf-8")
    assert reviews.load(t / "state", t / "rv") == seen


def test_below_cap_rows_in_an_old_workbook_are_not_reviews(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Passed Over"
    ws.append(["ID", "Score", "Title", "Company", "City", "Reason"])
    ws.append([7, 55, "T", "C", "X", "below cap (Python score 55)"])
    ws.append([8, 70, "T", "C", "X", "Sales-adjacent role"])
    (tmp_path / "rv").mkdir()
    wb.save(tmp_path / "rv" / "Claude_Review_2026-11-19.xlsx")
    assert reviews.load(tmp_path / "state", tmp_path / "rv") == {8: {"date": "2026-11-19", "verdict": "Skip"}}


# ---- graduating class -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title,jd,expect", [
    ("Capital Markets Specialist I (2027 Early Career Program)",
     "Intro.\n On track to graduate in Spring 2027 with a degree in Finance, Economics, or a related field \n",
     "On track to graduate in Spring 2027 with a degree in Finance, Economics, or a related field"),               # Affirm, 2026-11-19
    ("Analyst, Class of 2026", "Join us.", "title names a dated program (Class of 2026); JD states no graduation window"),
    ("Financial Analyst", "Founded in 1985, we have 40 years of history. A bachelor's degree is required.", ""),
])
def test_eligibility_line(title, jd, expect):
    assert markdown.eligibility_line(title, jd) == expect


# ---- graduating class: seasons, for the flag text only ----------------------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("On track to graduate in Spring 2027 with a degree in Finance", ("Spring 2027", 2027, 5)),
    ("Graduating Fall 2026", ("Fall 2026", 2026, 12)),
    ("Fall '26 graduates welcome", ("Fall 2026", 2026, 12)),
    ("Summer 2027 graduation", ("Summer 2027", 2027, 8)),
    ("Open to the Class of 2027", ("Class of 2027", 2027, 5)),
    ("2026 graduates only", ("Class of 2026", 2026, 5)),
    ("Expected graduation in December 2026", ("December 2026", 2026, 12)),
    ("Capital Markets Specialist I (2027 Early Career Program)", ("Class of 2027 (from the title)", 2027, 5)),
    ("Founded in 2020, our team has grown.", None),
    ("Investment Banking - 2027 ReEntry Program", None),                        # a dated program that is not a graduating class
])
def test_parse_grad_class(text, expect):
    assert markdown.parse_grad_class(text) == expect


GRADS = [("MS expected Jun 2028", 2028, 6, True), ("BS Dec 2024", 2024, 12, False)]


@pytest.mark.parametrize("title,jd,expect", [
    ("Capital Markets Specialist I (2027 Early Career Program)", "x\n On track to graduate in Spring 2027 with a degree in Finance\n",
     "Spring 2027 — likely ineligible (MS expected Jun 2028; BS Dec 2024)"),                              # Affirm, 2026-11-19
    ("Analyst", "Candidates graduating in Fall 2024 are welcome.", "Fall 2024 — possible fit: close to BS Dec 2024"),
    ("New Grad Analyst", "Expected graduation: Spring 2028.",
     "Spring 2028 — possible fit: close to MS expected Jun 2028 (degree in progress; check the program takes it)"),
    ("Financial Analyst", "Bachelor's degree required.", ""),
])
def test_grad_class_line_compares_with_the_candidate(title, jd, expect):
    assert markdown.grad_class_line(title, jd, GRADS) == expect


def test_candidate_graduations_come_from_candidate_yaml():
    labels = [g[0] for g in markdown.candidate_graduations()]
    assert "BS Dec 2024" in labels and any(l.startswith("MS") and "2028" in l for l in labels)


def test_grad_class_never_hides_a_role(board):
    t, res, out = board
    job = res["shown"][0]
    job["grad_class"] = "Spring 2027 — likely ineligible (MS expected Jun 2028; BS Dec 2024)"
    _, parsed = _md(t, res, out, {})
    r = next(r for r in parsed["board"] + parsed["unconfirmed"] if r["title"] == job["title"])
    assert r["grad_class"].startswith("Spring 2027 — likely ineligible") and not job["reasons"]
