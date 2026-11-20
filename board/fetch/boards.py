"""Board definitions: one dict per scannable company in config/companies.yaml, parsed from its careers_url.

`ats` is the Python fetcher to use, or "scanmjs" for providers that only the career-ops scan can read (Apple, Google,
Pinpoint, own sites). Entries whose URL does not parse come back with ats "unsupported" and a note, so they are logged
as failed boards and never silently skipped.
"""
from __future__ import annotations

import re
import urllib.parse

from .. import config

_SCANMJS = ("apple", "google", "pinpoint", "own")


def _ats_of(entry: dict) -> str:
    first = re.split(r"[\s(:]", (entry.get("ats") or "").strip().lower(), maxsplit=1)[0]
    if first == "amazon.jobs":
        return "amazon"
    if first == "oracle":
        return "oracle"
    return first


def parse_board(entry: dict) -> dict:
    ats, url = _ats_of(entry), entry.get("careers_url") or ""
    base = {"name": entry["name"], "ats": ats, "url": url}
    if ats in _SCANMJS:
        return {**base, "ats": "scanmjs"}
    if ats == "amazon":
        return {**base, "key": "amazon"}
    if ats in ("greenhouse", "ashby", "lever", "smartrecruiters"):
        host = {"greenhouse": r"greenhouse\.io", "ashby": r"ashbyhq\.com", "lever": r"lever\.co",
                "smartrecruiters": r"smartrecruiters\.com"}[ats]
        m = re.search(host + r"/([^/?#]+)", url)
        return {**base, "key": m.group(1)} if m else {**base, "ats": "unsupported", "note": f"no slug in {url}"}
    if ats == "workday":
        m = re.search(r"//([^./]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([^/?#]+)", url)
        if m:
            return {**base, "key": m.group(1), "tenant": m.group(1), "wd": m.group(2), "site": m.group(3),
                    "facet": entry.get("workday_facet")}         # finance category ids, cached by `python -m board workday-facets`
        return {**base, "ats": "unsupported", "note": f"unreadable Workday url {url}"}
    if ats == "oracle":
        m = re.search(r"//([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/?#]+)", url)
        if m:
            return {**base, "key": m.group(1), "host": m.group(1), "site": m.group(2)}
        return {**base, "ats": "unsupported", "note": f"unreadable Oracle url {url}"}
    if ats == "eightfold":
        parts = urllib.parse.urlsplit(url)
        domain = urllib.parse.parse_qs(parts.query).get("domain", [""])[0]
        if parts.netloc and domain:
            return {**base, "key": parts.netloc, "host": parts.netloc, "domain": domain}
        return {**base, "ats": "unsupported", "note": f"no domain in {url}"}
    return {**base, "ats": "unsupported", "note": f"no fetcher for ats '{entry.get('ats')}'"}


def load_boards(only: set[str] | None = None) -> list[dict]:
    """Boards for every company with scan_status OK. `only` matches company names or ats names, case-insensitive."""
    want = {o.lower() for o in only} if only else None
    out = []
    for entry in config.companies_config():
        if entry.get("scan_status") != "OK":
            continue
        b = parse_board(entry)
        if want and b["name"].lower() not in want and b["ats"] not in want:
            continue
        out.append(b)
    return out
