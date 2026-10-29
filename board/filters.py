"""Hard filters from CLAUDE.md. Each rule appends a Reason (hidden) or a flag (shown with a note).

Rules: quant, Master's/MBA required, more than 3 years required, banned title words, outside the metros,
fully remote, posted too long ago. Ambiguous cases flag; they never hide.
"""
from __future__ import annotations

import re

from . import config
from .extract import CONF_RANK, Extraction
from .models import Job, Reason

_MASTERS_PLUS = {"masters", "mba", "phd"}


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def classify_location(location: str, cfg: dict | None = None) -> tuple[str, str, list[str]]:
    """Return (status, best_metro_key, all_matching_keys). status: match | outside | unknown."""
    cfg = cfg or config.filters_config()
    loc = (location or "").strip()
    matches: list[str] = []
    for key, metro in cfg["metros"].items():
        for city in metro["cities"]:
            if re.search(r"(?<![A-Za-z])" + re.escape(city) + r"(?![A-Za-z])", loc, re.I):
                matches.append(key)
                break
    if matches:
        return "match", matches[0], matches   # filters.yaml lists bay_area first
    for pat in cfg.get("generic_location_patterns", []):
        if re.search(pat, loc, re.I):
            return "unknown", "", []
    if re.match(r"^\s*(us[- ]|usa[- ]|united states[ ,-]+)?remote\b", loc, re.I):
        return "unknown", "", []        # decided by the remote rule, not the metro rule
    return "outside", "", []


def apply_filters(job: Job, ext: Extraction, cfg: dict | None = None) -> None:
    cfg = cfg or config.filters_config()
    min_conf = CONF_RANK[cfg.get("min_confidence_to_hide", "medium")]
    title = job.title or ""

    # 1. quant
    for rx in _compile(cfg["quant_title_patterns"]):
        if rx.search(title):
            job.reasons.append(Reason("quant", "Quant role (title)", title))
            break
    else:
        for rx in _compile(cfg["quant_jd_strong_patterns"]):
            m = rx.search(job.jd_text or "")
            if m:
                job.reasons.append(Reason("quant", "Quant role (description)", _around(job.jd_text, m)))
                break
        else:
            if any(rx.search(job.jd_text or "") for rx in _compile(cfg["quant_jd_flag_patterns"])):
                job.flags.append("quantitative wording in JD")

    # 2. Master's / MBA required
    deg = ext.degree_required
    if deg and deg.level in _MASTERS_PLUS:
        if CONF_RANK[deg.confidence] >= min_conf:
            job.reasons.append(Reason("degree_required", f"{deg.level.upper() if deg.level == 'mba' else deg.level.title()} required",
                                      deg.evidence))
        else:
            job.flags.append(f"{deg.level} may be required (low confidence)")
    if ext.degree_preferred and ext.degree_preferred.level in _MASTERS_PLUS:
        job.flags.append(f"{ext.degree_preferred.level} preferred")

    # 3. more than N years required
    yr = ext.years_required
    max_years = cfg["max_years_required"]
    if yr and yr.min_years > max_years:
        if CONF_RANK[yr.confidence] >= min_conf:
            job.reasons.append(Reason("years_required", f"{_fmt(yr.min_years)}+ years required (limit {max_years})", yr.evidence))
        else:
            job.flags.append(f"{_fmt(yr.min_years)}+ years may be required (low confidence)")
    elif yr and yr.min_years == max_years:
        job.flags.append(f"{_fmt(yr.min_years)} years required")
    if yr and yr.routes and yr.years_bachelors and yr.years_bachelors > yr.min_years:
        job.flags.append(f"degree routes: {_fmt(yr.years_bachelors)} yrs with Bachelor's, {_fmt(yr.min_years)} with higher degree")

    # 4. title words
    for rx in _compile(cfg["hide_title_patterns"]):
        m = rx.search(title)
        if m:
            job.reasons.append(Reason("title", f"Title contains '{m.group(0)}'", title))
            break
    for rx in _compile(cfg["flag_title_patterns"]):
        m = rx.search(title)
        if m:
            job.flags.append(f"title: {m.group(0).lower()}")
            break

    # 5. metros
    status, metro, _all = classify_location(job.location, cfg)
    job.metro, job.metro_status = metro, status
    if status == "outside":
        job.reasons.append(Reason("metro", "Outside the 8 metros", job.location))
    elif status == "unknown":
        job.flags.append("location unknown")

    # 6. fully remote
    mode = ext.work_mode
    job.work_mode = mode.mode
    if mode.mode == "remote":
        job.reasons.append(Reason("remote", "Fully remote", mode.evidence))

    # 7. age (rows reaching here are already inside the window; kept for rows built by other callers)
    if job.age_days is not None and job.age_days > cfg["posting_max_age_days"]:
        job.reasons.append(Reason("age", f"Posted {job.age_days} days ago (limit {cfg['posting_max_age_days']})", ""))

    # --- flags that never hide ---
    if job.date_approx:
        job.flags.append("date approx")
    if job.jd_source == "none":
        job.flags.append("JD not fetched")
    for c in ext.certs:
        if c.status == "required":
            job.flags.append(f"{c.cert} required")
        elif c.status == "obtainable":
            job.flags.append(f"{c.cert} obtainable after hire")
    if ext.years_preferred and not yr:
        job.flags.append(f"{_fmt(ext.years_preferred.min_years)}+ years preferred")
    if mode.mode == "unknown":
        job.flags.append("work mode unknown")


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _around(text: str, m: re.Match, width: int = 120) -> str:
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return re.sub(r"\s+", " ", text[a:b]).strip()
