from __future__ import annotations

import argparse
import sys
import json
from datetime import date
from pathlib import Path

from . import applications, excel, pipeline, review


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m board")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="read scan history, fetch JDs, filter, score")
    run.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests and replays")
    run.add_argument("--limit", type=int, help="process at most N candidate rows")
    run.add_argument("--refresh", action="store_true", help="refetch JDs even when cached")
    run.add_argument("--no-fetch", action="store_true", help="use cached JDs only, no network")
    run.add_argument("--history", type=Path, help="scan-history.tsv path (default: CAREER_OPS_ROOT/data/scan-history.tsv)")
    run.add_argument("--out", type=Path, help="output directory (default: output)")
    run.add_argument("--workers", type=int, default=4, help="parallel JD fetches")
    run.add_argument("--top", type=int, default=15, help="rows to print")
    run.add_argument("--state", type=Path, help="state directory (default: state)")
    run.add_argument("--no-excel", action="store_true", help="skip the Excel workbooks")
    run.add_argument("--no-gate", action="store_true", help="skip the career-ops qualification gate (liveness, years, keywords, evaluation)")
    run.add_argument("--refresh-careerops", action="store_true", help="re-run career-ops evaluations even when cached")
    tb = sub.add_parser("table", help="print row, company, title, kept/hidden, reason for a run")
    tb.add_argument("--ids", default="", help="Excel IDs, e.g. 1-17 or 1,5,8")
    tb.add_argument("--date", help="run date (YYYY-MM-DD), default latest")
    tb.add_argument("--out", type=Path)
    tb.add_argument("--state", type=Path)
    run.add_argument("--scan-receipt", type=Path, help="career-ops scan --json receipt (default: state/last_scan.json)")
    rev = sub.add_parser("review", help="support for the /review command")
    rsub = rev.add_subparsers(dest="rcmd", required=True)
    prep = rsub.add_parser("prepare", help="pick the top rows of today's board and write state/review_packet.json")
    prep.add_argument("--top", type=int, default=25)
    prep.add_argument("--all", action="store_true", help="include rows that already have a review")
    prep.add_argument("--date", help="board date (YYYY-MM-DD), default today")
    app = rsub.add_parser("apply", help="write reviews into the Excel files")
    app.add_argument("input", type=Path, help="JSON written by /review (state/review_input.json)")
    for sp in (prep, app):
        sp.add_argument("--out", type=Path, help="output directory (default: output)")
        sp.add_argument("--state", type=Path, help="state directory (default: state)")
    mk = sub.add_parser("mark", help="set a role's Status in the master Excel (Applied also sets Applied Date)")
    mk.add_argument("id", type=int)
    mk.add_argument("status", help="one of: " + ", ".join(excel.STATUSES))
    mk.add_argument("--date", help="Applied Date (YYYY-MM-DD), default today")
    mk.add_argument("--out", type=Path, help="output directory (default: output)")
    ap_ = sub.add_parser("apply", help="support for the /apply command")
    asub = ap_.add_subparsers(dest="acmd", required=True)
    for name, text in (("prepare", "create the application folder and request.json"),
                       ("render", "validate content.json and write the resume and cover letter PDFs"),
                       ("finish", "put the PDF paths in Excel and set Status = Docs Ready")):
        sp = asub.add_parser(name, help=text)
        sp.add_argument("id", type=int)
        sp.add_argument("--out", type=Path, help="output directory (default: output)")
    args = ap.parse_args(argv)
    if args.cmd == "review":
        return _review(args)
    if args.cmd == "table":
        return _table(args)
    if args.cmd == "mark":
        return _mark(args)
    if args.cmd == "apply":
        return _apply(args)

    try:
        result = pipeline.run(
            today=date.fromisoformat(args.today) if args.today else None,
            limit=args.limit, refresh=args.refresh, fetch=not args.no_fetch,
            history=args.history, out_dir=args.out, workers=args.workers, gate=not args.no_gate,
            refresh_careerops=args.refresh_careerops, state_dir=args.state,
            scan_receipt=excel.load_receipt(args.scan_receipt),
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(pipeline.format_summary(result, top=args.top, samples=False))
    if not args.no_excel:
        try:
            out = excel.write_outputs(result, out_dir=args.out, state_dir=args.state, scan_receipt=args.scan_receipt)
        except PermissionError as exc:
            print(f"error: could not write the workbook: {exc}", file=sys.stderr)
            return 3
        print()
        print(f"Excel: {out['board']} on Board, {out['hidden']} Hidden, {out['new']} new since last run")
        print(f"  snapshot: {out['snapshot']}")
        print(f"  master:   {out['master']}")
        for note in out["notes"]:
            print(f"  NOTE: {note}")
        _write_last_run(result, out, args.state)
    print("\n".join(pipeline.format_samples(result)))      # last, so they are easy to find at the end of every run
    return 0


def _write_last_run(result: dict, out: dict, state_dir: Path | None) -> None:
    """state/last_run.json: what jobboard.ps1 prints in its summary."""
    sdir = state_dir or review.config.state_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_date": result["run_date"], "history_rows": result["history_rows"], "gate": result.get("gate", {}),
        "funnel": {k: result["funnel"][k] for k in ("steps", "totals", "classifier", "dropped_by_step")},
        "roles_found": len(result["shown"]) + len(result["hidden"]), "kept": len(result["shown"]),
        "hidden": len(result["hidden"]), "board_total": out["board"], "new": out["new"], "hidden_by_rule": result["hidden_by_rule"], "snapshot": out["snapshot"],
        "master": out["master"], "locked": out["locked"], "notes": out["notes"],
    }
    (sdir / "last_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _review(args) -> int:
    try:
        if args.rcmd == "prepare":
            packet = review.prepare(top=args.top, day=date.fromisoformat(args.date) if args.date else None,
                                    include_reviewed=args.all, out_dir=args.out, state_dir=args.state)
            print(f"Review packet: {len(packet['rows'])} rows from {packet['source']}")
            print(f"Skipped: {packet['skipped']}   Resume: {packet['resume_path']}")
            print(f"Packet file: {(args.state or review.config.state_dir()) / 'review_packet.json'}")
            print(f"{'id':>4} {'rule':>5}  {'company':<20} {'title':<50} JD")
            for r in packet["rows"]:
                print(f"{r['id']:>4} {r['rule_score']!s:>5}  {str(r['company'])[:20]:<20} {str(r['title'])[:50]:<50} {r['jd_path'] or 'NO JD'}")
            return 0
        res = review.apply(args.input, out_dir=args.out, state_dir=args.state)
    except (FileNotFoundError, excel.MasterUnreadable, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {res['written']} reviews.")
    for c in res["changes"]:
        print(f"  RE-RANKED id {c['id']} {c['company']} | {c['title']}: {c['from']:g} -> {c['to']:g}")
        print(f"     why: {c['reason']}")
        print(f"     JD quote: \"{c['evidence']}\"")
    for w in res["warnings"]:
        print(f"  WARNING: {w}")
    if res["missing"]:
        print(f"  Not reviewed (no entry): ids {res['missing']}")
    for n in res["notes"]:
        print(f"  NOTE: {n}")
    return 0


def _mark(args) -> int:
    try:
        res = excel.mark_status(args.id, args.status, applied_date=date.fromisoformat(args.date) if args.date else None,
                                out_dir=args.out)
    except (KeyError, ValueError, excel.MasterUnreadable) as exc:
        print(f"error: {exc.args[0] if isinstance(exc, KeyError) else exc}", file=sys.stderr)
        return 2
    old = res["before"].get("Status")
    print(f"ID {args.id}  {res['company']} | {res['title']}")
    print(f"  Status: {old} -> {res['status']}" + (f"   Applied Date: {res['applied_date']}" if res["status"] == "Applied" else ""))
    for n in res["notes"]:
        print(f"  NOTE: {n}")
    return 0


def _apply(args) -> int:
    try:
        if args.acmd == "prepare":
            req = applications.prepare(args.id, out_dir=args.out)
            print(f"ID {args.id}  {req['job']['company']} | {req['job']['title']}  (status {req['job']['status']})")
            print(f"  folder:   {req['folder']}")
            print(f"  request:  {Path(req['folder']) / 'request.json'}")
            print(f"  write:    {req['content_file']}")
            print(f"  mirror JD words:  {[w for k in req['keywords']['mirror'] for w in k['jd_words']]}")
            print(f"  do NOT claim:     {[w for k in req['keywords']['do_not_claim'] for w in k['jd_words']]}")
        elif args.acmd == "render":
            res = applications.render(args.id)
            print(f"Resume:       {res['resume']}  ({res['ats']['font_pt']}pt, {res['ats']['pages']} page)")
            print(f"Cover letter: {res['cover_letter']}  ({res['letter_font_pt']}pt)")
            print(f"ATS check:    {res['ats']}")
            print(f"JD skill groups covered:  {res['jd_skill_groups_covered']}")
            print(f"JD skill groups NOT in documents: {res['jd_skill_groups_not_in_documents']}")
        else:
            res = applications.finish(args.id, out_dir=args.out)
            print(f"Excel updated for ID {args.id}: Status {res['status']}")
            print(f"  Resume:       {res['resume']}")
            print(f"  Cover Letter: {res['cover_letter']}")
            if res["status_note"]:
                print(f"  {res['status_note']}")
            if not res["gap_check"]:
                print("  WARNING: GapCheck.md not found in the folder")
            for n in res["notes"]:
                print(f"  NOTE: {n}")
    except (applications.ApplyError, KeyError, excel.MasterUnreadable) as exc:
        print(f"error: {exc.args[0] if isinstance(exc, KeyError) else exc}", file=sys.stderr)
        return 2
    return 0


def _ids(spec: str) -> set[int]:
    out: set[int] = set()
    for part in filter(None, (p.strip() for p in spec.split(","))):
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def _table(args) -> int:
    out_dir = args.out or excel.config.output_dir()
    state = args.state or excel.config.state_dir()
    if args.date:
        path = out_dir / f"run_{args.date}.json"
    else:
        runs = sorted(out_dir.glob("run_*.json"))
        path = runs[-1] if runs else out_dir / "run_none.json"
    if not path.exists():
        print(f"error: {path} not found; run python -m board run first", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    ledger = json.loads((state / "ids.json").read_text(encoding="utf-8")).get("jobs", {}) if (state / "ids.json").exists() else {}
    want = _ids(args.ids) if args.ids else None
    rows = []
    for verdict, items in (("kept", data["shown"]), ("hidden", data["hidden"])):
        for j in items:
            num = ledger.get(j["id"], {}).get("id")
            if num is None or (want is not None and num not in want):
                continue
            reason = "; ".join(r["text"] for r in j["reasons"]) if j["reasons"] else "; ".join(j["flags"][:3])
            co = j.get("careerops_score")
            rows.append((num, j["company"], j["title"], verdict, co, reason))
    rows.sort()
    print(f"{'row':>3}  {'company':<16} {'title':<50} {'result':<6} {'co-ops':>6}  reason / flags")
    for num, company, title, verdict, co, reason in rows:
        print(f"{num:>3}  {company[:16]:<16} {title[:50]:<50} {verdict:<6} {'-' if co is None else format(co, 'g'):>6}  {reason}")
    return 0
