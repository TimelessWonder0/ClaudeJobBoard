"""Write Claude's review of jobs_latest.md as output\\reviews\\Claude_Review_<date>.xlsx.

Claude never opens or reads an .xlsx. It reads jobs_latest.md, then hands this script a small JSON of verdicts:

    {"verdicts": [{"id": 12, "verdict": "Apply now", "why": "one line", "gap": "one line"}, ...]}

`verdict` is Apply now, Strong, Stretch or Skip. The script looks every ID up in jobs_latest.md (the same IDs as
JobBoard_Master.xlsx) and fills the rest of each row itself, so the workbook cannot disagree with the board.
It never touches JobBoard_Master.xlsx. If today's review file already exists or is open in Excel, it saves a copy with a
timestamp suffix instead of overwriting.

    python -m board.write_review review.json [--md output\\jobs_latest.md] [--out output\\reviews]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config, markdown

TIER_ORDER = {"Apply now": 0, "Strong": 1, "Stretch": 2}
BOARD_CAP = 30            # Claude gives verdicts for at most this many Board roles (best Python score first)
UNCONFIRMED_CAP = 10      # ... and for at most this many Date unconfirmed roles
VERDICTS = ("Apply now", "Strong", "Stretch", "Skip")

PICKS = ["Rank", "ID", "Score", "Claude Tier", "Title", "Company", "City", "On-site/Hybrid", "Posted", "Date Flag",
         "Years Required", "Pay", "Why it fits", "Gap to watch", "Link"]
UNCONFIRMED = ["ID", "Score", "Title", "Company", "City", "On-site/Hybrid", "Date Flag (why unconfirmed)", "Worth checking",
               "Claude Tier", "Why it fits", "Gap to watch", "Link"]
PASSED = ["ID", "Score", "Title", "Company", "City", "Reason"]
INFO = ["Item", "Value"]

WIDTHS = {"Rank": 6, "ID": 6, "Score": 7, "Claude Tier": 12, "Title": 44, "Company": 22, "City": 24, "On-site/Hybrid": 13, "Posted": 12,
          "Date Flag": 22, "Date Flag (why unconfirmed)": 30, "Worth checking": 14, "Years Required": 9, "Pay": 18,
          "Why it fits": 60, "Gap to watch": 50, "Link": 40, "Reason": 70, "Item": 28, "Value": 90}
WRAP = {"Why it fits", "Gap to watch", "Reason", "Value", "Title"}


class ReviewError(ValueError):
    pass


def load_verdicts(raw: dict) -> dict[int, dict]:
    """JSON -> {id: {verdict, why, gap}}. Rejects unknown verdicts and duplicate IDs; keeps the text to one line each."""
    items = raw.get("verdicts")
    if not isinstance(items, list):
        raise ReviewError('the review JSON needs a "verdicts" list')
    out: dict[int, dict] = {}
    for i, v in enumerate(items, 1):
        try:
            num = int(v["id"])
        except (KeyError, TypeError, ValueError):
            raise ReviewError(f"verdict {i} has no numeric id") from None
        verdict = str(v.get("verdict", "")).strip()
        match = next((x for x in VERDICTS if x.lower() == verdict.lower()), None)
        if not match:
            raise ReviewError(f"verdict {i} (id {num}): '{verdict}' is not one of {', '.join(VERDICTS)}")
        if num in out:
            raise ReviewError(f"id {num} appears twice")
        out[num] = {"verdict": match, "why": " ".join(str(v.get("why", "")).split()), "gap": " ".join(str(v.get("gap", "")).split())}
    return out


def build_rows(board: dict, verdicts: dict[int, dict]) -> dict:
    """Split the verdicts into the four sheets. Returns {"picks", "unconfirmed", "passed", "warnings"}.

    Only the top BOARD_CAP Board roles and the top UNCONFIRMED_CAP Date unconfirmed roles (by Python score) are for Claude to judge.
    Anything past a cap without a verdict goes to Passed Over as "below cap" with its Python score, with no warning.
    """
    known = {r["id"]: r for r in board["board"] + board["unconfirmed"]}
    warnings: list[str] = []
    for num in verdicts:
        if num not in known:
            warnings.append(f"id {num} is not a Board or Date unconfirmed role in jobs_latest.md; ignored")
    ranked = lambda roles: sorted(roles, key=lambda r: (-r["score"], r["id"]))      # noqa: E731
    in_cap = {r["id"] for r in ranked(board["board"])[:BOARD_CAP]} | {r["id"] for r in ranked(board["unconfirmed"])[:UNCONFIRMED_CAP]}
    picks, unconf, passed = [], [], []

    def below_cap(r: dict) -> dict:
        return {**r, "reason": f"below cap (Python score {r['score']:g})"}

    for r in board["board"]:
        v = verdicts.get(r["id"])
        if v is None and r["id"] not in in_cap:
            passed.append(below_cap(r))
        elif v is None:
            warnings.append(f"id {r['id']} ({r['title']} - {r['company']}) has no verdict; listed under Passed Over")
            passed.append({**r, "reason": "no verdict given"})
        elif v["verdict"] == "Skip":
            passed.append({**r, "reason": v["why"] or v["gap"] or "skipped"})
        else:
            picks.append({**r, "claude_tier": v["verdict"], "why": v["why"], "gap": v["gap"]})
    for r in board["unconfirmed"]:
        v = verdicts.get(r["id"])
        if v is None and r["id"] not in in_cap:
            passed.append(below_cap(r))
            continue
        if v is None:
            warnings.append(f"id {r['id']} ({r['title']} - {r['company']}) has no verdict; marked not worth checking")
            v = {"verdict": "Skip", "why": "", "gap": ""}
        unconf.append({**r, "claude_tier": "" if v["verdict"] == "Skip" else v["verdict"], "worth": v["verdict"] != "Skip",
                       "why": v["why"], "gap": v["gap"]})
    picks.sort(key=lambda r: (TIER_ORDER[r["claude_tier"]], -r["score"], r["id"]))
    unconf.sort(key=lambda r: (not r["worth"], -r["score"], r["id"]))
    passed.sort(key=lambda r: (r["reason"].startswith("below cap"), -r["score"], r["id"]))
    return {"picks": picks, "unconfirmed": unconf, "passed": passed, "warnings": warnings}


def _link(ws, row: int, col: int, url: str) -> None:
    cell = ws.cell(row=row, column=col)
    cell.value = url
    if url.startswith("http"):
        cell.hyperlink = url
        cell.font = Font(color="0563C1", underline="single")


def _sheet(wb: Workbook, title: str, cols: list[str], rows: list[list], link_col: str | None = None, first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(ws.max_row, 2)}"
    for i, name in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(name, 14)
        if name in WRAP:
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
    if link_col:
        li = cols.index(link_col) + 1
        for ridx in range(2, ws.max_row + 1):
            url = ws.cell(row=ridx, column=li).value
            if url:
                _link(ws, ridx, li, str(url))
    return ws


def build_workbook(board: dict, parts: dict, md_path: Path, reviewed_at: datetime) -> Workbook:
    wb = Workbook()
    _sheet(wb, "Picks", PICKS, [
        [i, r["id"], r["score"], r["claude_tier"], r["title"], r["company"], r["city"], r["work_mode"], r["posted"], r["date_flag"],
         r["years"], r["pay"], r["why"], r["gap"], r["link"]] for i, r in enumerate(parts["picks"], 1)], "Link", first=True)
    _sheet(wb, "Date Unconfirmed", UNCONFIRMED, [
        [r["id"], r["score"], r["title"], r["company"], r["city"], r["work_mode"], r["date_flag"] or "no date field",
         "yes" if r["worth"] else "no", r["claude_tier"], r["why"], r["gap"], r["link"]] for r in parts["unconfirmed"]], "Link")
    _sheet(wb, "Passed Over", PASSED, [
        [r["id"], r["score"], r["title"], r["company"], r["city"], r["reason"]] for r in parts["passed"]])
    h = board["header"]
    info = [
        ["Board run", h.get("run", "")], ["Window", h.get("window", "")], ["Boards", h.get("boards scanned", "")],
        ["Board counts", h.get("counts", "")], ["Source file", str(md_path)],
        ["Review written", reviewed_at.strftime("%Y-%m-%d %H:%M")],
        ["Picks", len(parts["picks"])], ["Date Unconfirmed (worth checking / listed)",
                                          f"{sum(1 for r in parts['unconfirmed'] if r['worth'])} / {len(parts['unconfirmed'])}"],
        ["Passed Over", len(parts["passed"])],
        ["Apply now / Strong / Stretch", " / ".join(str(sum(1 for r in parts["picks"] if r["claude_tier"] == t)) for t in TIER_ORDER)],
        ["Warnings", "; ".join(parts["warnings"]) or "none"],
    ]
    _sheet(wb, "Run Info", INFO, info)
    return wb


def _save(wb: Workbook, target: Path, now: datetime) -> tuple[Path, bool]:
    """Save to target; if it exists or is open in Excel, save <name>_<timestamp>.xlsx next to it. Returns (path, used_copy)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return _save_copy(wb, target, now)
    try:
        wb.save(target)
        return target, False
    except PermissionError:
        return _save_copy(wb, target, now)


