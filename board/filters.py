"""Hard filters from CLAUDE.md and config/filters.yaml. Each rule appends a Reason (hidden) or a flag (shown with a note).

Hidden: quant roles, senior/managerial titles, titles outside the finance-role allow-list, Master's/MBA/CPA/CFA required,
a required-years minimum above max_required_years, licenses required before hire, a new-grad window that excludes
the candidate's graduation date, outside the metros, fully remote, posted too long ago.
Flagged, never hidden: a years requirement equal to the limit ("stretch"), years not stated, sponsored licenses,
preferred credentials, an unstated new-grad window, anything ambiguous.
"""
from __future__ import annotations

import re
from datetime import date

from . import config
from .extract import CONF_RANK, Extraction
from .models import Job, Reason


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


_JD_LOCATION = re.compile(r"^\s*(?:-\s+)?(?:job\s+|work\s+)?locations?\s*(?::|--|–)\s*(\S.{1,300})$", re.I)


def jd_location(jd_text: str, max_lines: int = 40) -> str:
    """The city list a JD states near its top ("Location: San Jose, California, US | RTP, North Carolina, US"), joined with
    "; ", or "" when there is none. Used when the listing only said "2 Locations" or "United States"."""
    for line in (jd_text or "").splitlines()[:max_lines]:
        m = _JD_LOCATION.match(line)
        if m:
            parts = [p.strip() for p in re.split(r"\s*[|;·]\s*", m.group(1)) if p.strip()]
            text = "; ".join(parts)
            if text and not re.match(r"^\d+ locations?$", text, re.I):
                return text
    return ""


# ----------------------------------------------------------------------------------------------
# titles
# ----------------------------------------------------------------------------------------------

def _title_head(title: str) -> str:
    """The role noun: the title up to the first ' - ', ',' or '('."""
    return re.split(r"\s+[-–—|]\s+|,\s*|\s+\(", title or "", maxsplit=1)[0].strip()


def title_is_finance_role(title: str, cfg: dict) -> bool:
    allowed = _compile(cfg["allowed_title_patterns"])
    head = _title_head(title)
    if any(rx.search(head) for rx in allowed):
        return True
    bare = re.sub(r"\b(?:senior|sr\.?|lead|principal|staff|junior|jr\.?|i{1,3}|iv)\b", "", head, flags=re.I).strip(" ,-")
    if any(re.search(p, bare, re.I) for p in cfg["generic_title_heads"]):
        rest = (title or "")[len(head):]
        if any(rx.search(rest) for rx in allowed):          # "Analyst, FP&A": the rest names the field
            return True
    return analyst_combo(title, cfg)


def analyst_combo(title: str, cfg: dict) -> bool:
    """A title with analyst / associate (business analyst included) passes only when it also names a finance word
    (analyst_combo.finance_words) and is not an off-track kind of analyst (analyst_combo.exclude)."""
    combo = cfg.get("analyst_combo")
    if not combo:
        return False
    t = title or ""
    return bool(re.search(combo["roles"], t, re.I) and re.search(combo["finance_words"], t, re.I)
                and not re.search(combo["exclude"], t, re.I))


def title_blocked(title: str, cfg: dict | None = None) -> tuple[str, str] | None:
    """Titles that are sales-like, move backward into estimating, or sit in the wrong function. Returns (rule, text)."""
    cfg = cfg or config.filters_config()
    for key, rule, label in (("sales_like_title_patterns", "sales_like", "Sales-like title"),
                             ("moves_backward_title_patterns", "moves_backward", "Moves backward (estimating / proposal pricing / EVM)"),
                             ("wrong_function_title_patterns", "wrong_function", "Wrong function (accounting, tax, audit, AP/AR, payroll, AML, insurance, credit, underwriting, commercial banking)")):
        for rx in _compile(cfg.get(key, [])):
            m = rx.search(title or "")
            if m:
                return rule, f"{label}: '{m.group(0).strip()}'"
    # "client" titles (client onboarding, client service, client reporting ...) are blocked unless the title also names a finance
    # function. "Financial Services" is an industry, not a function, so it does not count as a finance word.
    combo = cfg.get("analyst_combo")
    if combo and re.search(r"\bclient\b", title or "", re.I):
        stripped = re.sub(r"financ\w*\s+services", " ", title or "", flags=re.I)
        if not re.search(combo["finance_words"], stripped, re.I):
            return "client_title", "Client title without a finance word: 'client'"
    return None


_SENIOR = re.compile(r"\b(?:senior|sr)\b\.?", re.I)


def is_senior(title: str) -> bool:
    return bool(_SENIOR.search(title or ""))


def staffing_company(name: str, cfg: dict | None = None) -> str | None:
    cfg = cfg or config.filters_config()
    for agency in cfg.get("staffing_agencies", []):
        if re.search(r"(?<![A-Za-z])" + re.escape(agency) + r"(?![A-Za-z])", name or "", re.I):
            return agency
    return None


