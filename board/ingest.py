"""Read career-ops scan-history.tsv and pick the rows the board works on."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .models import HistoryRow

# Order declared by career-ops lib/scan-history-columns.mjs. Append-only: never reorder.
COLUMNS = [
    "url", "first_seen", "portal", "title", "company", "status", "location", "fingerprint",
    "posted_at", "trust_score", "trust_flags", "normalized_company", "requisition_id", "language",
    "listing_key",
]

# career-ops stores a cell that would run as a spreadsheet formula with one extra leading apostrophe.
_FORMULA_ESCAPE = re.compile(r"^'('*[=+\-@].*)$", re.S)


def unescape_cell(cell: str) -> str:
    m = _FORMULA_ESCAPE.match(cell)
    return m.group(1) if m else cell


def parse_line(line: str) -> HistoryRow:
    cells = line.rstrip("\r\n").split("\t")
    cells = [unescape_cell(c) for c in cells]
    cells += [""] * (len(COLUMNS) - len(cells))
    return HistoryRow(**dict(zip(COLUMNS, cells[: len(COLUMNS)])))


def read_history(path: Path) -> list[HistoryRow]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run a real scan first: node scan.mjs (from career-ops, with CAREER_OPS_ROOT set)."
        )
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines and lines[0].startswith("url\t"):   # header only when it IS the header
        lines = lines[1:]
    return [parse_line(ln) for ln in lines if ln.strip()]


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def row_key(row: HistoryRow) -> str:
    return row.listing_key or normalize_url(row.url)


def job_id(row: HistoryRow) -> str:
    """Stable 8-hex id: survives daily regeneration so Applied status can attach to it."""
    return hashlib.sha1(row_key(row).encode("utf-8")).hexdigest()[:8]


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@dataclass
class Selection:
    candidates: list[tuple[HistoryRow, date, bool]] = field(default_factory=list)  # (row, date_used, approx)
    prefiltered: list[tuple[HistoryRow, str, str]] = field(default_factory=list)   # (row, rule, reason)
    counts: dict[str, int] = field(default_factory=dict)


# career-ops statuses that mean "a title-matched role was dropped for this reason".
_CAREER_OPS_HIDES = {
    "skipped_location": ("metro", "Outside the 8 metros (career-ops location_filter)"),
    "skipped_age": ("age", "Posted before the age window (career-ops max_posting_age_days)"),
    "skipped_experience": ("years_required", "Stated years above band (career-ops experience_filter)"),
    "skipped_expired": ("expired", "Posting no longer live (career-ops liveness check)"),
}


def select_rows(rows: list[HistoryRow], today: date, window_days: int) -> Selection:
    sel = Selection()
    counts: dict[str, int] = {}

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    # One row per posting; an `added` row beats any other status, then the newest first_seen.
    best: dict[str, HistoryRow] = {}
    for r in rows:
        k = row_key(r)
        cur = best.get(k)
        if cur is None:
            best[k] = r
            continue
        rank = lambda x: (x.status == "added", x.first_seen)  # noqa: E731
        if rank(r) > rank(cur):
            best[k] = r
    cutoff = today - timedelta(days=window_days)

    for r in best.values():
        if r.status == "added":
            posted = parse_date(r.posted_at)
            seen = parse_date(r.first_seen)
            used, approx = (posted, False) if posted else (seen, True)
            if used is None:
                bump("added_no_date")
                continue
            if used < cutoff:
                bump("added_too_old")
                if seen and seen >= cutoff:
                    sel.prefiltered.append((r, "age", f"Posted {(today - used).days} days ago (window is {window_days})"))
                continue
            sel.candidates.append((r, used, approx))
            bump("added_in_window")
        elif r.status in _CAREER_OPS_HIDES:
            seen = parse_date(r.first_seen)
            if seen and seen >= cutoff:
                rule, text = _CAREER_OPS_HIDES[r.status]
                sel.prefiltered.append((r, rule, text))
                bump(r.status)
        elif r.status == "skipped_title":
            bump("skipped_title (off-target titles, not listed)")
    sel.counts = counts
    return sel
