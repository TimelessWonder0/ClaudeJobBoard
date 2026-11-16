"""The 4-day window: measured from run start in America/Chicago, dates stored as UTC, updated_at never a posting date."""
from datetime import datetime, timedelta, timezone

import pytest

from board import dates
from board.dates import IN, OLD, UNCONFIRMED, CHICAGO, UTC

# Run start: 2026-11-15 10:00 in Chicago (CST, UTC-6) = 16:00 UTC
START = datetime(2026, 11, 15, 10, 0, tzinfo=CHICAGO).astimezone(UTC)
DAYS = 4


def ts(delta: timedelta) -> dates.Posted:
    return dates.Posted(START - delta, "time", "first_published")


def test_run_start_is_utc_and_chicago_based():
    assert START.tzinfo == UTC and START.hour == 16
    assert dates.chicago_date(START).isoformat() == "2026-11-15"
    # 23:30 Chicago on the 14th is already the 15th in UTC; the run date follows Chicago
    late = datetime(2026, 11, 14, 23, 30, tzinfo=CHICAGO).astimezone(UTC)
    assert late.date().isoformat() == "2026-11-15" and dates.chicago_date(late).isoformat() == "2026-11-14"


def test_exactly_four_days_is_kept():
    assert dates.verdict(ts(timedelta(days=4)), START, DAYS) == IN


def test_four_days_and_one_hour_is_dropped():
    assert dates.verdict(ts(timedelta(days=4, hours=1)), START, DAYS) == OLD


def test_recent_and_future_timestamps_are_kept():
    assert dates.verdict(ts(timedelta(hours=1)), START, DAYS) == IN
    assert dates.verdict(ts(timedelta(hours=-3)), START, DAYS) == IN


def test_updated_at_is_never_a_posting_date():
    # Greenhouse job with only updated_at: never kept or dropped by age, always "Date unconfirmed"
    p = dates.unconfirmed("updated_at", "updated_at only")
    assert not p.confirmed and p.label() == "updated_at only"
    assert dates.verdict(p, START, DAYS) == UNCONFIRMED


def test_real_publish_date_beats_recent_updated_at():
    # first_published 9 days ago, updated_at yesterday: the real publish date decides, so it is old
    p = dates.from_iso((START - timedelta(days=9)).isoformat(), "first_published")
    assert dates.verdict(p, START, DAYS) == OLD


def test_iso_with_offset_converts_to_utc():
    p = dates.from_iso("2026-11-12T10:00:00-04:00", "first_published")
    assert p.when == datetime(2026, 11, 12, 14, 0, tzinfo=UTC) and p.precision == "time"
    assert dates.from_iso("2026-11-12T10:00:00Z", "publishedAt").when == datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
    assert dates.from_iso("2026-11-12T10:00:00", "x").when.tzinfo == UTC          # naive read as UTC


def test_epoch_sources():
    ms = int(datetime(2026, 11, 12, 12, 0, tzinfo=UTC).timestamp() * 1000)
    assert dates.from_epoch(ms, "createdAt", millis=True).when == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert dates.from_epoch(0, "postedTs").when is None and dates.from_epoch(None, "postedTs").when is None


def test_date_only_values_have_day_precision():
    # Oracle PostedDate 2026-11-11 is 4 calendar days before 2026-11-15 (kept); 2026-11-10 is 5 (dropped)
    assert dates.from_iso("2026-11-11", "PostedDate").precision == "day"
    assert dates.verdict(dates.from_iso("2026-11-11", "PostedDate"), START, DAYS) == IN
    assert dates.verdict(dates.from_iso("2026-11-10", "PostedDate"), START, DAYS) == OLD
    assert dates.verdict(dates.from_us_date("November 11, 2026", "posted_date"), START, DAYS) == IN
    assert dates.verdict(dates.from_us_date("November 10, 2026", "posted_date"), START, DAYS) == OLD


def test_day_precision_uses_the_chicago_calendar_date():
    # 00:30 Chicago on the 15th is 06:30 UTC; a date posted 2026-11-11 is still 4 days old in Chicago
    early = datetime(2026, 11, 15, 0, 30, tzinfo=CHICAGO).astimezone(UTC)
    assert dates.verdict(dates.from_iso("2026-11-11", "PostedDate"), early, DAYS) == IN


@pytest.mark.parametrize("text,days_ago,verdict", [
    ("Posted Today", 0, IN),
    ("Posted Yesterday", 1, IN),
    ("Posted 4 Days Ago", 4, IN),
    ("Posted 5 Days Ago", 5, OLD),
    ("Posted 30+ Days Ago", 30, OLD),
])
def test_workday_posted_text(text, days_ago, verdict):
    p = dates.from_workday(text, START)
    assert p.confirmed and p.precision == "day"
    assert (dates.chicago_date(START) - dates.chicago_date(p.when)).days == days_ago
    assert dates.verdict(p, START, DAYS) == verdict


def test_workday_unreadable_is_unconfirmed_not_dropped():
    for text in ("", None, "Posted recently"):
        p = dates.from_workday(text, START)
        assert dates.verdict(p, START, DAYS) == UNCONFIRMED


def test_first_seen_is_never_confirmed():
    p = dates.first_seen("2026-11-14")
    assert not p.confirmed and p.flag == "first seen 2026-11-14" and p.label() == "first seen 2026-11-14"
    assert dates.verdict(p, START, DAYS) == UNCONFIRMED
    assert dates.first_seen("").flag == "no date field"
