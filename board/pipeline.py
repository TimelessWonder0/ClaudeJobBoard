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
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import careerops, classify, config, funnel, ingest, jd, score
from .extract import extract
from .filters import apply_filters
from .models import HistoryRow, Job, Reason

_RULE_STEP = {"closed": 7, "careerops_low_fit": 7}


class Gate:
    """The external calls the pipeline makes. Tests replace these with fakes."""
    years = staticmethod(careerops.years_crosscheck)
    keywords = staticmethod(careerops.keyword_overlap)
    liveness = staticmethod(careerops.check_liveness)
    evaluate = staticmethod(careerops.evaluate)
    classify = staticmethod(classify.classify_titles)


def _job_from_row(row: HistoryRow, used: date | None, approx: bool, today: date) -> Job:
    return Job(
        id=ingest.job_id(row), row=row, company=row.company, title=row.title, location=row.location,
        url=row.url, portal=row.portal, date_used=used, date_approx=approx,
        age_days=(today - used).days if used else None,
    )


def _facts_json(job: Job) -> dict:
    return {
        "id": job.id, "url": job.url, "company": job.company, "title": job.title, "location": job.location,
        "posted": job.date_used.isoformat() if job.date_used else "", "date_approx": job.date_approx,
        "jd_source": job.jd_source, "metro": job.metro, "work_mode": job.work_mode,
        "hidden": job.hidden, "reasons": [r.__dict__ for r in job.reasons], "flags": job.flags,
        "score": job.score, "score_parts": job.score_parts, "careerops": job.careerops, "extraction": job.extraction,
    }


def phase_a(job: Job, refresh: bool, fetch: bool, gate: bool) -> None:
    """Step 6 for one posting: fetch the JD, extract, and apply the JD-based rules."""
    jd.load_or_fetch(job, refresh=refresh, fetch=fetch)
    ext = extract(job.jd_text, job.location)
    job.extraction = ext.to_dict()
    job.company_priority = score.company_priority(job.portal, job.company)
    co_years, co_confident = None, False
    if gate and config.load_yaml("careerops.yaml").get("years_crosscheck") and job.jd_text:
        got = Gate.years(job.jd_text)
        if got:
            job.careerops["years"] = got
            if got.get("status") == "stated" and got.get("min") is not None:
                co_years, co_confident = float(got["min"]), bool(got.get("confident"))
    apply_filters(job, ext, careerops_years=co_years, careerops_years_confident=co_confident,
                  skip_title=True, skip_location=True)


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


