"""Shared dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class HistoryRow:
    url: str = ""
    first_seen: str = ""
    portal: str = ""
    title: str = ""
    company: str = ""
    status: str = ""
    location: str = ""
    fingerprint: str = ""
    posted_at: str = ""
    trust_score: str = ""
    trust_flags: str = ""
    normalized_company: str = ""
    requisition_id: str = ""
    language: str = ""
    listing_key: str = ""


@dataclass
class Reason:
    rule: str            # e.g. "years_required", "degree_required", "title", "metro", "remote", "quant", "age"
    text: str            # human-readable reason
    evidence: str = ""   # sentence it came from


@dataclass
class Job:
    id: str
    row: HistoryRow
    company: str
    title: str
    location: str
    url: str
    portal: str
    date_used: date | None
    date_approx: bool
    age_days: int | None
    jd_text: str = ""
    jd_source: str = ""          # fetch-jd | html | cache | none
    jd_dir: Path | None = None
    extraction: dict = field(default_factory=dict)
    metro: str = ""              # key from filters.yaml, "" when none
    metro_status: str = "unknown"  # match | outside | unknown
    work_mode: str = "unknown"
    years_effective: float | None = None     # required years that apply to you after route and career-ops cross-check
    careerops: dict = field(default_factory=dict)   # {"score": 3.8, "report": path, "years": ..., "keyword_pct": ..., "live": ...}
    company_priority: int | None = None
    reasons: list[Reason] = field(default_factory=list)   # hard-filter hits; non-empty means hidden
    flags: list[str] = field(default_factory=list)
    score: float | None = None
    score_parts: dict = field(default_factory=dict)
    date_flag: str = ""          # why the posting date is not confirmed ("updated_at only", "first seen 2026-11-14", ...)
    pay: str = ""
    workplace: str = ""          # API hint: hybrid / remote / on-site, "" when absent

    @property
    def hidden(self) -> bool:
        return bool(self.reasons)
