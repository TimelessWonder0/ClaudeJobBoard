"""Cheap filters that run on list data, before any per-job detail call, so only survivors cost a request.

Order, cheapest first: date (confirmed old only), location, negative titles, finance title allow-list.
Returns None to keep a job or `(step, reason)` to drop it. A role with an unconfirmed date is never dropped here for
its date; an unknown location is kept. Titles dropped by the allow-list that still look finance-ish are reported by the
caller as "title not on list" so they can be overridden.
"""
from __future__ import annotations

import re
from datetime import datetime

from .. import config, dates
from ..filters import classify_location, title_is_finance_role
from ..funnel import NegativeTitles
from .base import RawJob

_REMOTE = re.compile(r"^\s*(?:us[- ]|usa[- ]|united states[ ,-]+|fully )?remote\b", re.I)
_LOOKS_FINANCE = re.compile(r"financ|pricing|fp&a|\bfpa\b|treasur|analyst", re.I)


class Prefilter:
    def __init__(self, start: datetime, days: int | None = None, fcfg: dict | None = None):
        self.start = start
        self.fcfg = fcfg or config.filters_config()
        self.days = int(days if days is not None else self.fcfg["days"])
        self.neg = NegativeTitles()

    def __call__(self, job: RawJob) -> tuple[str, str] | None:
        if dates.verdict(job.posted, self.start, self.days) == dates.OLD:
            return "date", f"Posted {job.posted.label()}, window is {self.days} days"
        status, _metro, _all = classify_location(job.location, self.fcfg)
        if status == "outside":
            return "location", "Outside the target metros"
        if status == "unknown" and _REMOTE.search(job.location or ""):
            return "location", "Fully remote"
        hit = self.neg.hit(job.title)
        if hit:
            return "title", f"Title contains '{hit}'"
        if not title_is_finance_role(job.title, self.fcfg):
            return "title not on list", "title not on list"
        return None

    @staticmethod
    def worth_review(job: RawJob) -> bool:
        """A title the allow-list dropped that contains a finance-ish word: shown in Hidden for override."""
        return bool(_LOOKS_FINANCE.search(job.title))
