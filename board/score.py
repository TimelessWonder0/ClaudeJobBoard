"""Score a shown job 0-100 from config/scoring.yaml. Pure function of the job and the config."""
from __future__ import annotations

import re

from . import config
from .models import Job


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def company_priority(portal: str, company: str, companies: list[dict] | None = None,
                     aliases: dict | None = None) -> int | None:
    """Priority 1-3 from config/companies.yaml. Matches on the career-ops portal name first, then the company."""
    companies = companies if companies is not None else config.companies_config()
    aliases = aliases if aliases is not None else config.scoring_config().get("company_aliases", {})
    by_norm = {_norm(c["name"]): c["priority"] for c in companies}
    for raw in (portal, company):
        n = _norm(raw)
        if not n:
            continue
        if n in by_norm:
            return by_norm[n]
        for alias, target in aliases.items():
            if _norm(alias) in n:
                t = by_norm.get(_norm(target))
                if t:
                    return t
        for cn, pr in by_norm.items():
            if len(cn) >= 4 and (n.startswith(cn) or cn in n):
                return pr
    return None


def score_job(job: Job, ext_dict: dict, cfg: dict | None = None) -> None:
    cfg = cfg or config.scoring_config()
    w = cfg["weights"]
    total_w = sum(w.values()) or 100.0
    parts: dict[str, float] = {}

    # title track
    title = job.title or ""
    t = cfg["title_default"]
    for tier in cfg["title_tiers"]:
        if any(re.search(p, title, re.I) for p in tier["patterns"]):
            t = tier["score"]
            break
    if any(re.search(p, title, re.I) for p in cfg.get("title_offtrack_patterns", [])):
        t = min(t, cfg["title_offtrack_score"])
        job.flags.append("title looks off-track")
    if re.search(r"\b(senior|sr\.?|lead|principal|staff)\b", title, re.I):
        t = max(0.0, t - cfg["title_senior_penalty"])
    parts["title_track"] = t

    # years fit
    yr = (ext_dict.get("years_required") or None)
    if yr:
        years = yr.get("years_bachelors") if yr.get("years_bachelors") is not None else yr["min_years"]
        table_score = next(row["score"] for row in cfg["years_table"] if years <= row["max_years"])
        if yr.get("confidence") == "low":
            b = cfg["years_low_confidence_blend"]
            table_score = b * table_score + (1 - b) * cfg["years_unknown"]
        parts["years_fit"] = table_score
    else:
        parts["years_fit"] = 1.0 if ext_dict.get("no_experience_needed") else cfg["years_unknown"]

    # skill overlap
    text = (job.jd_text or "") + "\n" + title
    skills = cfg["skills"]
    total = sum(s["weight"] for s in skills) or 1.0
    hit = 0.0
    matched: list[str] = []
    for s in skills:
        if any(re.search(term, text, re.I) for term in s["terms"]):
            hit += s["weight"]
            matched.append(s["name"])
    parts["skill_overlap"] = min(1.0, (hit / total) / cfg["skill_saturation"]) if job.jd_text else 0.0
    job.score_parts["skills_matched"] = matched

    # metro
    parts["metro"] = cfg["metro_scores"].get(job.metro, cfg["metro_unknown"]) if job.metro else cfg["metro_unknown"]

    # company priority
    pr = job.company_priority
    parts["company_priority"] = cfg["company_priority_scores"].get(pr, cfg["company_unknown"]) if pr else cfg["company_unknown"]

    # freshness
    max_age = config.filters_config()["posting_max_age_days"]
    if job.age_days is None:
        f = cfg["freshness_floor"]
    else:
        frac = min(max(job.age_days, 0), max_age) / max_age if max_age else 0
        f = 1.0 - frac * (1.0 - cfg["freshness_floor"])
    if job.date_approx:
        f *= cfg["freshness_approx_multiplier"]
    parts["freshness"] = f

    points = sum(parts[k] * w[k] for k in w) * (100.0 / total_w)

    adj = cfg["adjustments"]
    notes: list[str] = []
    if job.jd_source == "none":
        points += adj["jd_not_fetched"]; notes.append("JD not fetched")
    if job.metro_status == "unknown":
        points += adj["location_unknown"]; notes.append("location unknown")
    if ext_dict.get("degree_preferred") and ext_dict["degree_preferred"]["level"] in ("masters", "mba", "phd"):
        points += adj["degree_masters_preferred"]
    for c in ext_dict.get("certs", []):
        if c["status"] == "required":
            points += adj["cert_required"]; notes.append(f"{c['cert']} required")
        elif c["status"] == "obtainable":
            points += adj["cert_obtainable"]

    job.score = round(max(0.0, min(100.0, points)), 1)
    job.score_parts.update({k: round(v, 2) for k, v in parts.items()})
    job.score_parts["adjustments"] = notes