def _save_copy(wb: Workbook, target: Path, now: datetime) -> tuple[Path, bool]:
    alt = target.with_name(f"{target.stem}_{now:%H%M%S}{target.suffix}")
    n = 1
    while alt.exists():
        alt = target.with_name(f"{target.stem}_{now:%H%M%S}_{n}{target.suffix}")
        n += 1
    wb.save(alt)
    return alt, True


def write_review(review: dict, md_path: Path | None = None, out_dir: Path | None = None, now: datetime | None = None) -> dict:
    """Build and save the workbook. Returns {"path", "used_copy", "counts", "warnings"}."""
    now = now or datetime.now()
    md_path = md_path or config.output_dir() / "jobs_latest.md"
    board = markdown.parse(md_path.read_text(encoding="utf-8"))
    parts = build_rows(board, load_verdicts(review))
    run_date = (board["header"].get("title") or "").rsplit(" ", 1)[-1] or now.date().isoformat()
    out_dir = out_dir or config.output_dir() / "reviews"
    path, copy = _save(build_workbook(board, parts, md_path, now), out_dir / f"Claude_Review_{run_date}.xlsx", now)
    return {"path": str(path), "used_copy": copy, "warnings": parts["warnings"],
            "counts": {"picks": len(parts["picks"]), "date_unconfirmed": len(parts["unconfirmed"]), "passed_over": len(parts["passed"])}}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m board.write_review", description=__doc__.split("\n")[0])
    ap.add_argument("review", type=Path, help="the review JSON ({\"verdicts\": [...]})")
    ap.add_argument("--md", type=Path, help="jobs_latest.md (default: output\\jobs_latest.md)")
    ap.add_argument("--out", type=Path, help="output folder (default: output\\reviews)")
    args = ap.parse_args(argv)
    try:
        res = write_review(json.loads(args.review.read_text(encoding="utf-8")), args.md, args.out)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Review workbook: {res['path']}" + (" (today's file exists or is open, so this is a timestamped copy)" if res["used_copy"] else ""))
    c = res["counts"]
    print(f"Picks {c['picks']}, Date Unconfirmed {c['date_unconfirmed']}, Passed Over {c['passed_over']}")
    for w in res["warnings"]:
        print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
