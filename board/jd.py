"""Fetch a job description and cache it under jobs/<id>_<company>_<title>/JD.md."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import requests

from . import config
from .models import Job

MIN_JD_CHARS = 300
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ClaudeJobBoard/0.1 (personal job search)"
_SEP = "\n---\n"


def slug(text: str, limit: int) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return (s[:limit] or "x").strip("-")


def job_folder(job_id: str, company: str, title: str) -> Path:
    """Reuse an existing folder for this id (company/title spelling can change between scans)."""
    base = config.jobs_dir()
    existing = sorted(base.glob(f"{job_id}_*")) if base.exists() else []
    if existing:
        return existing[0]
    return base / f"{job_id}_{slug(company, 24)}_{slug(title, 48)}"


# ---- HTML -> text ---------------------------------------------------------------------------------

class _TextParser(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextParser()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _jsonld_description(html: str) -> str:
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "JobPosting" and it.get("description"):
                return html_to_text(it["description"])
    return ""


# ---- fetchers ---------------------------------------------------------------------------------------

def fetch_via_career_ops(url: str, timeout: int = 60) -> str:
    """career-ops fetch-jd.mjs: ATS JSON for Greenhouse/Lever/Ashby/Workday/SmartRecruiters/Workable."""
    node = shutil.which("node")
    script = config.career_ops_dir() / "fetch-jd.mjs"
    if not node or not script.exists():
        return ""
    env = dict(os.environ, CAREER_OPS_ROOT=str(config.data_root()))
    try:
        proc = subprocess.run([node, str(script), url], cwd=str(config.career_ops_dir()), env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def fetch_via_html(url: str, timeout: int = 25) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=timeout)
    except requests.RequestException:
        return ""
    if resp.status_code != 200:
        return ""
    html = resp.text
    return _jsonld_description(html) or html_to_text(html)


def fetch_apple(url: str, timeout: int = 30) -> str:
    """jobs.apple.com has no JSON API; the page embeds the posting in a React Router hydration blob."""
    try:
        html = requests.get(url, headers={"User-Agent": UA}, timeout=timeout).text
        m = re.search(r'__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', html, re.S)
        if not m:
            return ""
        # The blob is a JS string literal holding JSON; a JSON string parse undoes the escaping.
        data = json.loads(json.loads('"' + m.group(1) + '"'))
        job = data["loaderData"]["jobDetails"]["jobsData"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return ""
    parts = [job.get("postingTitle") or ""]
    for head, key in (("Summary", "jobSummary"), ("Description", "description"),
                      ("Minimum Qualifications", "minimumQualifications"),
                      ("Preferred Qualifications", "preferredQualifications"),
                      ("Responsibilities", "responsibilities")):
        if job.get(key):
            parts.append(f"{head}:\n{job[key]}")
    return "\n\n".join(parts).strip()


def fetch_oracle_hcm(url: str, timeout: int = 30) -> str:
    """Oracle Recruiting Cloud job detail (JPMorgan, Oracle): recruitingCEJobRequisitionDetails."""
    m = re.match(r"https://([^/]+\.oraclecloud\d*\.com)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/(\d+)", url)
    if not m:
        return ""
    host, site, req = m.groups()
    api = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
           f'?expand=all&onlyData=true&finder=ById;Id="{req}",siteNumber={site}')
    try:
        item = requests.get(api, headers={"User-Agent": UA}, timeout=timeout).json()["items"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return ""
    parts = [item.get("Title") or ""]
    if item.get("WorkplaceType"):
        parts.append(f"Workplace type: {item['WorkplaceType']}")
    for head, key in (("Description", "ExternalDescriptionStr"), ("Responsibilities", "ExternalResponsibilitiesStr"),
                      ("Qualifications", "ExternalQualificationsStr")):
        if item.get(key):
            parts.append(f"{head}:\n{html_to_text(item[key])}")
    return "\n\n".join(parts).strip()


_PAGE_TRIMS = [
    # Google's results page wraps the posting in a job-search sidebar; start at the qualifications.
    (re.compile(r"google\.com/about/careers"), re.compile(r"^(Minimum qualifications|About the job)\b", re.M | re.I)),
]


def _trim_page(url: str, text: str) -> str:
    for host_rx, start_rx in _PAGE_TRIMS:
        if host_rx.search(url):
            m = start_rx.search(text)
            if m:
                return text[m.start():]
    return text


def fetch_jd(url: str) -> tuple[str, str]:
    if "jobs.apple.com" in url:
        text = fetch_apple(url)
        return (text, "apple") if len(text) >= MIN_JD_CHARS else (text, "none")
    if "oraclecloud" in url:
        text = fetch_oracle_hcm(url)
        return (text, "oracle-hcm") if len(text) >= MIN_JD_CHARS else (text, "none")
    text = fetch_via_career_ops(url)
    if len(text) >= MIN_JD_CHARS:
        return text, "fetch-jd"
    text = _trim_page(url, fetch_via_html(url))
    if len(text) >= MIN_JD_CHARS:
        return text, "html"
    return text, "none"


# ---- cache ------------------------------------------------------------------------------------------

def _header(job: Job, source: str) -> str:
    return (
        f"# {job.title} — {job.company}\n\n"
        f"- ID: {job.id}\n- URL: {job.url}\n- Location: {job.location}\n"
        f"- Posted: {job.date_used.isoformat() if job.date_used else ''}"
        f"{' (approx, first seen)' if job.date_approx else ''}\n- Source: {source}\n"
    )


def read_cached(folder: Path) -> tuple[str, str] | None:
    path = folder / "JD.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    source = "cache"
    m = re.search(r"^- Source: (\S+)", raw, re.M)
    if m:
        source = m.group(1)
    body = raw.split(_SEP, 1)[1] if _SEP in raw else raw
    return body.strip(), source


def load_or_fetch(job: Job, refresh: bool = False, fetch: bool = True) -> None:
    """Fill job.jd_text / jd_source / jd_dir, writing JD.md on a fresh fetch."""
    folder = job_folder(job.id, job.company, job.title)
    job.jd_dir = folder
    if not refresh:
        cached = read_cached(folder)
        if cached and len(cached[0]) >= MIN_JD_CHARS:
            job.jd_text, job.jd_source = cached[0], "cache"
            return
    if not fetch:
        job.jd_text, job.jd_source = "", "none"
        return
    text, source = fetch_jd(job.url)
    job.jd_text, job.jd_source = text, source
    if source != "none":
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "JD.md").write_text(_header(job, source) + _SEP + text + "\n", encoding="utf-8")
