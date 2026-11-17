"""output\\jobs_latest.md: the plain-text board Claude reads. Same roles and IDs as the Board sheet of JobBoard_Master.xlsx.

Sections: header (run time, window, boards scanned / failed / partial, counts), Board (confirmed dates, best score first),
Date unconfirmed (passed every filter but the posting date is not confirmed, with the reason), Hidden (one line per role
with an ID, so it can be overridden). Plain Markdown, no tables.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config, dates, score

_HEADING = re.compile(r"^\W*(?:basic |minimum |required |preferred |key )?(?:qualifications|requirements|what you(?:'|’)?ll need|"
                      r"what we(?:'|’)?re looking for|about you|you have|who you are|skills and experience|experience)\b", re.I)
_REQ_WORDS = re.compile(r"\byears?\b|experience|proficien|degree|bachelor|knowledge of|ability to|skills?\b|excel|sql|python", re.I)


def requirement_lines(jd_text: str, n: int = 6, width: int = 220) -> list[str]:
    """Up to n lines quoted from the JD: the lines under a Qualifications / Requirements heading, else lines that sound like requirements."""
    lines = [re.sub(r"\s+", " ", l).strip(" -*•\t#>") for l in (jd_text or "").splitlines()]
    lines = [l for l in lines if l]
    picked: list[str] = []
    for i, l in enumerate(lines):
        if _HEADING.match(l) and len(l) < 80:
            for nxt in lines[i + 1:i + 1 + 14]:
                if _HEADING.match(nxt) and len(nxt) < 80 and picked:
                    break
                if len(nxt) >= 20 and not nxt.endswith(":"):
                    picked.append(nxt)
                if len(picked) >= n:
                    break
        if len(picked) >= n:
            break
    if not picked:
        picked = [l for l in lines if 20 <= len(l) <= 400 and _REQ_WORDS.search(l)][:n]
    return [p if len(p) <= width else p[:width - 1].rstrip() + "…" for p in picked[:n]]


def _city(location: str) -> str:
    parts = [p.strip() for p in re.split(r"[;|]", location or "") if p.strip()]
    return parts[0] if parts else "location not stated"


def _work_mode(job: dict) -> str:
    m = job.get("work_mode") or "unknown"
    if m == "unknown" and job.get("workplace"):
        m = str(job["workplace"]).lower()
    return {"onsite": "on-site", "unknown": "not stated"}.get(m, m)


def _years(job: dict) -> str:
    y = job.get("years_required")
    if y is None:
        return "not stated"
    return f"{int(y) if float(y).is_integer() else y}"


def _role(num: int, job: dict, jd_root: Path | None) -> list[str]:
    tier = score.tier_for(job.get("score") or 0)
    parts = job.get("score_parts") or {}
    posted = job["posted"] if job.get("posted") and not job.get("date_flag") else f"DATE UNCONFIRMED ({job.get('date_flag') or 'no date field'})"
    lines = [f"### [{num}] {job.get('score')} · {tier or 'below 50'} · {job.get('track') or parts.get('track', '')} — {job['title']} — {job['company']}",
             f"- City: {_city(job['location'])} · Work mode: {_work_mode(job)} · Posted: {posted} · Years required: {_years(job)}"
             f" · Pay: {job.get('pay') or 'not listed'}"]
    train = job.get("training") or parts.get("training_phrases") or []
    lines.append("- Training: " + ("; ".join(train) if train else "no signal found"))
    reqs = job.get("requirements") or []
    if reqs:
        lines.append("- Requirements (quoted from the JD):")
        lines += [f"  > {r}" for r in reqs]
    flags = [f for f in job.get("flags", []) if not f.startswith("date unconfirmed")]
    if flags:
        lines.append("- Flags: " + "; ".join(flags[:6]))
    lines.append(f"- Link: {job['url']}")
    lines.append(f"- JD file: {job.get('jd_path') or 'not saved'}")
    return lines


def render(result: dict, ids: dict[str, int], board_nums: list[int], generated: datetime | None = None) -> str:
    """ids: job id -> Excel ID; board_nums: Excel IDs on the Board sheet, in Board order."""
    generated = generated or datetime.fromisoformat(result["run_start"])
    chi = generated.astimezone(dates.CHICAGO)
    fetch = result.get("fetch", {})
    since = (chi.date() - timedelta(days=int(result["window_days"]))).isoformat()
    by_num = {ids[j["id"]]: j for j in result["shown"] + result["hidden"] if j["id"] in ids}
    board = [by_num[n] and (n, by_num[n]) for n in board_nums if n in by_num]
    confirmed = [(n, j) for n, j in board if not j.get("date_flag")]
    unconfirmed = [(n, j) for n, j in board if j.get("date_flag")]
    on_board = set(board_nums)
    hidden = [(ids[j["id"]], j) for j in result["hidden"] if j["id"] in ids and ids[j["id"]] not in on_board]
    failed = fetch.get("failed", [])
    partial = fetch.get("partial", [])
    out = [
        f"# Job board, run {chi.date().isoformat()}",
        "",
        f"- Run: {chi:%Y-%m-%d %H:%M} America/Chicago ({generated.astimezone(dates.UTC):%Y-%m-%d %H:%M} UTC)",
        f"- Window: {result['window_days']} days, posted on or after {since} (Chicago). Confirmed publish dates only; "
        "roles without one are in \"Date unconfirmed\".",
        f"- Boards scanned: {fetch.get('boards', 0)} · failed: {len(failed)}"
        + (" (" + ", ".join(f"{f['name']}: {f['status']}" for f in failed) + ")" if failed else "")
        + f" · partial (hit the time limit): {len(partial)}" + (" (" + ", ".join(p["name"] for p in partial) + ")" if partial else ""),
        f"- Counts: kept {len(confirmed)} · date unconfirmed {len(unconfirmed)} · hidden {len(hidden)} (with an ID) · "
        f"dropped before the JD stage {len(result.get('dropped', [])):,} (see Dropped_{chi.date().isoformat()}.csv)",
        f"- Fetch time: {fetch.get('elapsed_seconds', 0):.0f}s · Score: role fit 35, level 20, skills 15, training 15, future-facing 10, location 5",
        "",
        f"## Board ({len(confirmed)})",
        "",
    ]
    for n, j in confirmed:
        out += _role(n, j, None) + [""]
    if not confirmed:
        out += ["No role passed every filter with a confirmed date.", ""]
    out += [f"## Date unconfirmed ({len(unconfirmed)})", "",
            "Passed every other filter; the posting date could not be confirmed. Check it before applying.", ""]
    for n, j in unconfirmed:
        out += _role(n, j, None) + [""]
    out += [f"## Hidden ({len(hidden)})", "",
            "One line per hidden role: [ID] title — company — reason. Override by ID in the Hidden sheet of JobBoard_Master.xlsx.", ""]
    for n, j in sorted(hidden, key=lambda x: -(x[1].get("score") or 0)):
        why = "; ".join(r["text"] for r in j["reasons"]) or "hidden"
        out.append(f"- [{n}] {j['title']} — {j['company']} — {why}")
    return "\n".join(out) + "\n"


def write(result: dict, ids: dict[str, int], board_nums: list[int], out_dir: Path | None = None) -> tuple[Path, Path]:
    """Write jobs_latest.md and the dated copy jobs_<date>.md (atomic). Returns (latest, dated)."""
    out_dir = out_dir or config.output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    text = render(result, ids, board_nums)
    latest, dated = out_dir / "jobs_latest.md", out_dir / f"jobs_{result['run_date']}.md"
    for path in (dated, latest):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return latest, dated


# ---------------------------------------------------------------------------------------------------------------------
# reading jobs_latest.md back (the review workbook and its tests use this; nothing here reads an .xlsx)
# ---------------------------------------------------------------------------------------------------------------------

_ROLE = re.compile(r"^### \[(\d+)\] ([\d.]+) · (.+?) · (.+)$")
_SECTION = re.compile(r"^## (Board|Date unconfirmed|Hidden) \((\d+)\)\s*$")


def parse(text: str) -> dict:
    """jobs_latest.md -> {"header": {...}, "board": [role], "unconfirmed": [role], "hidden": [{id, line}]}.

    A role is {id, score, tier, track, title, company, city, work_mode, posted, date_flag, years, pay, training, requirements,
    flags, link, jd_path}. `date_flag` is "" for a confirmed date, else the reason.
    """
    header: dict[str, str] = {}
    out: dict = {"header": header, "board": [], "unconfirmed": [], "hidden": []}
    section, role = "header", None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _SECTION.match(line)
        if m:
            section = {"Board": "board", "Date unconfirmed": "unconfirmed", "Hidden": "hidden"}[m.group(1)]
            role = None
            header["count_" + section] = m.group(2)
            continue
        if section == "header":
            if line.startswith("# Job board"):
                header["title"] = line[2:]
            elif line.startswith("- ") and ": " in line:
                key, _, val = line[2:].partition(": ")
                header[key.strip().lower()] = val.strip()
            continue
        if section == "hidden":
            h = re.match(r"^- \[(\d+)\] (.+)$", line)
            if h:
                out["hidden"].append({"id": int(h.group(1)), "line": h.group(2)})
            continue
        m = _ROLE.match(line)
        if m:
            rest = m.group(4)
            body, _, company = rest.rpartition(" — ")
            track, _, title = body.partition(" — ")
            role = {"id": int(m.group(1)), "score": float(m.group(2)), "tier": m.group(3), "track": track, "title": title,
                    "company": company, "city": "", "work_mode": "", "posted": "", "date_flag": "", "years": "", "pay": "",
                    "training": "", "requirements": [], "flags": "", "link": "", "jd_path": ""}
            out[section].append(role)
            continue
        if role is None:
            continue
        if line.startswith("- City: "):
            for part in line[2:].split(" · "):
                key, _, val = part.partition(": ")
                key = key.strip().lower()
                if key == "city":
                    role["city"] = val
                elif key == "work mode":
                    role["work_mode"] = val
                elif key == "posted":
                    flag = re.match(r"^DATE UNCONFIRMED \((.*)\)$", val)
                    role["posted"], role["date_flag"] = ("", flag.group(1)) if flag else (val, "")
                elif key == "years required":
                    role["years"] = val
                elif key == "pay":
                    role["pay"] = val
        elif line.startswith("- Training: "):
            role["training"] = line[len("- Training: "):]
        elif line.startswith("  > "):
            role["requirements"].append(line[4:])
        elif line.startswith("- Flags: "):
            role["flags"] = line[len("- Flags: "):]
        elif line.startswith("- Link: "):
            role["link"] = line[len("- Link: "):]
        elif line.startswith("- JD file: "):
            role["jd_path"] = line[len("- JD file: "):]
    return out
