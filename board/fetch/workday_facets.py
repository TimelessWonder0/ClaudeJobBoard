"""Workday Finance facet: find each tenant's Job Category facet value(s) for Finance once, and cache them in companies.yaml.

A Workday list call (`POST /wday/cxs/<tenant>/<site>/jobs`) returns `facets` next to the postings. With an empty search the
"Job Category" facet (`jobFamilyGroup`, sometimes only `jobFamily`) lists every category on the board with its id and count.
The ids that name a finance category are stored on the company's line in companies.yaml:

    workday_facet: {param: jobFamilyGroup, ids: [0c40f6bd1d...], names: [Finance], jobs: 14, as_of: '2026-11-20'}
    workday_facet: {param: none, as_of: '2026-11-20'}          # the tenant has no finance category

`list_workday` then runs the facet query (empty search) plus the short `fetch.workday_fallback_keywords` without the
facet, merged by job id, so finance roles filed under another category are still found. A tenant with `param: none` gets
the fallback keywords only; a tenant never discovered keeps the full `fetch.workday_keywords`.

Discovery is one request per tenant, sequential, with a pause between tenants (`--delay`). It is meant to be run once by
hand, not by `jobboard`:

    python -m board workday-facets                 # every Workday tenant not yet discovered
    python -m board workday-facets --only NVIDIA PNC --refresh
    python -m board workday-facets --dry-run       # print what it would write, change nothing
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import yaml

from .. import config
from .base import NoBoard, RawStore, http_json, polite_sleep
from .boards import parse_board

# Facet parameters judged, best first. Location / worker-type / time-type facets are never used.
FACET_PARAMS = ("jobFamilyGroup", "jobFamily")


def _patterns() -> tuple[re.Pattern, re.Pattern]:
    f = config.filters_config()["fetch"]
    return re.compile(f["workday_facet_pattern"], re.I), re.compile(f["workday_facet_exclude"], re.I)


def pick_facet(facets: list[dict]) -> dict | None:
    """The finance values of the best category facet: {param, ids, names, jobs}, or None when no category names finance."""
    want, avoid = _patterns()
    by_param = {f.get("facetParameter"): f for f in facets or [] if isinstance(f, dict)}
    for param in FACET_PARAMS:
        values = (by_param.get(param) or {}).get("values") or []
        hits = [v for v in values if v.get("id") and want.search(v.get("descriptor") or "") and not avoid.search(v.get("descriptor") or "")]
        if hits:
            return {"param": param, "ids": [v["id"] for v in hits], "names": [v["descriptor"] for v in hits],
                    "jobs": sum(int(v.get("count") or 0) for v in hits)}
    return None


def discover_one(board: dict, store: RawStore | None = None) -> dict:
    """One empty-search list call for one tenant. Returns the cache value (param none when no finance category)."""
    host = f"{board['tenant']}.wd{board['wd']}.myworkdayjobs.com"
    api = f"https://{host}/wday/cxs/{board['tenant']}/{board['site']}/jobs"
    res = http_json(api, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=25,
                    store=store, tag=f"facets_{board['tenant']}")
    got = pick_facet(res.get("facets") or [])
    return got or {"param": "none"}


def _flow(value: dict) -> str:
    return yaml.safe_dump(value, default_flow_style=True, width=100000, sort_keys=False).strip()


def write_cache(found: dict[str, dict], path: Path | None = None) -> int:
    """Put `workday_facet: {...}` on each named company's line in companies.yaml (one entry per line, flow style), replacing
    an older value. Other lines, comments and order are untouched. Returns the number of lines changed."""
    path = path or config.CONFIG_DIR / "companies.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = 0
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*-\s*\{)(.*)\}(\s*)$", line)
        if not m:
            continue
        name = re.match(r"\s*name:\s*(?:'([^']*)'|\"([^\"]*)\"|([^,]*))", m.group(2))
        key = name and (name.group(1) or name.group(2) or (name.group(3) or "").strip())
        if key not in found:
            continue
        body = re.sub(r",\s*workday_facet:\s*\{[^{}]*\}", "", m.group(2))
        lines[i] = f"{m.group(1)}{body}, workday_facet: {_flow(found[key])}}}{m.group(3)}"
        changed += 1
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    yaml.safe_load(tmp.read_text(encoding="utf-8"))              # never write a file that no longer parses
    os.replace(tmp, path)
    return changed


def workday_entries(only: set[str] | None = None, refresh: bool = False) -> list[tuple[dict, dict]]:
    """(companies.yaml entry, parsed board) for every scannable Workday tenant still to discover."""
    want = {o.lower() for o in only} if only else None
    out = []
    for entry in config.companies_config():
        if entry.get("scan_status") != "OK":
            continue
        b = parse_board(entry)
        if b["ats"] != "workday" or (want and b["name"].lower() not in want):
            continue
        if entry.get("workday_facet") and not refresh and not want:
            continue
        out.append((entry, b))
    return out


def run(only: set[str] | None = None, refresh: bool = False, delay: float = 4.0, dry_run: bool = False,
        raw_dir: Path | None = None, say=print) -> dict:
    """Discover, pacing `delay` seconds between tenants. A tenant that errors is reported and left uncached (run again later).
    The cache is written after every 10 tenants and at the end, so an interrupted run keeps what it found."""
    store = RawStore(raw_dir)
    todo = workday_entries(only, refresh)
    today = date.today().isoformat()
    found: dict[str, dict] = {}
    failed: list[tuple[str, str]] = []
    say(f"Workday facet discovery: {len(todo)} tenants, {delay:g}s apart" + (" (dry run)" if dry_run else ""))
    for n, (entry, b) in enumerate(todo, 1):
        if n > 1:
            polite_sleep(delay)
        try:
            got = discover_one(b, store)
        except NoBoard as exc:
            failed.append((b["name"], f"no board: {exc}"))
            say(f"  [{n}/{len(todo)}] {b['name']}: FAILED (no board)")
            continue
        except Exception as exc:                      # noqa: BLE001 - one tenant never stops the run
            failed.append((b["name"], f"{type(exc).__name__}: {exc}"[:120]))
            say(f"  [{n}/{len(todo)}] {b['name']}: FAILED ({type(exc).__name__})")
            continue
        found[b["name"]] = {**got, "as_of": today}
        what = f"{got['param']} {', '.join(got['names'])} ({got['jobs']} jobs)" if got["param"] != "none" else "no finance category"
        say(f"  [{n}/{len(todo)}] {b['name']}: {what}")
        if not dry_run and len(found) % 10 == 0:
            write_cache(found)
    if found and not dry_run:
        write_cache(found)
    with_facet = sum(1 for v in found.values() if v["param"] != "none")
    say(f"Done: {with_facet} with a finance facet, {len(found) - with_facet} without, {len(failed)} failed"
        + ("" if dry_run else "; cached in config/companies.yaml"))
    return {"found": found, "failed": failed}


def facet_query(b: dict) -> dict | None:
    """appliedFacets for a discovered tenant, or None (no facet cached, `param: none`, or a --full run)."""
    f = b.get("facet") or {}
    if b.get("full") or not f.get("ids") or f.get("param") in (None, "none"):
        return None
    return {f["param"]: list(f["ids"])}

