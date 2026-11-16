"""Posting dates: one place that turns what each ATS reports into a UTC instant plus how far to trust it.

All dates are stored as aware UTC. The window is measured from the run start in America/Chicago:
- `time` precision (a real timestamp): old when more than `days` x 24h before the run start, so exactly 4 days is kept
  and 4 days + 1 hour is dropped.
- `day` precision (a calendar date, or Workday's "Posted N Days Ago"): old when the Chicago calendar date is more than
  `days` days before the run's Chicago date.
A date is "confirmed" only when the source gave a real publish date. `updated_at`, first-seen and missing dates are
never confirmed, so they can never drop a role by age; they send it to "Date unconfirmed" with the reason in `flag`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
UTC = timezone.utc

IN, OLD, UNCONFIRMED = "in", "old", "unconfirmed"


@dataclass(frozen=True)
class Posted:
    when: datetime | None          # aware UTC; None when the source gave nothing usable
    precision: str = "none"        # time | day | none
    source: str = ""               # API field it came from, e.g. "first_published"
    flag: str = ""                 # why it is not confirmed ("" when it is)

    @property
    def confirmed(self) -> bool:
        return self.when is not None and not self.flag

    def label(self) -> str:
        """Short text for the board: the date, or the flag."""
        if self.confirmed:
            return self.when.astimezone(CHICAGO).date().isoformat()
        return self.flag or "no date field"


def run_start(now: datetime | None = None) -> datetime:
    """The run's start as aware UTC. Everything is compared against this one instant."""
    now = now or datetime.now(UTC)
    return now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)


def chicago_date(moment: datetime) -> date:
    return moment.astimezone(CHICAGO).date()


def _day(d: date) -> datetime:
    """Chicago midnight of a calendar date, as UTC."""
    return datetime.combine(d, time.min, tzinfo=CHICAGO).astimezone(UTC)


def unconfirmed(source: str, reason: str) -> Posted:
    return Posted(None, "none", source, reason)


def from_iso(value, source: str) -> Posted:
    """ISO timestamp or date. A bare date (no time part) has day precision; a naive timestamp is read as UTC."""
    s = str(value or "").strip()
    if not s:
        return unconfirmed(source, "no date field")
    try:
        if len(s) == 10 and s[4] == "-":
            return Posted(_day(date.fromisoformat(s)), "day", source)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return unconfirmed(source, f"unreadable {source}")
    return Posted((d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC), "time", source)


def from_epoch(value, source: str, millis: bool = False) -> Posted:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return unconfirmed(source, "no date field")
    if n <= 0:
        return unconfirmed(source, "no date field")
    return Posted(datetime.fromtimestamp(n / 1000 if millis else n, UTC), "time", source)


_US_DATE = re.compile(r"[A-Za-z]+\s+\d{1,2},\s*\d{4}")


def from_us_date(value, source: str) -> Posted:
    """Amazon style "November 11, 2026"."""
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if not _US_DATE.fullmatch(s):
        return unconfirmed(source, "no date field" if not s else f"unreadable {source}")
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return Posted(_day(datetime.strptime(s, fmt).date()), "day", source)
        except ValueError:
            continue
    return unconfirmed(source, f"unreadable {source}")


def from_workday(text, start: datetime, source: str = "postedOn") -> Posted:
    """Workday "Posted Today" / "Posted Yesterday" / "Posted 3 Days Ago" / "Posted 30+ Days Ago".

    "30+" is a lower bound: the posting is at least 30 days old, which is confirmed old for any window under 30.
    """
    s = (text or "").strip().lower()
    if not s:
        return unconfirmed(source, "no date field")
    if "today" in s:
        n = 0
    elif "yesterday" in s:
        n = 1
    else:
        m = re.search(r"(\d+)\+?\s*days?", s)
        if not m:
            return unconfirmed(source, f"unreadable {source}")
        n = int(m.group(1))
    return Posted(_day(chicago_date(start) - timedelta(days=n)), "day", source)


def first_seen(value, source: str = "first_seen") -> Posted:
    """A scan's first-seen date: approximate, never confirmed. Used only when the source has no publish date."""
    p = from_iso(value, source)
    if p.when is None:
        return unconfirmed(source, "no date field")
    return Posted(p.when, p.precision, source, f"first seen {chicago_date(p.when).isoformat()}")


def verdict(posted: Posted, start: datetime, days: int) -> str:
    """IN, OLD or UNCONFIRMED. Never OLD unless the date is confirmed."""
    if not posted.confirmed:
        return UNCONFIRMED
    if posted.precision == "day":
        age = (chicago_date(start) - chicago_date(posted.when)).days
        return OLD if age > days else IN
    return OLD if start - posted.when > timedelta(days=days) else IN
