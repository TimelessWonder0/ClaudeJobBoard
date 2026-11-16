"""Fetch every board, drop what the list data already rules out, then fetch details for the survivors only.

    fetch_all(start) -> FetchResult

Boards run in a small thread pool (8) with a per-host cap in `base.http_json`. Nothing is printed here; the caller prints
a short summary and the details go to the log and the files written by `save_result`.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config, dates
from . import scanmjs
from .ats import DETAIL, LIST
from .base import BoardTimeout, NoBoard, RawJob, RawStore, check_deadline, set_deadline, was_partial
from .boards import load_boards
from .prefilter import Prefilter

WORKERS = 8


@dataclass
class BoardLog:
    name: str
    ats: str
    status: str = "OK"                       # OK | PARTIAL | NOBOARD | ERROR | UNSUPPORTED
    raw: int = 0                             # jobs the board listed
    kept: int = 0                            # survivors of date, location, title and allow-list
    dropped: dict = field(default_factory=dict)    # step -> count
    unconfirmed: int = 0                     # kept with no confirmed date
    detail_failed: int = 0
    seconds: float = 0.0
    note: str = ""
    metros: dict = field(default_factory=dict)     # jobs that reached the location step, by metro key | outside | unknown
    rows: list = field(default_factory=list, repr=False)   # (company, title, location, url, step, reason) for every non-date drop


@dataclass
class FetchResult:
    start: datetime
    days: int
    jobs: list[RawJob] = field(default_factory=list)
    review: list[tuple[RawJob, str, str]] = field(default_factory=list)   # dropped by the allow-list but finance-ish
    boards: list[BoardLog] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def failed(self) -> list[BoardLog]:
        return [b for b in self.boards if b.status not in ("OK", "PARTIAL")]

    @property
    def partial(self) -> list[BoardLog]:
        return [b for b in self.boards if b.status == "PARTIAL"]


def fetch_board(b: dict, start: datetime, store: RawStore, pre: Callable, detail: bool = True,
                limit: float | None = None) -> tuple[BoardLog, list[RawJob], list]:
    log, kept, review = BoardLog(b["name"], b["ats"]), [], []
    t0 = time.perf_counter()
    set_deadline(limit)
    try:
        if b["ats"] == "unsupported":
            raise NoBoard(b.get("note", "unsupported"))
        log.raw, jobs = LIST[b["ats"]](b, start, store)
        drops: Counter = Counter()
        for job in jobs:
            why = pre(job)
            if why is None or why[0] != "date":
                bucket = pre.location_bucket(job)
                log.metros[bucket] = log.metros.get(bucket, 0) + 1
            if why is None:
                kept.append(job)
            else:
                drops[why[0]] += 1
                if why[0] != "date":
                    log.rows.append((job.company, job.title, job.location, job.url, why[0], why[1]))
                if why[0] == "title not on list" and pre.worth_review(job):
                    review.append((job, *why))
        log.dropped = dict(drops)
        fetch_detail = DETAIL.get(b["ats"])
        if detail and fetch_detail:
            for job in kept:
                try:
                    check_deadline()
                    fetch_detail(job, store)
                except BoardTimeout:
                    log.detail_failed += 1
                    job.flags.append("JD not fetched (board time limit)")
                except Exception:                    # noqa: BLE001 - a missing JD must not drop the role
                    log.detail_failed += 1
        log.kept = len(kept)
        log.unconfirmed = sum(1 for j in kept if not j.posted.confirmed)
    except NoBoard as e:
        log.status = "UNSUPPORTED" if b["ats"] == "unsupported" else "NOBOARD"
        log.note = str(e)
    except BoardTimeout:
        log.status = "PARTIAL"
    except Exception as e:                           # noqa: BLE001 - one bad board never stops the run
        log.status, log.note = "ERROR", f"{type(e).__name__}: {e}"[:200]
    if was_partial():
        log.status, log.note = "PARTIAL", f"cut off at the {limit:g}s board time limit; kept what it had listed"
    log.seconds = round(time.perf_counter() - t0, 1)
    return log, kept, review


def fetch_all(start: datetime | None = None, only: set[str] | None = None, raw_dir: Path | None = None,
              detail: bool = True, progress: Callable[[BoardLog], None] | None = None,
              history: Path | None = None, board_limit: float | None = None, workers: int | None = None,
              full: bool = False) -> FetchResult:
    start = dates.run_start(start)
    fcfg = config.filters_config()
    days = int(fcfg["days"])
    limit = board_limit if board_limit is not None else float(fcfg.get("fetch", {}).get("board_time_limit_seconds", 90))
    workers = workers or int(fcfg.get("fetch", {}).get("workers", WORKERS))
    pre = Prefilter(start, days)
    store = RawStore(raw_dir)
    res = FetchResult(start, days)
    t0 = time.perf_counter()
    boards = [{**b, "full": full} for b in load_boards(only)]
    python_boards = [b for b in boards if b["ats"] != "scanmjs"]
    limited: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_board, b, start, store, pre, detail, limit): b for b in python_boards}
        for f in as_completed(futs):
            log, kept, review = f.result()
            if log.status == "ERROR" and log.note.startswith("RateLimited"):
                limited.append(futs[f])                 # second pass below, after a cool-down; not reported as failed yet
                continue
            res.boards.append(log)
            res.jobs += kept
            res.review += review
            if progress:
                progress(log)
    if limited:
        # Rate-limited boards get one more pass after a cool-down, two at a time, instead of failing the whole run's coverage.
        time.sleep(float(fcfg.get("fetch", {}).get("rate_limit_cooldown_seconds", 60)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            for log, kept, review in pool.map(lambda b: fetch_board(b, start, store, pre, detail, limit), limited):
                if log.status == "ERROR" and log.note.startswith("RateLimited"):
                    log.note += " (also after a cool-down pass)"
                res.boards.append(log)
                res.jobs += kept
                res.review += review
                if progress:
                    progress(log)
    names = {b["name"] for b in boards if b["ats"] == "scanmjs"}
    if names:
        log = BoardLog("scan.mjs providers (" + ", ".join(sorted(names)) + ")", "scanmjs")
        log.raw, jobs, stale = scanmjs.load(names, start, days, history)
        drops: Counter = Counter({"date": stale} if stale else {})
        for job in jobs:
            why = pre(job)
            if why is None or why[0] != "date":
                bucket = pre.location_bucket(job)
                log.metros[bucket] = log.metros.get(bucket, 0) + 1
            if why is None:
                res.jobs.append(job)
                log.kept += 1
                log.unconfirmed += 0 if job.posted.confirmed else 1
            else:
                drops[why[0]] += 1
                if why[0] != "date":
                    log.rows.append((job.company, job.title, job.location, job.url, why[0], why[1]))
                if why[0] == "title not on list" and pre.worth_review(job):
                    res.review.append((job, *why))
        log.dropped = dict(drops)
        if log.raw == 0:
            log.status, log.note = "NOBOARD", "no rows in scan history (run scan.mjs first)"
        res.boards.append(log)
    res.boards.sort(key=lambda x: (x.ats, x.name))
    res.elapsed = round(time.perf_counter() - t0, 1)
    return res


def _job_dict(j: RawJob) -> dict:
    d = asdict(j)
    p = j.posted
    d["posted"] = {"when": p.when.isoformat() if p.when else None, "precision": p.precision, "source": p.source,
                   "flag": p.flag, "label": p.label()}
    return d


def save_result(res: FetchResult, state_dir: Path | None = None) -> Path:
    """Write state/fetched.json (survivors and boards) atomically. Raw list responses are written during the fetch."""
    path = (state_dir or config.state_dir()) / "fetched.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"run_start": res.start.isoformat(), "days": res.days, "elapsed_seconds": res.elapsed,
            "boards": [{k: v for k, v in asdict(b).items() if k != "rows"} for b in res.boards],
            "jobs": [_job_dict(j) for j in res.jobs],
            "title_not_on_list": [{**_job_dict(j), "step": s, "reason": r} for j, s, r in res.review]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path
