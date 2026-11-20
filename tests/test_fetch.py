"""Fetchers use the right date field per ATS, filter before detail calls, route undated roles, and never print responses.
No network: http_json is replaced by canned responses."""
import json
from datetime import datetime, timedelta

import pytest

from board import dates
from board.dates import CHICAGO, UTC
from board.fetch import ats, base, boards, fetch_all, fetch_board, prefilter, save_result, scanmjs
from board.models import HistoryRow

START = datetime(2026, 11, 15, 10, 0, tzinfo=CHICAGO).astimezone(UTC)


def iso(delta):
    return (START - delta).isoformat()


def fake_http(monkeypatch, responses):
    """responses: {url substring: json}. Records every URL requested."""
    calls = []

    def fake(url, data=None, **kw):
        calls.append(url)
        for frag, body in responses.items():
            if frag in url:
                return body
        raise base.NoBoard("HTTP 404")

    monkeypatch.setattr(ats, "http_json", fake)
    return calls


GH = {"name": "Acme", "key": "acme"}


def gh_job(i, title, loc="San Jose, CA", first=None, updated=None):
    return {"id": i, "title": title, "location": {"name": loc}, "absolute_url": f"https://x/{i}",
            "first_published": first, "updated_at": updated}


def test_greenhouse_list_endpoint_supplies_first_published_not_updated_at(monkeypatch):
    calls = fake_http(monkeypatch, {"/jobs": {"jobs": [
        gh_job(1, "Financial Analyst", first=iso(timedelta(days=9)), updated=iso(timedelta(hours=2))),   # old but just edited
        gh_job(2, "FP&A Analyst", first=iso(timedelta(days=1)), updated=iso(timedelta(days=1))),
        gh_job(3, "Treasury Analyst", first=None, updated=iso(timedelta(hours=5)))]}})
    raw, jobs = ats.list_greenhouse(GH, START, base.RawStore(None))
    assert raw == 3 and len(calls) == 1                  # one list call, no per-job call for dates
    pre = prefilter.Prefilter(START, 4)
    by = {j.ext_id: j for j in jobs}
    assert pre(by["1"]) == ("date", "Posted 2026-11-06, window is 4 days")     # real publish date wins over updated_at
    assert pre(by["2"]) is None
    assert pre(by["3"]) is None and by["3"].posted.flag == "updated_at only"    # undated: kept for "Date unconfirmed"


def test_detail_calls_only_for_survivors(monkeypatch):
    calls = fake_http(monkeypatch, {
        "/jobs/2": {"content": "&lt;p&gt;3 years of experience. $90,000 - $110,000&lt;/p&gt;", "departments": [{"name": "Finance"}]},
        "/jobs": {"jobs": [
            gh_job(1, "Software Engineer", first=iso(timedelta(days=1))),                       # negative title
            gh_job(2, "Financial Analyst", first=iso(timedelta(days=1))),                       # survivor
            gh_job(3, "Financial Analyst", loc="London, UK", first=iso(timedelta(days=1))),     # outside metros
            gh_job(4, "Financial Analyst", first=iso(timedelta(days=10)))]}})                    # too old
    log, kept, _ = fetch_board({**GH, "ats": "greenhouse"}, START, base.RawStore(None), prefilter.Prefilter(START, 4))
    assert [j.ext_id for j in kept] == ["2"]
    assert sum("/jobs/" in c for c in calls) == 1        # only job 2 got a detail call
    assert "3 years" in kept[0].description and kept[0].pay == "$90,000-$110,000" and kept[0].department == "Finance"
    assert log.raw == 4 and log.kept == 1 and log.dropped == {"title": 1, "location": 1, "date": 1}


def test_title_not_on_list_goes_to_review_only_when_finance_ish(monkeypatch):
    fake_http(monkeypatch, {"/jobs": {"jobs": [
        gh_job(1, "Business Intelligence Analyst", first=iso(timedelta(days=1))),     # analyst: review
        gh_job(2, "Office Dog Walker", first=iso(timedelta(days=1)))]}})              # nothing finance-ish: silent drop
    log, kept, review = fetch_board({**GH, "ats": "greenhouse"}, START, base.RawStore(None), prefilter.Prefilter(START, 4))
    assert kept == [] and [r[0].ext_id for r in review] == ["1"] and review[0][2] == "title not on list"
    assert log.dropped == {"title not on list": 2}


