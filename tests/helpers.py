"""Test helper: run the real pipeline (prefilter, JD rules, score, outputs) on canned postings, no network."""
from collections import defaultdict
from datetime import datetime

from board import config, dates, fetch, pipeline
from board.dates import CHICAGO, UTC
from board.fetch import ats
from board.fetch.base import RawJob


def start_for(day, hour: int = 10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=CHICAGO).astimezone(UTC)


def raw_job(company, title, location, url, jd="", day=None, posted=None, pay="", workplace="") -> RawJob:
    """A posting with a confirmed publish date on `day` (a date), or `posted` (a dates.Posted) when given."""
    p = posted if posted is not None else dates.from_iso(f"{day.isoformat()}T08:00:00-06:00", "first_published")
    return RawJob(company, "fake", url.rsplit("/", 1)[-1], title, location, url, p, description=jd, pay=pay, workplace=workplace)


def run_jobs(tmp_path, monkeypatch, jobs: list[RawJob], day, **kw) -> dict:
    """pipeline.run over `jobs`, grouped into one fake board per company. Outputs go under tmp_path."""
    monkeypatch.setattr(config, "jobs_dir", lambda: tmp_path / "jobs")
    by_co: dict[str, list[RawJob]] = defaultdict(list)
    for j in jobs:
        by_co[j.company].append(j)
    monkeypatch.setattr(fetch, "load_boards", lambda only=None: [{"name": co, "ats": "fake", "key": co} for co in by_co])
    monkeypatch.setitem(ats.LIST, "fake", lambda b, start, store: (len(by_co[b["name"]]), list(by_co[b["name"]])))
    kw.setdefault("out_dir", tmp_path / "out")
    kw.setdefault("state_dir", tmp_path / "state")
    kw.setdefault("raw_dir", tmp_path / "raw")
    return pipeline.run(start=start_for(day), history=tmp_path / "no-history.tsv", **kw)
