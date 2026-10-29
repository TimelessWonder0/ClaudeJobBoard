"""Support for the /review command.

  python -m board review prepare [--top 25] [--all] [--date YYYY-MM-DD]
      Pick the top rows of today's board and write state/review_packet.json for Claude to read.
  python -m board review apply state/review_input.json
      Validate Claude's reviews and write Fit Score / Why it fits / Gaps into the Excel files.

Claude writes the judgment; this module only selects rows and enforces the rules:
  - Why and Gaps are single lines.
  - A score change needs a reason AND a verbatim quote from the JD (checked against JD.md), and
    may not move more than MAX_SHIFT points from the rule score. Otherwise the rule score stays.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from . import config, excel

REVIEWABLE = ("New", "Interested", "Docs Ready")
MAX_SHIFT = 25
MAX_LINE = 200


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("‘", "'")).strip().lower()


def _jd_for(key: str) -> Path | None:
    base = config.jobs_dir()
    for folder in sorted(base.glob(f"{key}_*")) if base.exists() else []:
        if (folder / "JD.md").exists():
            return folder / "JD.md"
    return None


def _jd_body(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    return raw.split("\n---\n", 1)[1] if "\n---\n" in raw else raw


def prepare(top: int = 25, day: date | None = None, include_reviewed: bool = False,
            out_dir: Path | None = None, state_dir: Path | None = None) -> dict:
    day = day or date.today()
    out_dir = out_dir or config.output_dir()
    sdir = state_dir or config.state_dir()
    path = out_dir / f"JobBoard_{day.isoformat()}.xlsx"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run python -m board run first (or pass --date).")
    board = excel.read_master(path).board
    reviews = _read_json(sdir / "reviews.json", {})

    def score_of(row) -> float:
        n = excel._num(row.get("Fit Score (0-100)"))
        return n if n is not None else -1.0

    ranked = sorted(board.values(), key=lambda r: (-score_of(r), int(r["ID"])))
    rows = []
    skipped = {"status": 0, "already_reviewed": 0}
    for r in ranked:
        num = int(r["ID"])
        if (r.get("Status") or "New") not in REVIEWABLE:
            skipped["status"] += 1
            continue
        if str(num) in reviews and not include_reviewed:
            skipped["already_reviewed"] += 1
            continue
        if len(rows) >= top:
            break
        key = str(r.get("Key") or "")
        jd = _jd_for(key)
        facts = _read_json(jd.parent / "facts.json", {}) if jd else {}
        ext = facts.get("extraction") or {}
        yr, deg = ext.get("years_required") or {}, ext.get("degree_required") or {}
        prior = reviews.get(str(num), {})
        rows.append({
            "id": num, "key": key, "company": r.get("Company"), "title": r.get("Title"),
            "location": r.get("Location / Metro"), "track": r.get("Track"), "status": r.get("Status") or "New",
            "rule_score": prior.get("rule_score", r.get("Fit Score (0-100)")),
            "current_score": r.get("Fit Score (0-100)"), "flags": r.get("Flags") or "",
            "score_parts": facts.get("score_parts", {}),
            "years_required": yr.get("min_years"), "years_evidence": yr.get("evidence", ""),
            "degree_required": deg.get("level"), "degree_evidence": deg.get("evidence", ""),
            "certs": ext.get("certs", []), "jd_path": str(jd) if jd else "",
            "jd_chars": len(_jd_body(jd)) if jd else 0,
        })
    packet = {"date": day.isoformat(), "top": top, "source": str(path), "skipped": skipped,
              "resume_path": str(config.data_root() / "cv.md"), "rows": rows}
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "review_packet.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    return packet


def _one_line(text: str, label: str, warnings: list[str]) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) > MAX_LINE:
        warnings.append(f"{label} longer than {MAX_LINE} chars; truncated")
        s = s[: MAX_LINE - 1].rstrip() + "…"
    return s


def validate(raw: dict, packet: dict) -> tuple[dict[int, dict], list[str], list[dict]]:
    """Return (updates for excel.apply_reviews, warnings, change_log)."""
    by_id = {r["id"]: r for r in packet["rows"]}
    updates: dict[int, dict] = {}
    warnings: list[str] = []
    changes: list[dict] = []
    for item in raw.get("reviews", []):
        try:
            num = int(item["id"])
        except (KeyError, TypeError, ValueError):
            warnings.append(f"review without a valid id skipped: {item!r:.80}")
            continue
        row = by_id.get(num)
        if row is None:
            warnings.append(f"id {num} was not in the review packet; skipped")
            continue
        why = _one_line(item.get("why"), f"id {num} why", warnings)
        gaps = _one_line(item.get("gaps"), f"id {num} gaps", warnings)
        if not why or not gaps:
            warnings.append(f"id {num}: both a Why and a Gaps line are required; skipped")
            continue
        rule = float(row["rule_score"]) if row["rule_score"] is not None else None
        final = rule
        reason = evidence = ""
        want = item.get("score")
        if want is not None and rule is not None and float(want) != rule:
            want = float(want)
            reason = _one_line(item.get("reason"), f"id {num} reason", warnings)
            evidence = re.sub(r"\s+", " ", str(item.get("evidence") or "")).strip()
            problem = ""
            if not (0 <= want <= 100):
                problem = "score outside 0-100"
            elif abs(want - rule) > MAX_SHIFT:
                problem = f"change of {abs(want - rule):.0f} points exceeds the {MAX_SHIFT}-point limit"
            elif not reason:
                problem = "no reason given"
            elif not evidence:
                problem = "no JD quote given"
            elif not row["jd_path"]:
                problem = "no JD on file to check the quote against"
            elif _norm(evidence) not in _norm(_jd_body(Path(row["jd_path"]))):
                problem = "the quoted evidence is not in JD.md"
            if problem:
                warnings.append(f"id {num}: score change {rule:g} -> {want:g} rejected ({problem}); rule score kept")
            else:
                final = want
                changes.append({"id": num, "company": row["company"], "title": row["title"], "from": rule, "to": want,
                                "reason": reason, "evidence": evidence})
        updates[num] = {"final": final, "rule": rule, "why": why, "gaps": gaps, "reason": reason if final != rule else "",
                        "evidence": evidence if final != rule else ""}
    return updates, warnings, changes


def apply(input_path: Path, out_dir: Path | None = None, state_dir: Path | None = None,
          now: datetime | None = None) -> dict:
    sdir = state_dir or config.state_dir()
    packet = _read_json(sdir / "review_packet.json", None)
    if not packet:
        raise FileNotFoundError("state/review_packet.json missing; run python -m board review prepare first")
    raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
    updates, warnings, changes = validate(raw, packet)
    missing = [r["id"] for r in packet["rows"] if r["id"] not in updates]
    result = {"written": 0, "warnings": warnings, "changes": changes, "missing": missing, "notes": []}
    if updates:
        out = excel.apply_reviews({k: {kk: vv for kk, vv in v.items()} for k, v in updates.items()}, now=now,
                                  out_dir=out_dir, state_dir=sdir)
        result.update(written=out["reviewed"], notes=out["notes"], master=out["master"], snapshot=out["snapshot"])
    return result
