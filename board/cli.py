from __future__ import annotations

import argparse
import sys
import json
from datetime import date
from pathlib import Path

from . import applications, config, excel, pipeline


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m board")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="fetch every board, filter, score, write jobs_latest.md and the Excel board (no Claude calls)")
    run.add_argument("--only", nargs="*", help="company or ats names, e.g. greenhouse Stripe")
    run.add_argument("--refresh", action="store_true", help="refetch JDs even when cached")
    run.add_argument("--no-fetch", action="store_true", help="never fetch a JD the API did not return (cached JDs only)")
    run.add_argument("--no-detail", action="store_true", help="skip per-job detail calls (list data only)")
    run.add_argument("--history", type=Path, help="scan-history.tsv for the scan.mjs providers (Apple, Google, ...)")
    run.add_argument("--out", type=Path, help="output directory (default: output)")
    run.add_argument("--workers", type=int, default=4, help="parallel JD loads")
    run.add_argument("--top", type=int, default=10, help="roles to print")
    run.add_argument("--state", type=Path, help="state directory (default: state)")
    run.add_argument("--board-timeout", type=float, help="per-board time limit in seconds (default: fetch.board_time_limit_seconds)")
    run.add_argument("--full", action="store_true", help="pull every posting from Workday / SmartRecruiters / Amazon / Eightfold / Oracle (no keyword queries); for testing")
    run.add_argument("--no-excel", action="store_true", help="skip the Excel workbooks")
    run.add_argument("--with-claude", action="store_true",
                     help="also run the career-ops qualification gate (liveness, evaluation); makes claude calls, off by default")
    run.add_argument("--scan-receipt", type=Path, help="career-ops scan --json receipt (default: state/last_scan.json)")
    fe = sub.add_parser("fetch", help="fetch every board (Greenhouse/Lever/Ashby/Workday/... APIs), prefilter, write state/fetched.json")
    fe.add_argument("--only", nargs="*", help="company or ats names, e.g. greenhouse Stripe")
    fe.add_argument("--full", action="store_true", help="pull every posting (no keyword queries); for testing")
    fe.add_argument("--no-detail", action="store_true", help="skip per-job detail calls (list data only)")
    fe.add_argument("--state", type=Path, help="state directory (default: state)")
    fe.add_argument("--history", type=Path, help="scan-history.tsv for the scan.mjs providers")
    tb = sub.add_parser("table", help="print row, company, title, kept/hidden, reason for a run")
    tb.add_argument("--ids", default="", help="Excel IDs, e.g. 1-17 or 1,5,8")
    tb.add_argument("--date", help="run date (YYYY-MM-DD), default latest")
    tb.add_argument("--out", type=Path)
    tb.add_argument("--state", type=Path)
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
    if args.cmd == "fetch":
        return _fetch(args)
    if args.cmd == "table":
        return _table(args)
    if args.cmd == "mark":
        return _mark(args)
    if args.cmd == "apply":
        return _apply(args)

    return _run(args)


def _run(args) -> int:
    import time
    from . import config, dates, markdown
    t0 = time.perf_counter()
    try:
        result = pipeline.run(only=set(args.only) if args.only else None, refresh=args.refresh, fetch_missing=not args.no_fetch,
                              detail=not args.no_detail, history=args.history, out_dir=args.out, state_dir=args.state,
                              workers=args.workers, with_claude=args.with_claude, board_limit=args.board_timeout, full=args.full)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    fetch_info = result["fetch"]
    if fetch_info["boards"] and len(fetch_info["failed"]) == fetch_info["boards"]:
        print("error: every board failed; check the internet connection", file=sys.stderr)
        return 2
    out = {"ids": {}, "board_nums": [], "board": len(result["shown"]), "hidden": len(result["hidden"]), "new": 0,
           "snapshot": "", "master": "", "locked": False, "notes": []}
    if not args.no_excel:
        try:
            out = excel.write_outputs(result, out_dir=args.out, state_dir=args.state, scan_receipt=args.scan_receipt)
        except PermissionError as exc:
            print(f"error: could not write the workbook: {exc}", file=sys.stderr)
            return 3
        _write_last_run(result, out, args.state)
    latest, dated = markdown.write(result, out["ids"], out["board_nums"], out_dir=args.out)
    log_dir = config.ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"board_{result['run_date']}.log").write_text(
        pipeline.format_detail(result, out, time.perf_counter() - t0), encoding="utf-8")
    print(pipeline.format_console(result, out, latest, top=args.top, seconds=time.perf_counter() - t0, log=log_dir / f"board_{result['run_date']}.log"))
    return 0


def _write_last_run(result: dict, out: dict, state_dir: Path | None) -> None:
    """state/last_run.json: what jobboard.ps1 prints in its summary."""
    sdir = state_dir or config.state_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_date": result["run_date"], "history_rows": result["history_rows"], "gate": result.get("gate", {}),
        "funnel": {k: result["funnel"][k] for k in ("steps", "totals", "classifier", "dropped_by_step")},
        "roles_found": len(result["shown"]) + len(result["hidden"]), "kept": len(result["shown"]),
        "hidden": len(result["hidden"]), "board_total": out["board"], "new": out["new"], "hidden_by_rule": result["hidden_by_rule"], "snapshot": out["snapshot"],
        "master": out["master"], "locked": out["locked"], "notes": out["notes"],
    }
    (sdir / "last_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


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
            print(f"ID {args.id}  {req['job']['company']} | {req['job']['title']}  (from {req['job']['source']})")
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


def _fetch(args) -> int:
    from . import config, dates, fetch

    start = dates.run_start()
    raw_dir = config.ROOT / "logs" / "raw" / dates.chicago_date(start).isoformat()
    res = fetch.fetch_all(start, only=set(args.only) if args.only else None, raw_dir=raw_dir, detail=not args.no_detail,
                          history=args.history, full=args.full)
    path = fetch.save_result(res, args.state)
    failed = res.failed
    print(f"fetch {dates.chicago_date(start)} window {res.days}d: {len(res.boards)} boards, {len(failed)} failed, "
          f"{len(res.jobs)} kept ({sum(1 for j in res.jobs if not j.posted.confirmed)} date unconfirmed), "
          f"{len(res.review)} 'title not on list' for review, {res.elapsed:.0f}s")
    for b in failed[:10]:
        print(f"  FAILED {b.ats}:{b.name} {b.status} {b.note}")
    print(f"  wrote {path}; raw responses in {raw_dir}")
    return 0 if len(failed) < len(res.boards) else 2
