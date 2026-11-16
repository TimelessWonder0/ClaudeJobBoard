"""Jobs for providers only the career-ops scan can read (Apple, Google, Pinpoint, own sites).

Reads the scan history that `node scan.mjs --since N --json` appends to; it does not run the scan. Dates: `posted_at`
when the row has one (confirmed), otherwise the first-seen date, which is never confirmed, so the role lands in
"Date unconfirmed" with the reason "first seen <date>". A row with no posted_at whose first-seen date is already older
than the window is skipped: it has been on the board that long.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .. import config, dates, ingest
from .base import RawJob


def load(companies: set[str], start: datetime, days: int, history: Path | None = None) -> tuple[int, list[RawJob], int]:
    """Returns (rows seen for these companies, jobs, rows skipped as first seen before the window)."""
    path = history or config.history_path()
    try:
        rows = ingest.read_history(path)
    except OSError:
        return 0, [], 0
    want = {c.lower() for c in companies}
    best: dict[str, object] = {}
    for r in rows:
        if r.status != "added":
            continue
        if (r.company or "").lower() not in want and (r.portal or "").lower() not in want:
            continue
        best[ingest.row_key(r)] = r              # later rows win
    cutoff = dates.chicago_date(start) - timedelta(days=days)
    jobs, stale = [], 0
    for r in best.values():
        if r.posted_at:
            posted = dates.from_iso(r.posted_at, "posted_at")
        else:
            posted = dates.first_seen(r.first_seen)
            if posted.when is not None and dates.chicago_date(posted.when) < cutoff:
                stale += 1
                continue
        jobs.append(RawJob(r.company or r.portal, "scanmjs", ingest.job_id(r), r.title, r.location, r.url, posted,
                           detail={"history_key": ingest.row_key(r)}))
    return len(best), jobs, stale
