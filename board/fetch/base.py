"""HTTP helper, raw-response store and the job record shared by every fetcher.

Fetchers never print responses: list responses are written to files under the raw store, and callers get parsed JSON.
Requests are polite: at most `PER_HOST` at once per host, a User-Agent that says what this is, and backoff on 429.
"""
from __future__ import annotations

import html
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..dates import Posted

UA = {"User-Agent": "personal-job-scout/2.0", "Accept": "application/json"}
PER_HOST = 3


class NoBoard(Exception):
    """The board does not exist (HTTP 4xx) or returned nothing usable."""


class BoardTimeout(Exception):
    """The board used up its time limit. Fetchers keep what they already listed; the board is reported as partial."""


_tl = threading.local()


def set_deadline(seconds: float | None) -> None:
    """Start a board's clock on this thread (None or 0 = no limit)."""
    _tl.deadline = time.monotonic() + seconds if seconds else None
    _tl.partial = False


def credit_wait(seconds: float) -> None:
    """Time spent queued behind a shared concurrency limit does not count against the board's time limit."""
    if getattr(_tl, "deadline", None) is not None and seconds > 0:
        _tl.deadline += seconds


def check_deadline() -> None:
    d = getattr(_tl, "deadline", None)
    if d is not None and time.monotonic() > d:
        _tl.partial = True
        raise BoardTimeout("board time limit reached")


def was_partial() -> bool:
    return bool(getattr(_tl, "partial", False))


def polite_sleep(seconds: float) -> None:
    check_deadline()
    time.sleep(seconds)


@dataclass
class RawJob:
    company: str
    ats: str
    ext_id: str
    title: str
    location: str
    url: str
    posted: Posted
    department: str = ""
    workplace: str = ""            # hint from the API (hybrid / remote / on-site), "" when absent
    pay: str = ""
    description: str = ""          # empty until fetch_detail for ATSs whose list has no description
    detail: dict = field(default_factory=dict)   # ATS-specific ids needed by fetch_detail
    flags: list[str] = field(default_factory=list)


class RawStore:
    """Writes raw list responses to files so nothing large is printed or kept in memory."""

    def __init__(self, directory: Path | None):
        self.dir = directory
        self._n = 0
        self._lock = threading.Lock()

    def save(self, tag: str, body: bytes) -> None:
        if self.dir is None:
            return
        with self._lock:
            self._n += 1
            n = self._n
        self.dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", tag)[:80]
        (self.dir / f"{n:05d}_{safe}.json").write_bytes(body)


_limits: dict[str, threading.Semaphore] = {}
_guard = threading.Lock()
RATE_LIMIT_RETRIES = 3


class RateLimited(Exception):
    """HTTP 429 persisted through every backoff retry."""


def _limit_key(host: str) -> tuple[str, int]:
    """All *.myworkdayjobs.com hosts share one limit (Workday throttles per client, not per tenant host)."""
    if host.endswith(".myworkdayjobs.com"):
        return "workday", int(config.filters_config().get("fetch", {}).get("workday_concurrency", 4))
    return host, PER_HOST


def _sem(host: str) -> threading.Semaphore:
    key, n = _limit_key(host)
    with _guard:
        if key not in _limits:
            _limits[key] = threading.Semaphore(n)
        return _limits[key]


def _backoff(attempt: int, retry_after: str | None) -> float:
    """2s, 4s, 8s (+ up to 1s of jitter), or the server's Retry-After when it asks for longer."""
    try:
        asked = float(retry_after) if retry_after else 0.0
    except ValueError:
        asked = 0.0
    return max(asked, 2.0 * 2 ** (attempt - 1)) + random.random()


def http_json(url: str, data=None, timeout: int = 30, retries: int = 1, headers: dict | None = None,
              store: RawStore | None = None, tag: str = "", per_host: int | None = None):
    """GET (or POST when `data` is given) and parse JSON. 4xx raises NoBoard. Raw body goes to `store`, never stdout.

    HTTP 429 is retried up to RATE_LIMIT_RETRIES times with exponential backoff (Retry-After honoured) and does not use up the
    ordinary retries; if it persists, RateLimited is raised.
    """
    host = urllib.parse.urlsplit(url).netloc
    last: Exception | None = None
    attempts, n, limited = retries + 1, 0, 0
    while n < attempts:
        n += 1
        check_deadline()
        try:
            h = dict(UA)
            body = None
            if data is not None:
                body = json.dumps(data).encode()
                h["Content-Type"] = "application/json"
            if headers:
                h.update(headers)
            queued = time.monotonic()
            with _sem(host):
                credit_wait(time.monotonic() - queued)
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=timeout) as r:
                        raw = r.read()
                finally:
                    if host.endswith(".myworkdayjobs.com"):       # pace the slot so 4 in flight is not 4 back to back
                        time.sleep(float(config.filters_config().get("fetch", {}).get("workday_delay_seconds", 0)))
            if store and tag:
                store.save(tag, raw)
            return json.loads(raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                limited += 1
                if limited > RATE_LIMIT_RETRIES:
                    raise RateLimited(f"HTTP 429 after {RATE_LIMIT_RETRIES} retries") from e
                polite_sleep(_backoff(limited, e.headers.get("Retry-After") if e.headers else None))
                n -= 1                                   # a rate-limit wait does not use up an ordinary retry
                continue
            if e.code in (400, 401, 403, 404, 405, 410, 422):
                raise NoBoard(f"HTTP {e.code}")
            last = e
        except Exception as e:          # noqa: BLE001 - network and parse errors all retry the same way
            last = e
        time.sleep(1.5)
    raise last if last else RuntimeError("request failed")


def to_text(s) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d|/div|/ul|/ol)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


_SAL = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])\s*(?:-|–|—|to|and|&mdash;)\s*\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])")


def find_salary(text: str) -> str:
    m = _SAL.search(text or "")
    return f"${m.group(1)}-${m.group(2)}" if m else ""