def grad_excludes(grad, grad_date: date, ref: date) -> str:
    """Return why a stated graduation window clearly excludes `grad_date`, or '' if it does not (or is unclear)."""
    def mi(d: date) -> int:
        return d.year * 12 + d.month

    def parse(s):
        return date.fromisoformat(s) if s else None

    start, end = parse(grad.get("start")), parse(grad.get("end"))
    kind = grad.get("kind")
    if kind in ("window", "after", "single") and start and mi(start) > mi(grad_date):
        return f"window starts {start:%B %Y}, after the {grad_date:%B %Y} graduation"
    if kind in ("window", "by") and end and mi(end) < mi(grad_date):
        return f"window ends {end:%B %Y}, before the {grad_date:%B %Y} graduation"
    if kind == "class_of" and grad.get("class_year") and grad["class_year"] >= grad_date.year + 2:
        return f"Class of {grad['class_year']} excludes a {grad_date:%B %Y} graduate"
    if kind == "recent" and grad.get("recent_months"):
        months_since = mi(ref) - mi(grad_date)
        if months_since > grad["recent_months"]:
            return f"requires graduating within {grad['recent_months']} months; {months_since} have passed"
    return ""


# ----------------------------------------------------------------------------------------------
# the filters
# ----------------------------------------------------------------------------------------------