def test_lever_uses_created_at_ms(monkeypatch):
    ms = lambda d: int((START - d).timestamp() * 1000)          # noqa: E731
    fake_http(monkeypatch, {"lever.co": [
        {"id": "a", "text": "Financial Analyst", "createdAt": ms(timedelta(days=2)), "hostedUrl": "u1",
         "categories": {"location": "New York, NY", "allLocations": ["New York, NY"]}, "descriptionPlain": "d", "lists": []},
        {"id": "b", "text": "Financial Analyst", "createdAt": ms(timedelta(days=4, hours=1)), "hostedUrl": "u2",
         "categories": {"location": "New York, NY"}, "descriptionPlain": "d", "lists": []}]})
    _, jobs = ats.list_lever({"name": "L", "key": "l"}, START, base.RawStore(None))
    pre = prefilter.Prefilter(START, 4)
    assert [pre(j) is None for j in jobs] == [True, False] and jobs[0].posted.source == "createdAt"


def test_ashby_uses_published_at(monkeypatch):
    fake_http(monkeypatch, {"ashbyhq": {"jobs": [
        {"id": "1", "title": "Finance Analyst", "location": "Austin, TX", "publishedAt": iso(timedelta(days=2)),
         "jobUrl": "u", "descriptionPlain": "x"}]}})
    _, jobs = ats.list_ashby({"name": "A", "key": "a"}, START, base.RawStore(None))
    assert jobs[0].posted.source == "publishedAt" and jobs[0].posted.confirmed


def test_workday_posted_on_becomes_a_date(monkeypatch):
    fake_http(monkeypatch, {"/jobs": {"total": 3, "jobPostings": [
        {"title": "Financial Analyst", "externalPath": "/job/a", "locationsText": "San Jose, CA", "postedOn": "Posted Today"},
        {"title": "Financial Analyst II", "externalPath": "/job/b", "locationsText": "2 Locations", "postedOn": "Posted 30+ Days Ago"},
        {"title": "Finance Analyst", "externalPath": "/job/c", "locationsText": "Seattle, WA", "postedOn": ""}]}})
    _, jobs = ats.list_workday({"name": "W", "tenant": "w", "wd": "5", "site": "ext"}, START, base.RawStore(None))
    pre = prefilter.Prefilter(START, 4)
    by = {j.ext_id: j for j in jobs}
    assert pre(by["/job/a"]) is None and by["/job/a"].posted.confirmed
    assert pre(by["/job/b"])[0] == "date"                       # 30+ days is confirmed old
    assert pre(by["/job/c"]) is None and not by["/job/c"].posted.confirmed


def test_other_ats_date_fields():
    j = {"name": "X"}
    sr = dates.from_iso("2026-11-14T08:00:00.000Z", "releasedDate")
    assert sr.confirmed
    assert dates.from_epoch(1763000000, "postedTs").source == "postedTs"          # Eightfold
    assert dates.from_us_date("November 14, 2026", "posted_date").precision == "day"   # Amazon
    assert dates.from_iso("2026-11-14", "PostedDate").precision == "day"          # Oracle
    assert j


def test_unconfirmed_roles_that_fail_role_filters_are_dropped():
    from board.fetch.base import RawJob
    pre = prefilter.Prefilter(START, 4)
    undated = dates.unconfirmed("none", "no date field")
    bad = RawJob("A", "greenhouse", "1", "Director, Financial Analysis", "San Jose, CA", "u", undated)
    good = RawJob("A", "greenhouse", "2", "Financial Analyst", "San Jose, CA", "u", undated)
    assert pre(bad)[0] == "title" and pre(good) is None


def test_board_errors_are_logged_not_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ats, "http_json", boom)
    log, kept, _ = fetch_board({**GH, "ats": "greenhouse"}, START, base.RawStore(None), prefilter.Prefilter(START, 4))
    assert log.status == "ERROR" and "down" in log.note and kept == []
    log2, _, _ = fetch_board({"name": "X", "ats": "unsupported", "note": "no slug"}, START, base.RawStore(None),
                             prefilter.Prefilter(START, 4))
    assert log2.status == "UNSUPPORTED"


