"""python -m board run: history -> window -> JD fetch -> extract -> hard filters -> score -> results JSON."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import config, ingest, jd, score
from .extract import extract
from .filters import apply_filters
from .models import HistoryRow, Job, Reason


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
        "score": job.score, "score_parts": job.score_parts, "extraction": job.extraction,
    }


def process_job(job: Job, refresh: bool, fetch: bool) -> None:
    jd.load_or_fetch(job, refresh=refresh, fetch=fetch)
    ext = extract(job.jd_text, job.location)
    job.extraction = ext.to_dict()
    job.company_priority = score.company_priority(job.portal, job.company)
    apply_filters(job, ext)
    # Hidden jobs are scored too, so a role you override from the Hidden sheet already has a score.
    score.score_job(job, job.extraction)
    if job.jd_dir and job.jd_source != "none":
        job.jd_dir.mkdir(parents=True, exist_ok=True)
        (job.jd_dir / "facts.json").write_text(json.dumps(_facts_json(job), indent=2, ensure_ascii=False), encoding="utf-8")


def _public(job: Job) -> dict:
    ext = job.extraction or {}
    yr = ext.get("years_required") or {}
    deg = ext.get("degree_required") or {}
    return {
        "id": job.id, "first_seen": job.row.first_seen, "years_bachelors": yr.get("years_bachelors"),
        "score": job.score, "company": job.company, "title": job.title, "location": job.location,
        "url": job.url, "posted": job.date_used.isoformat() if job.date_used else "", "age_days": job.age_days,
        "date_approx": job.date_approx, "metro": job.metro, "work_mode": job.work_mode,
        "company_priority": job.company_priority, "jd_source": job.jd_source,
        "jd_dir": str(job.jd_dir) if job.jd_dir else "",
        "flags": job.flags, "reasons": [r.__dict__ for r in job.reasons],
        "years_required": yr.get("min_years"), "years_evidence": yr.get("evidence", ""),
        "degree_required": deg.get("level"), "degree_evidence": deg.get("evidence", ""),
        "certs": ext.get("certs", []), "score_parts": job.score_parts,
    }


def run(today: date | None = None, limit: int | None = None, refresh: bool = False, fetch: bool = True,
        history: Path | None = None, out_dir: Path | None = None, workers: int = 4) -> dict:
    today = today or date.today()
    fcfg = config.filters_config()
    window = fcfg["posting_max_age_days"]

    rows = ingest.read_history(history or config.history_path())
    sel = ingest.select_rows(rows, today, window)

    cands = sel.candidates[:limit] if limit else sel.candidates
    jobs = [_job_from_row(r, used, approx, today) for r, used, approx in cands]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(lambda j: process_job(j, refresh, fetch), jobs))

    pre_hidden: list[Job] = []
    for r, rule, text in sel.prefiltered:
        used = ingest.parse_date(r.posted_at) or ingest.parse_date(r.first_seen)
        j = _job_from_row(r, used, not ingest.parse_date(r.posted_at), today)
        j.reasons.append(Reason(rule, text, ""))
        pre_hidden.append(j)

    shown = sorted((j for j in jobs if not j.hidden), key=lambda j: -(j.score or 0))
    hidden = [j for j in jobs if j.hidden] + pre_hidden

    reason_counts: dict[str, int] = {}
    for j in hidden:
        for rs in j.reasons:
            reason_counts[rs.rule] = reason_counts.get(rs.rule, 0) + 1

    result = {
        "run_date": today.isoformat(),
        "window_days": window,
        "history_rows": len(rows),
        "selection": sel.counts,
        "jd_fetch": {s: sum(1 for j in jobs if j.jd_source == s) for s in ("fetch-jd", "html", "cache", "none")},
        "shown": [_public(j) for j in shown],
        "hidden": [_public(j) for j in hidden],
        "hidden_by_rule": reason_counts,
    }
    out = out_dir or config.output_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"run_{today.isoformat()}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["_path"] = str(path)
    return result


def format_summary(result: dict, top: int = 15) -> str:
    lines = [
        f"Run {result['run_date']}  window {result['window_days']}d  history rows {result['history_rows']}",
        f"Selection: {result['selection']}",
        f"JD source: {result['jd_fetch']}",
        f"Shown {len(result['shown'])}   Hidden {len(result['hidden'])}   by rule: {result['hidden_by_rule']}",
        "",
        f"{'score':>5}  {'company':<22} {'title':<52} {'location':<26} flags",
    ]
    for r in result["shown"][:top]:
        flags = "; ".join(r["flags"][:3])
        lines.append(f"{r['score']:>5}  {r['company'][:22]:<22} {r['title'][:52]:<52} {r['location'][:26]:<26} {flags}")
    lines.append("")
    lines.append(f"Results: {result.get('_path', '')}")
    return "\n".join(lines)
