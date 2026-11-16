"""Score a shown job 0-100 from config/scoring.yaml. Pure function of the job and the config: no Claude, no web lookups.

Points: role fit 35, level fit 20, skills overlap 15, training signal 15, future-facing 10, location 5.
Company size and market cap are never used.
"""
from __future__ import annotations

import re

from . import config
from .filters import is_senior
from .models import Job


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def company_priority(portal: str, company: str, companies: list[dict] | None = None,
                     aliases: dict | None = None) -> int | None:
    """Priority 1-3 from config/companies.yaml. Matches on the portal name first, then the company."""
    rec = company_record(company, companies, aliases, portal=portal)
    return rec.get("priority") if rec else None


def company_record(company: str, companies: list[dict] | None = None, aliases: dict | None = None,
                   portal: str = "") -> dict | None:
    """The companies.yaml entry for a company name (exact, alias, then prefix match), or None."""
    companies = companies if companies is not None else config.companies_config()
    aliases = aliases if aliases is not None else config.scoring_config().get("company_aliases", {})
    by_norm = {_norm(c["name"]): c for c in companies}
    for raw in (portal, company):
        n = _norm(raw)
        if not n:
            continue
        if n in by_norm:
            return by_norm[n]
        for alias, target in aliases.items():
            if _norm(alias) in n and _norm(target) in by_norm:
                return by_norm[_norm(target)]
        low = (raw or "").lower().strip()
        for c in companies:
            name = c["name"].lower()
            # a legal-name suffix is fine ("Stripe, Inc."); a longer different word is not ("Totally" must not match "Ally")
            if len(_norm(name)) >= 4 and re.match(re.escape(name) + r"(?![a-z0-9])", low):
                return c
    return None


def track_for(title: str, cfg: dict | None = None) -> dict:
    """The track dict {name, letter, points} for a title; first matching track in scoring.yaml wins."""
    cfg = cfg or config.scoring_config()
    for t in cfg["tracks"]:
        if any(re.search(p, title or "", re.I) for p in t["patterns"]):
            return t
    return cfg["track_default"]


def training_signal(text: str, cfg: dict | None = None) -> tuple[int, list[str]]:
    """(points, matched phrases) from the training_signal groups. Each group scores once; the total is capped."""
    cfg = cfg or config.scoring_config()
    total, phrases = 0, []
    for group in cfg["training_signal"]:
        for pat in group["patterns"]:
            m = re.search(pat, text or "", re.I)
            if m:
                total += group["points"]
                phrases.append(re.sub(r"\s+", " ", m.group(0)).strip().lower())
                break
    return min(total, cfg["points"]["training"]), phrases


def tier_for(points: float, cfg: dict | None = None) -> str:
    cfg = cfg or config.scoring_config()
    t = cfg["tiers"]
    if points >= t["apply_now"]:
        return "Apply now"
    if points >= t["strong"]:
        return "Strong"
    if points >= t["stretch"]:
        return "Stretch"
    return ""


def score_job(job: Job, ext_dict: dict, cfg: dict | None = None) -> None:
    cfg = cfg or config.scoring_config()
    pts = cfg["points"]
    title = job.title or ""
    text = (job.jd_text or "") + "\n" + title
    parts: dict[str, float] = {}

    # role fit
    track = track_for(title, cfg)
    parts["role_fit"] = min(track["points"], pts["role_fit"])

    # level fit: the figure that applies to you (route resolved, higher of the readings)
    yr = ext_dict.get("years_required") or None
    if yr:
        years = job.years_effective
        if years is None:
            years = yr.get("effective_years")
        if years is None:
            years = yr.get("years_bachelors") if yr.get("years_bachelors") is not None else yr["min_years"]
        level = next((row["points"] for row in cfg["level_table"] if years <= row["max_years"]), 0)
    elif ext_dict.get("no_experience_needed"):
        level = pts["level_fit"]
    else:
        level = cfg["level_unstated_points"]
    if is_senior(title):
        level -= cfg["senior_penalty"]
    parts["level_fit"] = max(0, min(level, pts["level_fit"]))

    # skills overlap
    matched = [g["name"] for g in cfg["skills"] if any(re.search(t, text, re.I) for t in g["terms"])] if job.jd_text else []
    parts["skills"] = min(len(matched) * cfg["skill_points_each"], pts["skills"])

    # training signal
    train, phrases = training_signal(text, cfg)
    parts["training"] = train

    # future-facing sector, from the static sector_tier in companies.yaml
    rec = company_record(job.company, portal=job.portal)
    tier = (rec or {}).get("sector_tier")
    parts["future_facing"] = min(cfg["future_tier_points"].get(tier, cfg["future_tier_unknown_points"]), pts["future_facing"])

    # location
    if job.metro:
        loc = cfg["location_points"].get(job.metro, cfg["default_location_points"])
    else:
        loc = cfg["location_unknown_points"]
    parts["location"] = min(loc, pts["location"])

    job.score = round(max(0.0, min(100.0, sum(parts.values()))), 1)
    job.score_parts = {k: round(v, 1) for k, v in parts.items()}
    job.score_parts.update({"track": track["name"], "track_letter": track["letter"], "skills_matched": matched,
                            "training_phrases": phrases, "sector_tier": tier or "unknown"})
    job.company_priority = rec.get("priority") if rec else None
