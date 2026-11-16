"""The negative-filter funnel, steps 1-5 (title and location only, so they are free). Steps 6-7 live in pipeline.py.

Nothing here searches for words we thought of. A posting is dropped only for a stated reason; everything else moves on.
Each drop records the step and the reason, so nothing disappears without a trace.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from . import config, ingest
from .filters import classify_location
from .models import HistoryRow

STAGES = ["pulled", "after_date", "after_location", "after_titles", "after_finance", "after_years", "after_fit"]
STEP_NAMES = {2: "date", 3: "location", 4: "title", 5: "finance relevance", 6: "experience / degree / license", 7: "fit"}
_IGNORED_STATUS = ("cooldown", "skipped_invalid_url", "skipped_blocked_host", "skipped_expired")


@dataclass
class Posting:
    row: HistoryRow
    key: str
    pid: str
    company: str
    used: date | None = None
    approx: bool = False
    flags: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.row.title

    @property
    def location(self) -> str:
        return self.row.location


@dataclass
class Drop:
    posting: Posting
    step: int
    rule: str
    reason: str
    evidence: str = ""

    @property
    def step_name(self) -> str:
        return STEP_NAMES[self.step]


def funnel_config() -> dict:
    return config.load_yaml("funnel.yaml")


# ---------------------------------------------------------------------------------------------------------------------
# company label
# ---------------------------------------------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


@lru_cache(maxsize=1)
def _company_names() -> list[tuple[str, str]]:
    names = [(c["name"], _norm(c["name"])) for c in config.companies_config()]
    return sorted(names, key=lambda x: -len(x[1]))


def canonical_company(portal: str, company: str = "") -> str:
    """career-ops entry names like 'Amazon (financial analyst)' collapse to the company name in companies.yaml."""
    for raw in (portal, company):
        n = _norm(raw)
        for name, nn in _company_names():
            if n and nn and n.startswith(nn):
                return name
    return portal or company or "?"


# ---------------------------------------------------------------------------------------------------------------------
# step 1: the pool
# ---------------------------------------------------------------------------------------------------------------------

def build_pool(rows: list[HistoryRow], today: date, window: int) -> list[Posting]:
    """Every distinct posting the scan recorded recently (first seen, or posted, inside the window), any status."""
    cutoff = today - timedelta(days=window)
    best: dict[str, HistoryRow] = {}
    for r in rows:
        if r.status.startswith(_IGNORED_STATUS):
            continue
        k = ingest.row_key(r)
        cur = best.get(k)
        if cur is None or (r.first_seen, r.status == "added") >= (cur.first_seen, cur.status == "added"):
            best[k] = r
    pool: list[Posting] = []
    for r in best.values():
        seen, posted = ingest.parse_date(r.first_seen), ingest.parse_date(r.posted_at)
        if (seen and seen >= cutoff) or (posted and posted >= cutoff):
            pool.append(Posting(row=r, key=ingest.row_key(r), pid=ingest.job_id(r),
                                company=canonical_company(r.portal, r.company)))
    return pool


# ---------------------------------------------------------------------------------------------------------------------
# step 2: date
# ---------------------------------------------------------------------------------------------------------------------

def step_date(p: Posting, today: date, window: int) -> Drop | None:
    posted = ingest.parse_date(p.row.posted_at)
    if posted:
        p.used, p.approx = posted, False
        age = (today - posted).days
        if age > window:
            return Drop(p, 2, "date", f"Posted {age} days ago (window is {window})")
        return None
    seen = ingest.parse_date(p.row.first_seen)
    p.used, p.approx = seen, True
    if seen is None or (today - seen).days > window:
        return Drop(p, 2, "date", f"No posting date and first seen more than {window} days ago")
    p.flags.append("date approx")
    return None


# ---------------------------------------------------------------------------------------------------------------------
# step 3: location
# ---------------------------------------------------------------------------------------------------------------------

_REMOTE_LOC = re.compile(r"^\s*(?:us[- ]|usa[- ]|united states[ ,-]+|fully )?remote\b", re.I)


def step_location(p: Posting, fcfg: dict | None = None) -> Drop | None:
    status, _metro, _all = classify_location(p.location, fcfg)
    if status == "outside":
        return Drop(p, 3, "metro", "Outside the 8 metros", p.location)
    if status == "unknown":
        if _REMOTE_LOC.search(p.location or ""):
            return Drop(p, 3, "remote", "Fully remote", p.location)
        p.flags.append("location unknown")
    return None


# ---------------------------------------------------------------------------------------------------------------------
# step 4: negative titles
# ---------------------------------------------------------------------------------------------------------------------

class NegativeTitles:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or config.load_yaml("negative_titles.yaml")
        edge = r"(?<![A-Za-z0-9])(?:{})(?![A-Za-z0-9])"

        def alt(items: list[str], suffix: str = "") -> str:
            return "|".join(re.escape(w).replace(r"\ ", r"\s+") + suffix for w in items)

        self.words = re.compile(edge.format(alt(cfg["words"], r"\.?")), re.I)
        self.phrases = re.compile(edge.format(alt(cfg["phrases"])), re.I)
        self.exceptions = [re.compile(edge.format(alt([e])), re.I) for e in cfg.get("exceptions", [])]
        self.roman = re.compile(edge.format("|".join(cfg["roman_levels"])))        # case-sensitive
        self.sector_words = {w.lower() for w in cfg.get("sector_words", [])}
        prot = cfg.get("protected_titles") or []
        self.protected = re.compile("|".join(f"(?:{p})" for p in prot), re.I) if prot else None
        ctx = alt(cfg["associate_contexts"])
        self.associate = re.compile(rf"(?<![A-Za-z0-9])(?:{ctx})\s+associates?(?![A-Za-z0-9])|"
                                    rf"(?<![A-Za-z0-9])associates?,?\s+(?:{ctx})(?![A-Za-z0-9])", re.I)

    def hit(self, title: str) -> str | None:
        t = title or ""
        for ex in self.exceptions:
            t = ex.sub(" ", t)
        protected = bool(self.protected and self.protected.search(t))
        for rx in (self.phrases, self.words, self.associate):
            for m in rx.finditer(t):
                word = m.group(0).strip(" .")
                if protected and word.lower() in self.sector_words:
                    continue            # "Equity Research Associate, Software": the word names a sector, not the job
                return word
        m = self.roman.search(t)
        return m.group(0) if m else None


def step_title(p: Posting, neg: NegativeTitles) -> Drop | None:
    h = neg.hit(p.title)
    return Drop(p, 4, "title", f"Title contains '{h}'", p.title) if h else None


# ---------------------------------------------------------------------------------------------------------------------
# step 5: finance relevance (generous)
# ---------------------------------------------------------------------------------------------------------------------

class FinanceRelevance:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or funnel_config()
        self.keep = re.compile("|".join(f"(?:{p})" for p in cfg["keep_patterns"]), re.I)
        self.accounting = re.compile("|".join(f"(?:{p})" for p in cfg["accounting_patterns"]), re.I)
        pats = cfg.get("finance_department_patterns") or []
        self.dept = re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None

    @staticmethod
    def department_of(p: Posting) -> str:
        """The ATS department, where the source puts it in the URL (Apple: ?team=CODE)."""
        m = re.search(r"[?&]team=([A-Za-z0-9_-]+)", p.row.url or "")
        return m.group(1) if m else ""

    def judge(self, p: Posting) -> str:
        """'accounting' | 'keep' | 'unknown' (needs Claude)."""
        dept = self.department_of(p)
        text = f"{p.title} {dept}"
        if self.accounting.search(text):
            return "accounting"
        if self.keep.search(text) or (self.dept and dept and self.dept.search(dept)):
            return "keep"
        return "unknown"


# ---------------------------------------------------------------------------------------------------------------------
# bookkeeping
# ---------------------------------------------------------------------------------------------------------------------

def new_counts() -> dict[str, dict[str, int]]:
    return {}


def bump(counts: dict, company: str, stage: str, n: int = 1) -> None:
    row = counts.setdefault(company, {s: 0 for s in STAGES})
    row[stage] += n


def totals(counts: dict) -> dict[str, int]:
    out = {s: 0 for s in STAGES}
    for row in counts.values():
        for s in STAGES:
            out[s] += row.get(s, 0)
    return out


def sample_drops(drops: list[Drop], step: int, n: int, seed: str) -> list[dict]:
    pool = [d for d in drops if d.step == step]
    rng = random.Random(seed)
    pick = rng.sample(pool, min(n, len(pool)))
    return [{"company": d.posting.company, "title": d.posting.title, "reason": d.reason} for d in pick]
