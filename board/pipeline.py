"""python -m board run: the negative-filter funnel.

  1 pull everything (scan-history, every job the scan recorded)   2 posted in the window   3 location (8 metros)
  4 negative titles   5 finance relevance (generous; Claude judges what keywords cannot)
  -- steps 1-5 use only the title and location, so they are free; a JD is fetched only for what survives --
  6 experience / degree / license from the JD, cross-checked with career-ops jd-years.mjs
  7 career-ops: liveness, evaluation (1-5), hide low fit; then sort

Every posting that drops out is recorded with its step and reason (Hidden sheet and output\\Dropped_<date>.csv).
"""
from __future__ import annotations

import csv
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from . import careerops, classify, config, dates, fetch, funnel, ingest, jd, markdown, score
from .extract import extract
from .filters import apply_filters, classify_location, jd_location
from .fetch.base import RawJob
from .models import HistoryRow, Job, Reason

_RULE_STEP = {"closed": 7, "careerops_low_fit": 7, "score": 7, "metro": 3, "title_not_on_list": 5}


class Gate:
    """The external calls the pipeline makes. Tests replace these with fakes."""
    years = staticmethod(careerops.years_crosscheck)
    keywords = staticmethod(careerops.keyword_overlap)
    liveness = staticmethod(careerops.check_liveness)
    evaluate = staticmethod(careerops.evaluate)
    classify = staticmethod(classify.classify_titles)


def _facts_json(job: Job) -> dict:
    return {
        "id": job.id, "url": job.url, "company": job.company, "title": job.title, "location": job.location,
        "posted": job.date_used.isoformat() if job.date_used else "", "date_approx": job.date_approx,
        "jd_source": job.jd_source, "metro": job.metro, "work_mode": job.work_mode,
        "hidden": job.hidden, "reasons": [r.__dict__ for r in job.reasons], "flags": job.flags,
        "score": job.score, "score_parts": job.score_parts, "careerops": job.careerops, "extraction": job.extraction,
    }


def _write_facts(job: Job) -> None:
    if job.jd_dir and job.jd_source != "none":
        job.jd_dir.mkdir(parents=True, exist_ok=True)
        (job.jd_dir / "facts.json").write_text(json.dumps(_facts_json(job), indent=2, ensure_ascii=False), encoding="utf-8")


def run_gate(jobs: list[Job], cache: dict, refresh_careerops: bool, workers: int) -> dict:
    """Step 7 on the survivors of step 6. Mutates jobs (reasons, careerops, flags); returns a summary."""
    cfg = config.load_yaml("careerops.yaml")
    cap = int(funnel.funnel_config().get("max_evaluations_per_run", 25))
    summary = {"checked": 0, "evaluated": 0, "cached": 0, "closed": 0, "low_fit": 0, "not_evaluated": 0, "errors": []}
    alive = [j for j in jobs if not j.hidden]
    summary["checked"] = len(alive)

    if cfg.get("liveness") and alive:
        verdicts = Gate.liveness([j.url for j in alive])
        for j in alive:
            v = verdicts.get(j.url)
            j.careerops["live"] = v or "unchecked"
            if v == "expired":
                j.reasons.append(Reason("closed", "career-ops: posting is closed", j.url))
                summary["closed"] += 1
    alive = [j for j in jobs if not j.hidden]

    for j in alive:
        kw = Gate.keywords(j.jd_text)
        if kw:
            j.careerops["keywords"] = {"total": kw.get("total"), "coveragePct": kw.get("coveragePct"), "missing": kw.get("missing", [])}
        score.score_job(j, j.extraction)            # Fit Score decides who is evaluated first when there is a cap

    todo: list[Job] = []
    for j in alive:
        hit = cache.get(j.id)
        if hit and hit.get("score") is not None and not refresh_careerops and Path(hit.get("report", "")).exists():
            j.careerops.update(score=hit["score"], report=hit["report"])
            summary["cached"] += 1
        else:
            todo.append(j)
    todo.sort(key=lambda j: -(j.score or 0))
    for j in todo[cap:]:
        j.flags.append("career-ops not evaluated yet")
        summary["not_evaluated"] += 1
    todo = todo[:cap]

    def one(j: Job) -> tuple[Job, dict]:
        return j, Gate.evaluate({"key": j.id, "url": j.url, "company": j.company, "title": j.title,
                                 "location": j.location, "jd_text": j.jd_text})

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, int(cfg["evaluate"].get("workers", 2)))) as pool:
            for j, res in pool.map(one, todo):
                if "score" in res:
                    j.careerops.update(score=res["score"], report=res["report"])
                    cache[j.id] = {"score": res["score"], "report": res["report"], "url": j.url,
                                   "evaluated_on": date.today().isoformat()}
                    summary["evaluated"] += 1
                else:
                    j.careerops["error"] = res.get("error", "unknown error")
                    j.flags.append("career-ops evaluation unavailable")
                    summary["errors"].append({"company": j.company, "title": j.title, "error": j.careerops["error"]})

    minimum = float(cfg["min_score"])
    for j in alive:
        s = j.careerops.get("score")
        if s is not None and s < minimum:
            j.reasons.append(Reason("careerops_low_fit", f"career-ops: low fit ({s:g}/5, minimum {minimum:g})",
                                    j.careerops.get("report", "")))
            summary["low_fit"] += 1
    return summary