def run(today: date | None = None, limit: int | None = None, refresh: bool = False, fetch: bool = True,
        history: Path | None = None, out_dir: Path | None = None, workers: int = 4, gate: bool = True,
        refresh_careerops: bool = False, state_dir: Path | None = None, scan_receipt: dict | None = None) -> dict:
    today = today or date.today()
    fcfg = funnel.funnel_config()
    window = int(fcfg["window_days"])
    gate = gate and bool(config.load_yaml("careerops.yaml").get("enabled", True))

    rows = ingest.read_history(history or config.history_path())
    pool = funnel.build_pool(rows, today, window)
    if limit:
        pool = pool[:limit]
    counts = funnel.new_counts()
    for p in pool:
        funnel.bump(counts, p.company, "pulled")
    steps: list[dict] = [{"step": 1, "name": "pulled", "in": len(rows), "out": len(pool)}]
    drops: list[funnel.Drop] = []

    def run_step(step: int, items: list[funnel.Posting], fn, stage: str) -> list[funnel.Posting]:
        out: list[funnel.Posting] = []
        for p in items:
            d = fn(p)
            if d:
                drops.append(d)
            else:
                out.append(p)
                funnel.bump(counts, p.company, stage)
        steps.append({"step": step, "name": funnel.STEP_NAMES[step], "in": len(items), "out": len(out)})
        return out

    s2 = run_step(2, pool, lambda p: funnel.step_date(p, today, window), "after_date")
    fcfg_filters = config.filters_config()
    s3 = run_step(3, s2, lambda p: funnel.step_location(p, fcfg_filters), "after_location")
    neg = funnel.NegativeTitles()
    s4 = run_step(4, s3, lambda p: funnel.step_title(p, neg), "after_titles")

    # step 5: accounting track and keyword relevance are free; Claude judges the rest
    rel = funnel.FinanceRelevance(fcfg)
    verdicts = {id(p): rel.judge(p) for p in s4}
    unknown = [p for p in s4 if verdicts[id(p)] == "unknown"]
    class_stats: dict = {"titles": 0, "calls": 0}
    claude_says: dict[str, str] = {}
    if unknown and gate and fcfg["classifier"].get("enabled", True):
        cache = classify.load_cache(state_dir)
        items = [(classify.norm_key(p.title, rel.department_of(p)), f"{p.title} - {p.company}") for p in unknown]
        claude_says, class_stats = Gate.classify(items, cache, fcfg["classifier"])
        classify.save_cache(cache, state_dir)

    def finance(p: funnel.Posting):
        v = verdicts[id(p)]
        if v == "accounting":
            return funnel.Drop(p, 5, "accounting", "accounting track", p.title)
        if v == "keep":
            return None
        verdict = claude_says.get(classify.norm_key(p.title, rel.department_of(p)))
        if verdict == "drop":
            return funnel.Drop(p, 5, "not_finance", "Not finance-adjacent (Claude judged the title)", p.title)
        p.flags.append("finance relevance unverified" if verdict is None else "finance-adjacent (Claude)")
        return None

    s5 = run_step(5, s4, finance, "after_finance")

    # step 6: JD-based rules, only for what is still standing
    jobs = [_job_from_row(p.row, p.used, p.approx, today) for p in s5]
    for j, p in zip(jobs, s5):
        j.flags = list(p.flags)
        j.portal = p.row.portal
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool_:
        list(pool_.map(lambda j: phase_a(j, refresh, fetch, gate), jobs))
    company_of = {j.id: funnel.canonical_company(j.portal, j.company) for j in jobs}
    for j in jobs:
        if not j.hidden:
            funnel.bump(counts, company_of[j.id], "after_years")
    steps.append({"step": 6, "name": funnel.STEP_NAMES[6], "in": len(jobs), "out": sum(1 for j in jobs if not j.hidden)})

    # step 7
    gate_summary: dict = {"enabled": gate}
    if gate:
        cache = careerops.load_cache(state_dir)
        gate_summary.update(run_gate(jobs, cache, refresh_careerops, workers))
        careerops.save_cache(cache, state_dir)
    for j in jobs:
        score.score_job(j, j.extraction)            # final Fit Score (hidden jobs too, so an override already has one)
        _write_facts(j)
        if not j.hidden:
            funnel.bump(counts, company_of[j.id], "after_fit")
    steps.append({"step": 7, "name": funnel.STEP_NAMES[7], "in": steps[-1]["out"], "out": sum(1 for j in jobs if not j.hidden)})

    shown = sorted((j for j in jobs if not j.hidden), key=_fit_key)
    detailed_hidden = [_public(j) for j in jobs if j.hidden]
    dropped = [_dropped_record(d) for d in drops]

    reason_counts: dict[str, int] = {}
    by_step: dict[int, int] = {}
    for d in drops:
        reason_counts[d.rule] = reason_counts.get(d.rule, 0) + 1
        by_step[d.step] = by_step.get(d.step, 0) + 1
    for j in detailed_hidden:
        by_step[j["step"]] = by_step.get(j["step"], 0) + 1
        for r in j["reasons"]:
            reason_counts[r["rule"]] = reason_counts.get(r["rule"], 0) + 1

    seed = today.isoformat()
    n = int(fcfg.get("samples_per_step", 25))
    result = {
        "run_date": today.isoformat(), "window_days": window, "history_rows": len(rows),
        "selection": {"pool": len(pool)},
        "jd_fetch": {s: sum(1 for j in jobs if j.jd_source == s) for s in ("fetch-jd", "html", "cache", "none", "apple", "oracle-hcm")},
        "funnel": {"steps": steps, "companies": counts, "totals": funnel.totals(counts), "dropped_by_step": by_step,
                   "classifier": class_stats, "scan": _scan_summary(scan_receipt),
                   "samples": {"4": funnel.sample_drops(drops, 4, n, seed + "-4"), "5": funnel.sample_drops(drops, 5, n, seed + "-5")}},
        "gate": gate_summary,
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


def format_summary(result: dict, top: int = 15, samples: bool = True) -> str:
    g = result.get("gate", {})
    lines = [f"Run {result['run_date']}  window {result['window_days']}d  history rows {result['history_rows']}"]
    lines += format_funnel(result)
    lines.append(f"JD source: {result['jd_fetch']}")
    if g.get("enabled"):
        lines.append(f"career-ops gate: checked {g['checked']}, closed {g['closed']}, evaluated {g['evaluated']}, "
                     f"cached {g['cached']}, low fit {g['low_fit']}, not evaluated yet {g.get('not_evaluated', 0)}, "
                     f"evaluation errors {len(g['errors'])}")
        for e in g["errors"]:
            lines.append(f"  ! {e['company']} | {e['title'][:40]}: {e['error']}")
    lines += [
        f"Shown {len(result['shown'])}   dropped {len(result['dropped']) + len(result['hidden']):,}   by rule: {result['hidden_by_rule']}",
        "",
        f"{'co-ops':>6} {'fit':>5}  {'company':<22} {'title':<46} {'location':<24} flags",
    ]
    for r in result["shown"][:top]:
        flags = "; ".join(r["flags"][:3])
        cs = r.get("careerops_score")
        lines.append(f"{'-' if cs is None else format(cs, 'g'):>6} {r['score']:>5}  {r['company'][:22]:<22} {r['title'][:46]:<46} "
                     f"{r['location'][:24]:<24} {flags}")
    lines.append("")
    lines.append(f"Results: {result.get('_path', '')}")
    if samples:
        lines += format_samples(result)
    return "\n".join(lines)
