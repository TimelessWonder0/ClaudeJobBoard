"""Which roles Claude has already given a verdict: state/reviews.json.

`python -m board.write_review` records every verdict it writes. Review workbooks written before this ledger existed
(output/reviews/Claude_Review_<date>.xlsx) are read once and folded in, so their roles count as reviewed too. The run
reads this ledger to print "Previously reviewed" in jobs_latest.md, so /jobs never has to open an .xlsx.

    {"reviews": {"920": {"date": "2026-11-19", "verdict": "Skip"}, ...}, "sources": ["Claude_Review_2026-11-19.xlsx"]}

The first review of a role is kept; a later one does not overwrite it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config

# Passed Over reasons that are not a verdict (the role was never judged in that review).
_NOT_A_VERDICT = re.compile(r"^(?:below cap|no verdict given|reviewed \d|status )", re.I)


def _path(state_dir: Path | None) -> Path:
    return (state_dir or config.state_dir()) / "reviews.json"


def _read(state_dir: Path | None) -> dict:
    try:
        data = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"reviews": data.get("reviews", {}), "sources": data.get("sources", [])}


def _write(data: dict, state_dir: Path | None) -> None:
    path = _path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _from_workbook(path: Path) -> dict[int, str]:
    """ID -> verdict from one review workbook (Picks, Date Unconfirmed, Passed Over)."""
    from openpyxl import load_workbook

    out: dict[int, str] = {}
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for name in ("Picks", "Date Unconfirmed", "Passed Over"):
            if name not in wb.sheetnames:
                continue
            rows = wb[name].iter_rows(values_only=True)
            head = [str(c or "") for c in next(rows, [])]
            col = {h: i for i, h in enumerate(head)}
            for r in rows:
                try:
                    num = int(r[col["ID"]])
                except (KeyError, TypeError, ValueError):
                    continue
                if name == "Picks":
                    out.setdefault(num, str(r[col["Claude Tier"]] or "reviewed"))
                elif name == "Date Unconfirmed":
                    worth = str(r[col["Worth checking"]] or "").lower() == "yes"
                    out.setdefault(num, str(r[col["Claude Tier"]] or "reviewed") if worth else "Skip")
                elif not _NOT_A_VERDICT.match(str(r[col["Reason"]] or "")):
                    out.setdefault(num, "Skip")
    finally:
        wb.close()
    return out


def load(state_dir: Path | None = None, reviews_dir: Path | None = None) -> dict[int, dict]:
    """ID -> {"date", "verdict"} for every role already reviewed. Folds in review workbooks not seen before."""
    data = _read(state_dir)
    reviews_dir = reviews_dir or config.output_dir() / "reviews"
    changed = False
    for wb_path in sorted(reviews_dir.glob("Claude_Review_*.xlsx")) if reviews_dir.exists() else []:
        if wb_path.name in data["sources"] or wb_path.name.startswith("~$"):
            continue
        m = re.match(r"Claude_Review_(\d{4}-\d{2}-\d{2})", wb_path.name)
        try:
            found = _from_workbook(wb_path)
        except Exception:                             # noqa: BLE001 - an unreadable workbook is skipped, never fatal
            continue
        for num, verdict in found.items():
            data["reviews"].setdefault(str(num), {"date": m.group(1) if m else "", "verdict": verdict})
        data["sources"].append(wb_path.name)
        changed = True
    if changed:
        _write(data, state_dir)
    return {int(k): v for k, v in data["reviews"].items()}


def record(verdicts: dict[int, dict], run_date: str, source: str = "", state_dir: Path | None = None) -> None:
    """Add verdicts ({id: {"verdict", ...}}) to the ledger under `run_date`; `source` is the workbook name, so it is not
    folded in a second time."""
    data = _read(state_dir)
    for num, v in verdicts.items():
        data["reviews"].setdefault(str(num), {"date": run_date, "verdict": v["verdict"]})
    if source and source not in data["sources"]:
        data["sources"].append(source)
    _write(data, state_dir)


def note(num: int, reviews: dict[int, dict], status: str = "") -> str:
    """'' when the role is new to /jobs, else why it is not: "reviewed 2026-11-19 (Strong)", "status Applied", or both."""
    parts = []
    r = reviews.get(num)
    if r:
        parts.append(f"reviewed {r['date']} ({r['verdict']})" if r.get("date") else f"reviewed ({r['verdict']})")
    if status and status != "New":
        parts.append(f"status {status}")
    return "; ".join(parts)