def test_raw_responses_go_to_files_never_stdout(monkeypatch, tmp_path, capsys):
    import io
    import urllib.request

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = json.dumps({"jobs": [{"id": 1}]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: Resp(body))
    store = base.RawStore(tmp_path / "raw")
    assert base.http_json("https://example.test/x", store=store, tag="greenhouse_acme") == {"jobs": [{"id": 1}]}
    files = list((tmp_path / "raw").glob("*.json"))
    assert len(files) == 1 and files[0].read_bytes() == body
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_scanmjs_without_posted_at_goes_to_unconfirmed_with_first_seen(tmp_path):
    from board import ingest
    cols = ["url", "first_seen", "portal", "title", "company", "status", "location", "fingerprint", "posted_at"]
    rows = [
        ["https://jobs.apple.com/1", "2026-11-14", "Apple", "Financial Analyst", "Apple", "added", "Cupertino, CA", "f1", ""],
        ["https://jobs.apple.com/2", "2026-11-01", "Apple", "Financial Analyst", "Apple", "added", "Cupertino, CA", "f2", ""],
        ["https://jobs.apple.com/3", "2026-11-14", "Apple", "Pricing Analyst", "Apple", "added", "Cupertino, CA", "f3", "2026-11-13"]]
    hist = tmp_path / "scan-history.tsv"
    hist.write_text("\t".join(cols) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    seen, jobs, stale = scanmjs.load({"Apple"}, START, 4, hist)
    by = {j.url: j for j in jobs}
    assert seen == 3 and stale == 1 and set(by) == {"https://jobs.apple.com/1", "https://jobs.apple.com/3"}
    assert by["https://jobs.apple.com/1"].posted.flag == "first seen 2026-11-14"
    assert by["https://jobs.apple.com/3"].posted.confirmed
    assert ingest and HistoryRow


@pytest.mark.parametrize("entry,expect", [
    ({"name": "A", "ats": "greenhouse", "careers_url": "https://job-boards.greenhouse.io/anthropic"}, ("greenhouse", "anthropic")),
    ({"name": "A", "ats": "Ashby", "careers_url": "https://jobs.ashbyhq.com/plaid"}, ("ashby", "plaid")),
    ({"name": "A", "ats": "lever", "careers_url": "https://jobs.lever.co/dadavidson"}, ("lever", "dadavidson")),
    ({"name": "A", "ats": "Workday", "careers_url": "https://adobe.wd5.myworkdayjobs.com/external_experienced"}, ("workday", "adobe")),
    ({"name": "A", "ats": "workday", "careers_url": "https://x.wd1.myworkdayjobs.com/en-US/Global"}, ("workday", "x")),
    ({"name": "A", "ats": "Apple (career-ops provider)", "careers_url": "https://jobs.apple.com"}, ("scanmjs", None)),
    ({"name": "A", "ats": "greenhouse", "careers_url": "https://example.com"}, ("unsupported", None)),
])
def test_parse_board(entry, expect):
    b = boards.parse_board(entry)
    assert (b["ats"], b.get("key")) == expect


def test_parse_board_workday_parts_and_eightfold_oracle():
    wd = boards.parse_board({"name": "A", "ats": "Workday", "careers_url": "https://x.wd5.myworkdayjobs.com/en-US/Site1"})
    assert (wd["tenant"], wd["wd"], wd["site"]) == ("x", "5", "Site1")
    ef = boards.parse_board({"name": "M", "ats": "Eightfold", "careers_url": "https://apply.careers.microsoft.com/careers?domain=microsoft.com"})
    assert (ef["host"], ef["domain"]) == ("apply.careers.microsoft.com", "microsoft.com")
    orc = boards.parse_board({"name": "O", "ats": "Oracle HCM", "careers_url": "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/jobs"})
    assert (orc["host"], orc["site"]) == ("eeho.fa.us2.oraclecloud.com", "CX_45001")


def test_real_companies_yaml_has_a_fetcher_or_a_logged_reason():
    bs = boards.load_boards()
    assert bs and all(b["ats"] in ats.LIST or b["ats"] in ("scanmjs", "unsupported") for b in bs)
    assert all(b.get("note") for b in bs if b["ats"] == "unsupported")


def test_fetch_all_runs_in_parallel_and_saves(monkeypatch, tmp_path):
    monkeypatch.setattr("board.fetch.load_boards", lambda only=None: [
        {"name": "Acme", "ats": "greenhouse", "key": "acme"}, {"name": "Dead", "ats": "greenhouse", "key": "dead"}])
    fake_http(monkeypatch, {"boards/acme/jobs/": {"content": "d"}, "boards/acme/jobs": {"jobs": [
        gh_job(1, "Financial Analyst", first=iso(timedelta(days=1)))]}})
    res = fetch_all(START, raw_dir=tmp_path / "raw", history=tmp_path / "none.tsv")
    assert [j.title for j in res.jobs] == ["Financial Analyst"] and [b.name for b in res.failed] == ["Dead"]
    path = save_result(res, tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["days"] == 4 and body["jobs"][0]["posted"]["when"].startswith("2026-11-14")
    assert res.elapsed >= 0


@pytest.mark.parametrize("title,company,expect", [
    ("Asset Management - Everyday 401K Internal Client Advisor - Associate", "JPMorgan Chase", "sales like"),      # from the 2026-11-15 smoke run
    ("Asset Management - Internal Client Advisor - Retirement Plan Specialist - Associate", "JPMorgan Chase", "sales like"),
    ("Credit Underwriter I/Credit Officer I-Technology", "Bank of America", "wrong function"),      # from the 2026-11-15 trial run
    ("Commercial Associate – Global Commercial Banking - Dealer Financial Services - (Dallas, Chicago, Houston, Charlotte)",
     "Bank of America", "wrong function"),                                                          # from the 2026-11-19 board
    ("Credit Portfolio Analyst II", "Citi", "wrong function"),                                      # from the 2026-11-17 board
    ("Cost Estimator", "Acme", "moves backward"),
    ("Tax Analyst", "Acme", "wrong function"),
    ("Financial Analyst", "Robert Half", "staffing"),
    ("Operations Coordinator", "Acme", "title not on list"),
    ("Senior Financial Analyst", "Acme", None),                 # Senior is decided by the years rule in the JD stage
    ("Sr. FP&A Analyst", "Acme", None),
    ("Senior Manager, FP&A", "Acme", "title"),                  # a managerial word still drops it
    ("Treasury Analyst", "Acme", None),
])
def test_prefilter_rules(title, company, expect):
    from board.fetch.base import RawJob
    pre = prefilter.Prefilter(START, 4)
    job = RawJob(company, "greenhouse", "1", title, "New York, NY", "u", dates.from_iso(iso(timedelta(days=1)), "first_published"))
    got = pre(job)
    assert (got[0] if got else None) == expect, (title, got)


# ---- rate limits, shared Workday limit, server-side keyword queries ---------------------------------------------------

def test_429_is_retried_with_backoff_then_succeeds(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    sleeps, calls = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        calls.append(1)
        if len(calls) <= 2:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "7"} if len(calls) == 1 else {}, None)
        return Resp(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    assert base.http_json("https://example.test/x") == {"ok": True}
    assert len(calls) == 3 and sleeps[0] >= 7 and 4 <= sleeps[1] < 5.1        # Retry-After honoured, then 2 * 2 ** 1 + jitter


def test_429_gives_up_after_three_retries_with_rate_limited(monkeypatch):
    import urllib.error
    import urllib.request

    calls = []

    def fake_open(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    with pytest.raises(base.RateLimited):
        base.http_json("https://example.test/x")
    assert len(calls) == 4                                                      # the first try plus 3 retries


def test_all_workday_hosts_share_one_concurrency_limit():
    a = base._sem("expedia.wd108.myworkdayjobs.com")
    b = base._sem("forrester.wd501.myworkdayjobs.com")
    assert a is b and base._limit_key("x.wd5.myworkdayjobs.com") == ("workday", 4)
    assert base._sem("boards-api.greenhouse.io") is not a and base._limit_key("boards-api.greenhouse.io")[1] == base.PER_HOST


def test_keywords_come_from_config_and_full_uses_an_empty_query(monkeypatch):
    from board import config as cfg
    assert "finance" in ats._keywords({}) and "FP&A" in ats._keywords({}) and len(ats._keywords({})) >= 8
    assert ats._keywords({"full": True}) == [""]
    seen = []

    def fake(url, data=None, **kw):
        seen.append((data or {}).get("searchText"))
        return {"total": 0, "jobPostings": []}

    monkeypatch.setattr(ats, "http_json", fake)
    wd = {"name": "W", "tenant": "w", "wd": "5", "site": "ext"}
    ats.list_workday(wd, START, base.RawStore(None))
    assert seen == cfg.filters_config()["fetch"]["keywords"]                      # server-side searchText, one query per keyword
    seen.clear()
    ats.list_workday({**wd, "full": True}, START, base.RawStore(None))
    assert seen == [""]


def test_workday_results_from_several_queries_are_deduped_by_job_id(monkeypatch):
    def fake(url, data=None, **kw):
        kw_ = data["searchText"]
        posts = [{"title": "Financial Analyst", "externalPath": "/job/same", "locationsText": "San Jose, CA", "postedOn": "Posted Today"}]
        if kw_ == "pricing":
            posts.append({"title": "Pricing Analyst", "externalPath": "/job/other", "locationsText": "San Jose, CA", "postedOn": "Posted Today"})
        return {"total": len(posts), "jobPostings": posts}

    monkeypatch.setattr(ats, "http_json", fake)
    raw, jobs = ats.list_workday({"name": "W", "tenant": "w", "wd": "5", "site": "ext"}, START, base.RawStore(None))
    assert raw == 2 and sorted(j.ext_id for j in jobs) == ["/job/other", "/job/same"]


def test_netflix_entry_now_has_an_eightfold_domain():
    from board import config as cfg
    entry = next(c for c in cfg.companies_config() if c["name"] == "Netflix")
    b = boards.parse_board(entry)
    assert b["ats"] == "eightfold" and b["domain"] == "netflix.com" and b["host"] == "explore.jobs.netflix.net"


def test_time_spent_queued_behind_a_shared_limit_does_not_count_against_the_board_limit():
    import time as _t
    base.set_deadline(0.2)
    base.credit_wait(1.0)                       # waited a second for a Workday slot
    _t.sleep(0.3)
    base.check_deadline()                       # still inside the limit: only request time counts
    assert not base.was_partial()
    base.set_deadline(0.05)
    _t.sleep(0.1)
    with pytest.raises(base.BoardTimeout):
        base.check_deadline()
    base.set_deadline(None)


def test_rate_limited_boards_get_a_cooldown_pass_and_only_then_count_as_failed(monkeypatch, tmp_path):
    from board import fetch as fetch_pkg
    monkeypatch.setattr(fetch_pkg, "load_boards", lambda only=None: [
        {"name": "Flaky", "ats": "greenhouse", "key": "flaky"}, {"name": "Stuck", "ats": "greenhouse", "key": "stuck"},
        {"name": "Fine", "ats": "greenhouse", "key": "fine"}])
    attempts = {"flaky": 0}
    slept = []

    def fake(url, data=None, **kw):
        if "boards/flaky/jobs/" in url or "boards/fine/jobs/" in url:
            return {"content": "d"}
        if "boards/flaky/jobs" in url:
            attempts["flaky"] += 1
            if attempts["flaky"] == 1:
                raise base.RateLimited("HTTP 429 after 3 retries")
            return {"jobs": [gh_job(1, "Financial Analyst", first=iso(timedelta(days=1)))]}
        if "boards/stuck/jobs" in url:
            raise base.RateLimited("HTTP 429 after 3 retries")
        return {"jobs": [gh_job(2, "FP&A Analyst", first=iso(timedelta(days=1)))]}

    monkeypatch.setattr(ats, "http_json", fake)
    monkeypatch.setattr(fetch_pkg.time, "sleep", lambda s: slept.append(s))
    res = fetch_all(START, raw_dir=tmp_path / "raw", history=tmp_path / "none.tsv")
    assert sorted(j.title for j in res.jobs) == ["FP&A Analyst", "Financial Analyst"]          # Flaky recovered on the second pass
    assert [b.name for b in res.failed] == ["Stuck"] and "cool-down" in res.failed[0].note
    assert slept and slept[0] == 60
