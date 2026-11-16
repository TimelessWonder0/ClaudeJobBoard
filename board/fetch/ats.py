"""One list function and one detail function per ATS.

`list_<ats>(board, start, store)` returns `(raw_count, [RawJob])` using only cheap list calls. Where the list has no
job description the job's `detail` dict carries what `detail_<ats>` needs, and the detail call runs only for jobs that
survive the date, metro and title filters. The date field each ATS uses is named in the comment above its function.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime

from .. import dates
from .base import NoBoard, RawJob, RawStore, find_salary, http_json, to_text


def _q(s: str) -> str:
    return urllib.parse.quote(s)


# Greenhouse. Date: `first_published` (the list endpoint returns it, so no per-job call is needed for the date).
# `updated_at` is never used as a posting date; a job with only updated_at is "Date unconfirmed (updated_at only)".
def list_greenhouse(b, start: datetime, store: RawStore):
    slug = b["key"]
    jobs = http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=90, store=store,
                     tag=f"greenhouse_{slug}")["jobs"]
    out = []
    for x in jobs:
        if x.get("first_published"):
            posted = dates.from_iso(x["first_published"], "first_published")
        elif x.get("updated_at"):
            posted = dates.unconfirmed("updated_at", "updated_at only")
        else:
            posted = dates.unconfirmed("none", "no date field")
        out.append(RawJob(b["name"], "greenhouse", str(x["id"]), x.get("title", ""), (x.get("location") or {}).get("name", ""),
                          x.get("absolute_url", ""), posted, detail={"slug": slug, "id": x["id"]}))
    return len(jobs), out


def detail_greenhouse(job: RawJob, store: RawStore) -> None:
    d = job.detail
    j = http_json(f"https://boards-api.greenhouse.io/v1/boards/{d['slug']}/jobs/{d['id']}")
    job.description = to_text(j.get("content"))
    job.department = "; ".join(x.get("name", "") for x in j.get("departments") or [])
    job.pay = find_salary(job.description)


# Lever. Date: `createdAt` (ms since epoch). The list already carries the description.
def list_lever(b, start: datetime, store: RawStore):
    jobs = http_json(f"https://api.lever.co/v0/postings/{b['key']}?mode=json", timeout=60, store=store,
                     tag=f"lever_{b['key']}")
    if not isinstance(jobs, list):
        raise NoBoard("not a list")
    out = []
    for x in jobs:
        cat = x.get("categories") or {}
        parts = [x.get("descriptionPlain", "")]
        for lst in x.get("lists") or []:
            parts.append(lst.get("text", "") + "\n" + to_text(lst.get("content")))
        parts.append(x.get("additionalPlain", ""))
        sr = x.get("salaryRange") or {}
        pay = f"${sr['min']:,.0f}-${sr['max']:,.0f}" if sr.get("min") and sr.get("max") else ""
        desc = "\n".join(parts)
        out.append(RawJob(b["name"], "lever", str(x.get("id", "")), x.get("text", ""),
                          ", ".join(cat.get("allLocations") or [cat.get("location", "")]), x.get("hostedUrl", ""),
                          dates.from_epoch(x.get("createdAt"), "createdAt", millis=True),
                          department=cat.get("team", "") or "", workplace=x.get("workplaceType", "") or "",
                          pay=pay or find_salary(desc), description=desc))
    return len(jobs), out


# Ashby. Date: `publishedAt`. The list already carries the description. A browser-like UA is not needed for this endpoint.
def list_ashby(b, start: datetime, store: RawStore):
    jobs = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{b['key']}?includeCompensation=true", timeout=60,
                     store=store, tag=f"ashby_{b['key']}")["jobs"]
    out = []
    for x in jobs:
        sec = [s.get("location", "") for s in x.get("secondaryLocations") or []]
        hint = "Remote" if x.get("isRemote") else (x.get("workplaceType") or "")
        desc = x.get("descriptionPlain") or to_text(x.get("descriptionHtml"))
        comp = (x.get("compensation") or {}).get("compensationTierSummary", "")
        out.append(RawJob(b["name"], "ashby", str(x.get("id", "")), x.get("title", ""),
                          "; ".join([x.get("location", "")] + sec), x.get("jobUrl", ""),
                          dates.from_iso(x.get("publishedAt"), "publishedAt"), department=x.get("department", "") or "",
                          workplace=hint, pay=comp or find_salary(desc), description=desc))
    return len(jobs), out


# Workday. Date: `postedOn` ("Posted Today" / "Posted 3 Days Ago" / "Posted 30+ Days Ago") turned into a date.
# Searches are keyword based (the endpoint pages 20 at a time) and merged by `externalPath`.
WD_KEYWORDS = ["finance", "FP&A", "financial analyst", "pricing", "deal desk", "strategic finance"]


def list_workday(b, start: datetime, store: RawStore):
    t, n, site = b["tenant"], b["wd"], b["site"]
    host = f"{t}.wd{n}.myworkdayjobs.com"
    api = f"https://{host}/wday/cxs/{t}/{site}/jobs"
    seen: dict[str, dict] = {}
    for kw in WD_KEYWORDS:
        off, total = 0, None
        while True:
            try:
                res = http_json(api, {"appliedFacets": {}, "limit": 20, "offset": off, "searchText": kw}, timeout=25,
                                store=store if off == 0 else None, tag=f"workday_{t}_{kw}")
            except NoBoard:
                if off == 0 and not seen:
                    raise
                break
            except Exception:                       # noqa: BLE001 - keep what earlier pages returned
                if off == 0 and not seen:
                    raise
                break
            posts = res.get("jobPostings") or []
            if total is None:
                total = res.get("total") or 0      # Workday reports total on the first page only
            for p in posts:
                if p.get("externalPath"):
                    seen[p["externalPath"]] = p
            off += 20
            if not posts or off >= total or off >= 1000:
                break
    out = []
    for path, p in seen.items():
        out.append(RawJob(b["name"], "workday", path, p.get("title", ""), p.get("locationsText", ""),
                          f"https://{host}/en-US/{site}{path}", dates.from_workday(p.get("postedOn"), start),
                          detail={"host": host, "t": t, "site": site, "path": path}))
    return len(seen), out


def detail_workday(job: RawJob, store: RawStore) -> None:
    d = job.detail
    j = http_json(f"https://{d['host']}/wday/cxs/{d['t']}/{d['site']}{d['path']}")["jobPostingInfo"]
    job.description = to_text(j.get("jobDescription"))
    extra = "; ".join(j.get("additionalLocations") or [])
    job.location = "; ".join(x for x in (j.get("location", ""), extra) if x)
    job.workplace = j.get("remoteType", "") or ""
    job.pay = find_salary(job.description)


# SmartRecruiters. Date: `releasedDate`. Keyword searches merged by id.
def list_smartrecruiters(b, start: datetime, store: RawStore):
    cid = b["key"]
    seen: dict[str, dict] = {}
    for kw in ("finance", "FP&A", "financial analyst", "pricing"):
        off = 0
        while off < 500:
            data = http_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=100&offset={off}&q={_q(kw)}",
                             store=store if off == 0 else None, tag=f"smartrecruiters_{cid}_{kw}")
            content = data.get("content") or []
            for p in content:
                seen[p["id"]] = p
            off += 100
            if off >= data.get("totalFound", 0) or not content:
                break
    if not seen:
        raise NoBoard("no postings")
    out = []
    for p in seen.values():
        loc = p.get("location") or {}
        out.append(RawJob(b["name"], "smartrecruiters", str(p["id"]), p.get("name", ""),
                          loc.get("fullLocation") or loc.get("city", ""), p.get("ref", ""),
                          dates.from_iso(p.get("releasedDate"), "releasedDate"),
                          department=(p.get("department") or {}).get("label", ""),
                          workplace="Remote" if loc.get("remote") else "", detail={"cid": cid, "id": p["id"]}))
    return len(seen), out


def detail_smartrecruiters(job: RawJob, store: RawStore) -> None:
    d = job.detail
    j = http_json(f"https://api.smartrecruiters.com/v1/companies/{d['cid']}/postings/{d['id']}")
    secs = (j.get("jobAd") or {}).get("sections") or {}
    job.description = "\n".join(to_text((secs.get(k) or {}).get("text"))
                                for k in ("jobDescription", "qualifications", "additionalInformation"))
    job.url = j.get("postingUrl") or job.url
    job.pay = find_salary(job.description)


# Amazon. Date: `posted_date` ("November 11, 2026"), day precision. The list carries the description.
AMZ_KW = ["financial analyst", "finance analyst", "FP&A", "pricing analyst", "strategic finance", "business finance",
          "corporate development", "finance associate"]


def list_amazon(b, start: datetime, store: RawStore):
    seen: dict[str, dict] = {}
    for kw in AMZ_KW:
        off = 0
        while off < 1000:
            res = http_json(f"https://www.amazon.jobs/en/search.json?base_query={_q(kw)}&country=USA&result_limit=100"
                            f"&offset={off}&sort=recent", timeout=60, store=store if off == 0 else None,
                            tag=f"amazon_{kw}")
            jobs = res.get("jobs") or []
            for j in jobs:
                seen[j["id_icims"]] = j
            off += 100
            if len(jobs) < 100 or off >= res.get("hits", 0):
                break
    out = []
    for j in seen.values():
        desc = "\n".join([to_text(j.get("description")), "Basic qualifications:\n" + to_text(j.get("basic_qualifications")),
                          "Preferred qualifications:\n" + to_text(j.get("preferred_qualifications"))])
        out.append(RawJob(b["name"], "amazon", str(j["id_icims"]), j.get("title", ""),
                          j.get("normalized_location") or j.get("location", ""), "https://www.amazon.jobs" + j.get("job_path", ""),
                          dates.from_us_date(j.get("posted_date"), "posted_date"), department=j.get("job_category", "") or "",
                          pay=find_salary(desc), description=desc))
    return len(seen), out


# Eightfold. Date: `postedTs` (epoch seconds).
EF_KW = ["finance", "FP&A", "financial analyst", "pricing", "strategic finance", "corporate development"]


def list_eightfold(b, start: datetime, store: RawStore):
    host, domain = b["host"], b["domain"]
    seen: dict[str, dict] = {}
    for kw in EF_KW:
        pos_start = 0
        while pos_start < 300:
            res = http_json(f"https://{host}/api/pcsx/search?domain={domain}&query={_q(kw)}&location=United%20States"
                            f"&start={pos_start}&sort_by=timestamp", timeout=45, store=store if pos_start == 0 else None,
                            tag=f"eightfold_{host}_{kw}")
            pos = (res.get("data") or {}).get("positions") or []
            time.sleep(1.0)                        # Eightfold rate-limits (429)
            for p in pos:
                seen[str(p["id"])] = p
            pos_start += len(pos)
            oldest = min((p.get("postedTs", 0) for p in pos), default=0)
            if not pos or (oldest and (start.timestamp() - oldest) / 86400 > 30):
                break
    if not seen:
        raise NoBoard("no data")
    out = []
    for p in seen.values():
        out.append(RawJob(b["name"], "eightfold", str(p["id"]), p.get("name", ""),
                          "; ".join(p.get("standardizedLocations") or p.get("locations") or []),
                          f"https://{host}{p.get('positionUrl', '')}", dates.from_epoch(p.get("postedTs"), "postedTs"),
                          workplace=p.get("workLocationOption") or "", detail={"host": host, "domain": domain, "id": p["id"]}))
    return len(seen), out


def detail_eightfold(job: RawJob, store: RawStore) -> None:
    d = job.detail
    time.sleep(0.5)
    det = http_json(f"https://{d['host']}/api/pcsx/position_details?position_id={d['id']}&domain={d['domain']}&hl=en")["data"]
    job.description = to_text(det.get("jobDescription"))
    job.pay = find_salary(job.description)


# Oracle HCM. Date: `PostedDate` (a date, day precision).
ORA_KW = ["finance", "financial analyst", "FP&A", "pricing", "valuation", "investment banking analyst", "research"]


def list_oracle(b, start: datetime, store: RawStore):
    host, site = b["host"], b["site"]
    seen: dict[str, dict] = {}
    for kw in ORA_KW:
        off = 0
        while off < 500:
            u = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
                 f"&finder=findReqs;siteNumber={site},keyword=%22{_q(kw)}%22,limit=50,offset={off},sortBy=POSTING_DATES_DESC")
            res = http_json(u, timeout=60, store=store if off == 0 else None, tag=f"oracle_{host}_{kw}")
            items = (res.get("items") or [{}])[0].get("requisitionList") or []
            for r in items:
                seen[str(r["Id"])] = r
            off += 50
            if len(items) < 50:
                break
    out = []
    for r in seen.values():
        out.append(RawJob(b["name"], "oracle", str(r["Id"]), r.get("Title", ""), r.get("PrimaryLocation", ""),
                          f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}",
                          dates.from_iso(r.get("PostedDate"), "PostedDate"), workplace=r.get("WorkplaceType") or "",
                          description=r.get("ShortDescriptionStr") or "", detail={"host": host, "site": site, "id": r["Id"]}))
    return len(seen), out


def detail_oracle(job: RawJob, store: RawStore) -> None:
    d = job.detail
    det = http_json(f"https://{d['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                    f"&finder=ById;Id=%22{d['id']}%22,siteNumber={d['site']}")
    it = (det.get("items") or [{}])[0]
    text = "\n".join(to_text(str(v)) for k, v in it.items()
                     if isinstance(v, str) and re.search(r"(Description|Qualifications|Responsibilities)Str$", k))
    job.description = text or job.description
    job.pay = find_salary(job.description)


LIST = dict(greenhouse=list_greenhouse, lever=list_lever, ashby=list_ashby, workday=list_workday,
            smartrecruiters=list_smartrecruiters, amazon=list_amazon, eightfold=list_eightfold, oracle=list_oracle)
DETAIL = dict(greenhouse=detail_greenhouse, workday=detail_workday, smartrecruiters=detail_smartrecruiters,
              eightfold=detail_eightfold, oracle=detail_oracle)     # lever, ashby and amazon lists already carry the JD
