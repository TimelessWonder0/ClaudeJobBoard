"""HTTP helper, raw-response store and the job record shared by every fetcher.

Fetchers never print responses: list responses are written to files under the raw store, and callers get parsed JSON.
Requests are polite: at most `PER_HOST` at once per host, a User-Agent that says what this is, and backoff on 429.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..dates import Posted

UA = {"User-Agent": "personal-job-scout/2.0", "Accept": "application/json"}
PER_HOST = 3


class NoBoard(Exception):
    """The board does not exist (HTTP 4xx) or returned nothing usable."""


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


_host_locks: dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(PER_HOST))
_guard = threading.Lock()


def _sem(host: str) -> threading.Semaphore:
    with _guard:
        return _host_locks[host]


def http_json(url: str, data=None, timeout: int = 30, retries: int = 1, headers: dict | None = None,
              store: RawStore | None = None, tag: str = "", per_host: int | None = None):
    """GET (or POST when `data` is given) and parse JSON. 4xx raises NoBoard. Raw body goes to `store`, never stdout."""
    host = urllib.parse.urlsplit(url).netloc
    last: Exception | None = None
    attempts, n = retries + 1, 0
    while n < attempts:
        n += 1
        try:
            h = dict(UA)
            body = None
            if data is not None:
                body = json.dumps(data).encode()
                h["Content-Type"] = "application/json"
            if headers:
                h.update(headers)
            with _sem(host):
                with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=timeout) as r:
                    raw = r.read()
            if store and tag:
                store.save(tag, raw)
            return json.loads(raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 405, 410, 422):
                raise NoBoard(f"HTTP {e.code}")
            if e.code == 429:
                attempts = max(attempts, n + 1) if n < 5 else attempts
                time.sleep(8 * n)
            last = e
            continue
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