def _fit_key(j: Job):
    s = j.careerops.get("score")
    return (-(s if s is not None else -1), -(j.score or 0), j.company)


def _step_of(job: Job) -> int:
    return min(_RULE_STEP.get(r.rule, 6) for r in job.reasons) if job.reasons else 0


def _public(job: Job) -> dict:
    ext = job.extraction or {}
    yr = ext.get("years_required") or {}
    deg = ext.get("degree_required") or {}
    eff = job.years_effective if job.years_effective is not None else yr.get("effective_years", yr.get("min_years"))
    return {
        "id": job.id, "first_seen": job.row.first_seen, "years_bachelors": yr.get("years_bachelors"),
        "score": job.score, "company": job.company, "title": job.title, "location": job.location,
        "url": job.url, "posted": job.date_used.isoformat() if job.date_used else "", "age_days": job.age_days,
        "date_approx": job.date_approx, "metro": job.metro, "work_mode": job.work_mode,
        "company_priority": job.company_priority, "jd_source": job.jd_source,
        "jd_dir": str(job.jd_dir) if job.jd_dir else "",
        "flags": job.flags, "reasons": [r.__dict__ for r in job.reasons], "step": _step_of(job),
        "years_required": eff, "years_evidence": yr.get("evidence", ""),
        "degree_required": deg.get("level"), "degree_evidence": deg.get("evidence", ""),
        "certs": ext.get("certs", []), "score_parts": job.score_parts,
        "careerops_score": job.careerops.get("score"), "careerops_report": job.careerops.get("report", ""),
        "careerops": job.careerops,
        "track": job.score_parts.get("track", ""), "tier": score.tier_for(job.score or 0), "training": job.score_parts.get("training_phrases", []),
        "date_flag": job.date_flag, "pay": job.pay, "workplace": job.workplace,
        "requirements": markdown.requirement_lines(job.jd_text), "eligibility": markdown.eligibility_line(job.title, job.jd_text),
        "grad_class": markdown.grad_class_line(job.title, job.jd_text),
        "jd_path": str(job.jd_dir / "JD.md") if job.jd_dir and job.jd_source != "none" else "",
    }


def _dropped_record(d: funnel.Drop) -> dict:
    p = d.posting
    return {"id": p.pid, "company": p.company, "title": p.title, "location": p.location, "url": p.row.url,
            "posted": p.used.isoformat() if p.used and not p.approx else "", "first_seen": p.row.first_seen,
            "step": d.step, "step_name": d.step_name, "rule": d.rule, "reason": d.reason, "evidence": d.evidence}


def _scan_summary(receipt: dict | None) -> dict:
    if not receipt:
        return {}
    return {"scanned": receipt.get("scanned"), "found": receipt.get("found"), "filtered": receipt.get("filtered"),
            "added": receipt.get("added"), "errors": receipt.get("errors", []), "unverified_zero": receipt.get("unverified_zero", [])}


def write_dropped_csv(path: Path, dropped: list[dict], detailed: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["step", "step_name", "company", "title", "location", "posted", "reason", "evidence", "url"])
        for d in dropped:
            w.writerow([d["step"], d["step_name"], d["company"], d["title"], d["location"], d["posted"], d["reason"], d["evidence"], d["url"]])
        for j in detailed:
            w.writerow([j["step"], funnel.STEP_NAMES.get(j["step"], ""), j["company"], j["title"], j["location"], j["posted"],
                        "; ".join(r["text"] for r in j["reasons"]), next((r["evidence"] for r in j["reasons"] if r["evidence"]), ""), j["url"]])