def apply_filters(job: Job, ext: Extraction, cfg: dict | None = None, careerops_years: float | None = None,
                  careerops_years_confident: bool = False, skip_title: bool = False, skip_location: bool = False) -> None:
    """skip_title / skip_location: the funnel already judged those in steps 3-5, so only the JD-based rules run."""
    cfg = cfg or config.filters_config()
    min_conf = CONF_RANK[cfg.get("min_confidence_to_hide", "medium")]
    title = job.title or ""
    jd = job.jd_text or ""

    # 1. quant
    for rx in _compile(cfg["quant_title_patterns"]):
        if rx.search(title):
            job.reasons.append(Reason("quant", "Quant role (title)", title))
            break
    else:
        for rx in _compile(cfg["quant_jd_strong_patterns"]):
            m = rx.search(jd)
            if m:
                job.reasons.append(Reason("quant", "Quant role (description)", _around(jd, m)))
                break
        else:
            if any(rx.search(jd) for rx in _compile(cfg["quant_jd_flag_patterns"])):
                job.flags.append("quantitative wording in JD")

    # 2. senior / managerial titles, then the finance-role allow-list (the funnel does this in steps 4-5)
    for rx in ([] if skip_title else _compile(cfg["hide_title_patterns"])):
        m = rx.search(title)
        if m:
            job.reasons.append(Reason("title", f"Senior or managerial title ('{m.group(0).strip()}')", title))
            break
    else:
        m = None if skip_title else re.search(cfg["hide_title_roman_levels"], title)           # case-sensitive: III and above
        if m:
            job.reasons.append(Reason("title", f"Level {m.group(0).strip(' ,(-')} or above", title))
    blocked = None if skip_title else title_blocked(title, cfg)
    if blocked:
        job.reasons.append(Reason(blocked[0], blocked[1], title))
    elif not skip_title and not title_is_finance_role(title, cfg):
        job.reasons.append(Reason("title_offtrack", "Not a finance analyst, FP&A, pricing or research role",
                                  f"title: {title}"))
    for rx in _compile(cfg["flag_title_patterns"]):
        m = rx.search(title)
        if m:
            job.flags.append(f"title: {m.group(0).lower()}")
            break

    # 3. degree and credentials required
    deg = ext.degree_required
    if deg and deg.level in cfg["hide_degrees_required"]:
        label = "MBA" if deg.level == "mba" else deg.level.title()
        if CONF_RANK[deg.confidence] >= min_conf:
            job.reasons.append(Reason("degree_required", f"{label} required", deg.evidence))
        else:
            job.flags.append(f"{deg.level} may be required (low confidence)")
    if ext.degree_preferred and ext.degree_preferred.level in cfg["hide_degrees_required"]:
        job.flags.append(f"{ext.degree_preferred.level} preferred")
    hide_certs = set(cfg["hide_certs_required"])
    hide_before_hire = set(cfg["hide_licenses_before_hire"])
    sponsored, listed_required = [], []
    for c in ext.certs:
        if c.cert in hide_certs:
            if c.status == "required":
                job.reasons.append(Reason("cert_required", f"{c.cert} required", c.evidence))
            elif c.status == "preferred":
                job.flags.append(f"{c.cert} preferred")
            continue
        if c.cert in hide_before_hire and c.status == "required":
            job.reasons.append(Reason("license_required", f"{c.cert} required before hire", c.evidence))
        elif c.status == "obtainable":
            sponsored.append(c.cert)
        elif c.status == "required":
            listed_required.append(c.cert)          # Series 86/87, SIE, 79...: research roles normally sponsor these
        elif c.status == "preferred":
            job.flags.append(f"{c.cert} preferred")
    if sponsored:
        job.flags.append("licenses sponsored: " + ", ".join(sponsored))
    if listed_required:
        job.flags.append("licenses listed as required (JD does not say sponsored): " + ", ".join(listed_required))

    # 4. years of experience required
    yr = ext.years_required
    senior = is_senior(title)
    max_years = cfg["senior_max_required_years"] if senior else cfg["max_required_years"]
    if senior:
        job.flags.append(f"senior title (kept only with {_fmt(max_years)} or fewer years required)")
    if yr:
        eff = yr.effective_years if yr.effective_years is not None else yr.min_years
        conf = yr.confidence
        if careerops_years is not None and careerops_years != eff:
            job.flags.append(f"years differ: board {_fmt(eff)}, career-ops {_fmt(careerops_years)}; used the higher")
            if careerops_years > eff:
                eff = careerops_years
                conf = "high" if careerops_years_confident else "low"
        job.years_effective = eff
        if eff > max_years:
            if CONF_RANK[conf] >= min_conf:
                job.reasons.append(Reason("years_required", f"{_fmt(eff)}+ years required (limit {_fmt(max_years)})", yr.evidence))
            else:
                job.flags.append(f"{_fmt(eff)}+ years may be required (low confidence)")
        elif eff == max_years:
            job.flags.append(f"stretch: {_fmt(eff)} years required")
        if yr.routes and yr.years_bachelors is not None and yr.years_bachelors != yr.min_years:
            job.flags.append(f"degree routes: {_fmt(yr.years_bachelors)} yrs with a Bachelor's, {_fmt(yr.min_years)} with a higher degree")
    else:
        if careerops_years is not None and careerops_years > max_years and careerops_years_confident:
            job.years_effective = careerops_years
            job.flags.append(f"years: career-ops reads {_fmt(careerops_years)}, board found none")
            job.reasons.append(Reason("years_required", f"{_fmt(careerops_years)}+ years required (limit {_fmt(max_years)})",
                                      "read by career-ops jd-years.mjs"))
        elif not ext.no_experience_needed:
            job.flags.append("years not stated")

    # 5. new-grad programs
    newgrad_title = any(rx.search(title) for rx in _compile(cfg["newgrad_title_patterns"]))
    grad = ext.grad_window
    if grad:
        grad_date = date.fromisoformat(str(cfg["graduation_date"]))
        why = grad_excludes(grad.__dict__ if hasattr(grad, "__dict__") else grad, grad_date, job.date_used or date.today())
        if why:
            job.reasons.append(Reason("grad_window", f"New-grad window excludes you: {why}",
                                      grad.evidence if hasattr(grad, "evidence") else grad.get("evidence", "")))
        elif newgrad_title:
            job.flags.append("new-grad window: " + (grad.evidence if hasattr(grad, "evidence") else grad.get("evidence", ""))[:80])
    elif newgrad_title:
        job.flags.append("new-grad window not stated")

    # 6. metros
    status, metro, _all = classify_location(job.location, cfg)
    job.metro, job.metro_status = metro, status
    if skip_location:
        pass
    elif status == "outside":
        job.reasons.append(Reason("metro", "Outside the target metros", job.location))
    elif status == "unknown":
        job.flags.append("location unknown")

    # 7. fully remote
    mode = ext.work_mode
    job.work_mode = mode.mode
    if mode.mode == "remote":
        job.reasons.append(Reason("remote", "Fully remote", mode.evidence))
    elif mode.mode == "unknown":
        job.flags.append("work mode not stated")

    # 9. staffing agencies and contract postings
    agency = staffing_company(job.company, cfg)
    if agency:
        job.reasons.append(Reason("staffing", f"Staffing agency ({agency})", job.company))
    else:
        for rx in _compile(cfg.get("staffing_jd_patterns", [])):
            m = rx.search(jd)
            if m:
                job.reasons.append(Reason("staffing", "Staffing or contract posting", _around(jd, m)))
                break

    # 10. moves backward: a cost / pricing analyst whose JD is about government contracts
    low = title.lower()
    if any(t in low for t in cfg.get("moves_backward_cost_titles", [])):
        for rx in _compile(cfg.get("moves_backward_jd_patterns", [])):
            m = rx.search(jd)
            if m:
                job.reasons.append(Reason("moves_backward", "Cost / pricing analyst on government contracts (moves backward)",
                                          _around(jd, m)))
                break

    # 8. age (rows reaching here are already inside the window; kept for rows built by other callers)
    if job.age_days is not None and job.age_days > cfg["days"]:
        job.reasons.append(Reason("age", f"Posted {job.age_days} days ago (limit {cfg['days']})", ""))

    # --- flags that never hide ---
    if job.date_approx:
        job.flags.append("date approx")
    if job.jd_source == "none":
        job.flags.append("JD not fetched")
    if ext.years_preferred and not yr:
        job.flags.append(f"{_fmt(ext.years_preferred.min_years)}+ years preferred")


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _around(text: str, m: re.Match, width: int = 120) -> str:
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return re.sub(r"\s+", " ", text[a:b]).strip()