def _norm_key(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", "|".join(parts).lower())


def _job_from_raw(rj: RawJob, start: datetime) -> Job:
    p = rj.posted
    today = dates.chicago_date(start)
    used = dates.chicago_date(p.when) if p.confirmed else None
    row = HistoryRow(url=rj.url, first_seen=today.isoformat(), portal=rj.company, title=rj.title, company=rj.company,
                     status="added", location=rj.location, posted_at=used.isoformat() if used else "", requisition_id=rj.ext_id)
    job = Job(id=ingest.job_id(row), row=row, company=rj.company, title=rj.title, location=rj.location, url=rj.url,
              portal=rj.company, date_used=used, date_approx=not p.confirmed,
              age_days=(today - used).days if used else None)
    job.date_flag = "" if p.confirmed else (p.flag or "no date field")
    job.pay, job.workplace, job.flags = rj.pay, rj.workplace, list(rj.flags)
    if job.date_flag:
        job.flags.append("date unconfirmed: " + job.date_flag)
    return job


def phase_a(job: Job, rj: RawJob, refresh: bool, fetch_missing: bool, with_claude: bool) -> None:
    """Step 6 for one posting: the JD (from the API listing, the cache, or a fetch), extraction, and the JD-based rules."""
    if (rj.description or "").strip():
        jd.save_jd(job, rj.description, "api")
    else:
        jd.load_or_fetch(job, refresh=refresh, fetch=fetch_missing)
    # The listing said only "2 Locations" or "United States": take the cities from the JD's own "Location:" line, so the
    # metro rule judges the real places (outside every metro is hidden with an ID, as for any other outside location).
    if classify_location(job.location)[0] == "unknown" and not re.search(r"remote", job.location or "", re.I):
        found = jd_location(job.jd_text)
        if found and classify_location(found)[0] != "unknown":
            job.flags.append(f"location from JD (listing said '{job.location or 'blank'}')")
            job.location = found
    ext = extract(job.jd_text, job.location)
    job.extraction = ext.to_dict()
    job.company_priority = score.company_priority(job.portal, job.company)
    co_years, co_confident = None, False
    if with_claude and config.load_yaml("careerops.yaml").get("years_crosscheck") and job.jd_text:
        got = Gate.years(job.jd_text)
        if got:
            job.careerops["years"] = got
            if got.get("status") == "stated" and got.get("min") is not None:
                co_years, co_confident = float(got["min"]), bool(got.get("confident"))
    apply_filters(job, ext, careerops_years=co_years, careerops_years_confident=co_confident, skip_title=True)


def _dedupe(pairs: list[tuple[Job, RawJob]]) -> tuple[list[tuple[Job, RawJob]], int]:
    """Same posting seen twice (same URL, or same company + title + location): keep the one with a confirmed date."""
    best: dict[str, tuple[Job, RawJob]] = {}
    for job, rj in pairs:
        for key in (job.id, _norm_key(job.company, job.title, job.location)):
            cur = best.get(key)
            if cur is None:
                best[key] = (job, rj)
            elif rj.posted.confirmed and not cur[1].posted.confirmed:
                best[key] = (job, rj)
    keep: dict[str, tuple[Job, RawJob]] = {}
    for job, rj in pairs:
        winner = best[job.id]
        alt = best[_norm_key(job.company, job.title, job.location)]
        if alt[1].posted.confirmed and not winner[1].posted.confirmed:
            winner = alt
        keep[winner[0].id] = winner
    return list(keep.values()), len(pairs) - len(keep)


_PRE_STEP = {"location": 3, "title": 4, "sales like": 4, "moves backward": 4, "wrong function": 4, "staffing": 4,
             "title not on list": 5}


def _funnel_counts(res: fetch.FetchResult, jobs: list[Job]) -> dict:
    counts = funnel.new_counts()
    for b in res.boards:
        if b.status in ("UNSUPPORTED",):
            continue
        d = b.dropped
        title_drops = sum(n for k, n in d.items() if _PRE_STEP.get(k) == 4)
        row = counts.setdefault(b.name, {s: 0 for s in funnel.STAGES})
        row["pulled"] = b.raw
        row["after_date"] = b.raw - d.get("date", 0)
        row["after_location"] = row["after_date"] - d.get("location", 0)
        row["after_titles"] = row["after_location"] - title_drops
        row["after_finance"] = row["after_titles"] - d.get("title not on list", 0)
    for j in jobs:
        row = counts.setdefault(j.company, {s: 0 for s in funnel.STAGES})
        if not any(_step_of_rule(r.rule) == 6 for r in j.reasons):
            row["after_years"] += 1
        if not j.hidden:
            row["after_fit"] += 1
    return counts


def _step_of_rule(rule: str) -> int:
    return _RULE_STEP.get(rule, 6)


def run(start: datetime | None = None, fetched: fetch.FetchResult | None = None, out_dir: Path | None = None,
        state_dir: Path | None = None, workers: int = 4, refresh: bool = False, fetch_missing: bool = True,
        with_claude: bool = False, only: set[str] | None = None, detail: bool = True, board_limit: float | None = None,
        progress=None, history: Path | None = None, raw_dir: Path | None = None, full: bool = False) -> dict:
    """The one pipeline: fetch, prefilter, JD rules, score, dedupe. Zero Claude calls unless with_claude is set."""
    start = dates.run_start(start)
    today = dates.chicago_date(start)
    window = int(config.filters_config()["days"])
    scfg = config.scoring_config()
    res = fetched or fetch.fetch_all(start, only=only, raw_dir=raw_dir or config.ROOT / "logs" / "raw" / today.isoformat(), detail=detail,
                                     progress=progress, history=history, board_limit=board_limit, full=full)

    pairs, deduped = _dedupe([(_job_from_raw(rj, start), rj) for rj in res.jobs])
    review_keys = set()
    review_jobs: list[Job] = []
    for rj, _step, _why in res.review:
        j = _job_from_raw(rj, start)
        if any(j.id == p[0].id for p in pairs):
            continue
        j.reasons.append(Reason("title_not_on_list", "title not on list", rj.title))
        j.jd_source = "none"
        review_jobs.append(j)
        review_keys.add((rj.company, rj.title, rj.url))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool_:
        list(pool_.map(lambda p: phase_a(p[0], p[1], refresh, fetch_missing, with_claude), pairs))
    jobs = [p[0] for p in pairs]

    gate_summary: dict = {"enabled": with_claude}
    if with_claude:
        cache = careerops.load_cache(state_dir)
        gate_summary.update(run_gate(jobs, cache, False, workers))
        careerops.save_cache(cache, state_dir)
    min_score = float(scfg["min_score"])
    for j in jobs + review_jobs:
        if j.jd_source != "none" or j.jd_text:
            score.score_job(j, j.extraction)
        else:
            score.score_job(j, {})
        if not j.hidden and j.score is not None and j.score < min_score:
            j.reasons.append(Reason("score", f"Score below {min_score:g} ({j.score:g})", ""))
        _write_facts(j)

    shown = sorted((j for j in jobs if not j.hidden), key=_fit_key)
    hidden_jobs = [j for j in jobs if j.hidden] + review_jobs
    detailed_hidden = [_public(j) for j in hidden_jobs]

    dropped = []
    for b in res.boards:
        for company, title, location, url, step_key, reason in b.rows:
            if (company, title, url) in review_keys:
                continue
            step = _PRE_STEP.get(step_key, 4)
            dropped.append({"id": "", "company": company, "title": title, "location": location, "url": url, "posted": "",
                            "first_seen": today.isoformat(), "step": step, "step_name": funnel.STEP_NAMES[step],
                            "rule": step_key, "reason": reason, "evidence": ""})

    reason_counts: dict[str, int] = {}
    by_step: dict[int, int] = {}
    for d in dropped:
        reason_counts[d["rule"]] = reason_counts.get(d["rule"], 0) + 1
        by_step[d["step"]] = by_step.get(d["step"], 0) + 1
    for j in detailed_hidden:
        by_step[j["step"]] = by_step.get(j["step"], 0) + 1
        for r in j["reasons"]:
            reason_counts[r["rule"]] = reason_counts.get(r["rule"], 0) + 1

    raw_total = sum(b.raw for b in res.boards)
    date_dropped = sum(b.dropped.get("date", 0) for b in res.boards)
    loc_dropped = sum(b.dropped.get("location", 0) for b in res.boards)
    title_dropped = sum(n for b in res.boards for k, n in b.dropped.items() if _PRE_STEP.get(k) == 4)
    list_dropped = sum(b.dropped.get("title not on list", 0) for b in res.boards)
    s2, s3 = raw_total - date_dropped, raw_total - date_dropped - loc_dropped
    s4 = s3 - title_dropped
    s5 = s4 - list_dropped
    n_jd = len(jobs)
    n_ok6 = sum(1 for j in jobs if not any(_step_of_rule(r.rule) == 6 for r in j.reasons))
    steps = [{"step": 1, "name": "pulled", "in": raw_total, "out": raw_total},
             {"step": 2, "name": funnel.STEP_NAMES[2], "in": raw_total, "out": s2},
             {"step": 3, "name": funnel.STEP_NAMES[3], "in": s2, "out": s3},
             {"step": 4, "name": funnel.STEP_NAMES[4], "in": s3, "out": s4},
             {"step": 5, "name": funnel.STEP_NAMES[5], "in": s4, "out": s5},
             {"step": 6, "name": funnel.STEP_NAMES[6], "in": n_jd, "out": n_ok6},
             {"step": 7, "name": funnel.STEP_NAMES[7], "in": n_ok6, "out": len(shown)}]
    counts = _funnel_counts(res, jobs)

    rng = random.Random(today.isoformat())
    samples = {str(step): [{"company": d["company"], "title": d["title"], "reason": d["reason"]}
                           for d in rng.sample([x for x in dropped if x["step"] == step],
                                               min(25, sum(1 for x in dropped if x["step"] == step)))]
               for step in (4, 5)}
    fetch_info = {"boards": len(res.boards), "failed": [{"name": b.name, "ats": b.ats, "status": b.status, "note": b.note} for b in res.failed],
                  "partial": [{"name": b.name, "ats": b.ats, "note": b.note} for b in res.partial], "elapsed_seconds": res.elapsed,
                  "raw_jobs": raw_total, "deduped": deduped,
                  "location_step": {k: sum(b.metros.get(k, 0) for b in res.boards) for k in sorted({m for b in res.boards for m in b.metros})}}
    result = {
        "run_date": today.isoformat(), "run_start": start.isoformat(), "window_days": window, "history_rows": raw_total,
        "selection": {"pool": len(res.jobs)},
        "jd_fetch": {s: sum(1 for j in jobs if j.jd_source == s) for s in ("api", "fetch-jd", "html", "cache", "none", "apple", "oracle-hcm")},
        "funnel": {"steps": steps, "companies": counts, "totals": funnel.totals(counts), "dropped_by_step": by_step,
                   "classifier": {}, "scan": {"scanned": len(res.boards), "found": raw_total, "filtered": date_dropped,
                                              "errors": [{"company": b.name, "error": b.note or b.status} for b in res.failed],
                                              "unverified_zero": []},
                   "samples": samples},
        "gate": gate_summary, "fetch": fetch_info,
        "shown": [_public(j) for j in shown],
        "hidden": detailed_hidden,
        "dropped": dropped,
        "hidden_by_rule": reason_counts,
    }
    out = out_dir or config.output_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"run_{today.isoformat()}.json"
    slim = {k: v for k, v in result.items() if k != "dropped"}
    path.write_text(json.dumps(slim, indent=2, ensure_ascii=False), encoding="utf-8")
    write_dropped_csv(out / f"Dropped_{today.isoformat()}.csv", dropped, detailed_hidden)
    result["_path"] = str(path)
    return result


def format_funnel(result: dict) -> list[str]:
    f = result["funnel"]
    lines = ["", "FUNNEL (count after each step)"]
    prev = None
    for s in f["steps"]:
        if s["step"] == 1:
            lines.append(f"  1 pulled              {s['out']:>8,}   (scan history rows: {s['in']:,})")
        else:
            dropped = s["in"] - s["out"]
            lines.append(f"  {s['step']} {s['name']:<18} {s['out']:>8,}   dropped {dropped:,}")
    c = f.get("classifier") or {}
    if c.get("titles"):
        lines.append(f"    step 5 Claude: {c['titles']} titles, {c['calls']} calls ({c.get('cached', 0)} cached, "
                     f"{c.get('failed_calls', 0)} failed, {c.get('uncapped_titles', 0)} over the cap and kept)")
    return lines


def format_samples(result: dict) -> list[str]:
    lines: list[str] = []
    for step, label in (("4", "STEP 4 (negative titles)"), ("5", "STEP 5 (finance relevance)")):
        samples = result["funnel"]["samples"].get(step, [])
        lines += ["", f"25 RANDOM TITLES DROPPED AT {label}: check for anything wrongly filtered"]
        for s in samples:
            lines.append(f"  [{s['company'][:14]:<14}] {s['title'][:70]:<70} -> {s['reason']}")
        if not samples:
            lines.append("  (none)")
    return lines


def _short(items: list[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" +{len(items) - n} more" if len(items) > n else "")


def _metro_line(f: dict) -> str:
    m = f["location_step"]
    bay = m.get("bay_area", 0)
    other = sum(v for k, v in m.items() if k not in ("bay_area", "outside", "unknown"))
    return f"Reached the location step: Bay Area {bay:,}, other target metros {other:,}, outside {m.get('outside', 0):,}, unknown {m.get('unknown', 0):,}"


def format_console(result: dict, out: dict, latest: Path, top: int = 10, seconds: float = 0.0, log: Path | None = None) -> str:
    """The whole console summary: 20 lines at most. Everything else goes to logs\board_<date>.log."""
    f = result["fetch"]
    steps = {s["step"]: s["out"] for s in result["funnel"]["steps"]}
    shown = result["shown"]
    unconfirmed = sum(1 for j in shown if j.get("date_flag"))
    ids = out.get("ids", {})
    lines = [f"Job board {result['run_date']}  window {result['window_days']}d  fetch {f['elapsed_seconds']:.0f}s  total {seconds:.0f}s",
             f"Boards {f['boards']}: failed {len(f['failed'])}" + (f" ({_short([b['name'] for b in f['failed']])})" if f["failed"] else "")
             + f", partial {len(f['partial'])}" + (f" ({_short([b['name'] for b in f['partial']])})" if f["partial"] else ""),
             _metro_line(f) if f.get("location_step") else "Location step: n/a",
             f"Pulled {steps[1]:,} > date {steps[2]:,} > location {steps[3]:,} > title {steps[4]:,} > on list {steps[5]:,} > JD rules {steps[6]:,} > scored {steps[7]:,}",
             f"Kept {len(shown) - unconfirmed} + {unconfirmed} date unconfirmed, hidden {len(result['hidden'])}, "
             f"dropped before JD {len(result['dropped']):,}, deduped {f['deduped']}",
             f"Top {min(top, len(shown))} (ID, score, company | title | training):"]
    for j in shown[:top]:
        train = "; ".join(j.get("training") or []) or "-"
        lines.append(f"  [{ids.get(j['id'], '?')}] {j['score']:>5} {j['company'][:18]:<18} | {j['title'][:44]:<44} | {train[:34]}")
    if not shown:
        lines.append("  (no role passed every filter)")
    lines.append(f"Board: {latest}")
    if out.get("master"):
        lines.append(f"Excel: {out['master']}")
    if log:
        lines.append(f"Detail: {log}")
    for note in out.get("notes", [])[:1]:
        lines.append(f"NOTE: {note[:150]}")
    return "\n".join(lines[:20])


def format_detail(result: dict, out: dict, seconds: float) -> str:
    """The long version of a run, for logs\board_<date>.log."""
    f = result["fetch"]
    lines = [f"Run {result['run_date']} (started {result['run_start']})  window {result['window_days']}d  total {seconds:.0f}s", ""]
    lines += format_funnel(result)
    lines += ["", f"Fetch: {f['boards']} boards, {f['raw_jobs']:,} jobs listed, {f['elapsed_seconds']:.0f}s, deduped {f['deduped']}",
              f"JD source: {result['jd_fetch']}", f"Hidden by rule: {result['hidden_by_rule']}"]
    for label, key in (("FAILED", "failed"), ("PARTIAL", "partial")):
        for b in f[key]:
            lines.append(f"  {label} {b['ats']}:{b['name']} {b.get('status', '')} {b.get('note', '')}")
    lines += ["", f"SHOWN ({len(result['shown'])})"]
    ids = out.get("ids", {})
    for j in result["shown"]:
        lines.append(f"  [{ids.get(j['id'], '?')}] {j['score']:>5} {j['company'][:20]:<20} {j['title'][:50]:<50} {j['location'][:24]:<24} "
                     f"{'; '.join(j['flags'][:3])}")
    lines += format_samples(result)
    return "\n".join(lines) + "\n"
