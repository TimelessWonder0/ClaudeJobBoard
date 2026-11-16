"""Support for /apply: prepare a job folder, validate and render the resume and cover letter, update Excel.

  python -m board apply prepare <id>   create applications/<date>_<company>_<title>/ with request.json
  python -m board apply render  <id>   check content.json against the facts in config/candidate.yaml, write two PDFs
  python -m board apply finish  <id>   put the PDF paths in Excel and set Status = Docs Ready

Claude writes the wording (content.json). Everything factual is fixed here: employers, titles, dates, places,
degrees and the skills list come from config/candidate.yaml, and a number, credential or skill in the wording that
the candidate file does not support is rejected before any PDF is made.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

import yaml

from . import config, excel, review
from .jd import slug

FONT_SIZES_RESUME = [10.5, 10.0, 9.5, 9.0]
FONT_SIZES_LETTER = [11.0, 10.5, 10.0]
MAX_BULLET = 280
MAX_SUMMARY = 520
LETTER_WORDS = (200, 420)
_NEVER_RX = {
    "CFA": r"\bCFA\b(?!\s+Research Challenge)", "CPA": r"\bCPA\b", "SIE": r"\bSIE\b", "MBA": r"\bMBA\b",
    "Series license": r"\bSeries\s*(?:7|63|65|66|79|86|87)\b", "security clearance": r"security clearance|TS/SCI",
}


class ApplyError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------
# candidate facts
# ---------------------------------------------------------------------------------------------

def load_candidate(path: Path | None = None) -> dict:
    with open(path or (config.CONFIG_DIR / "candidate.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _corpus(cand: dict) -> str:
    parts: list[str] = []
    for j in cand["jobs"]:
        parts += [j["company"], j["title"], j["dates"], j["location"]] + j["bank"]
    for e in cand["education"]:
        parts += [e["school"], e["degree"], e["dates"], e.get("note", "")]
    for group, items in cand["skills"].items():
        parts += [group] + list(items)
    parts.append(cand["contact"]["location"])
    return "\n".join(parts)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _stem(w: str) -> str:
    w = w.lower()
    for suf in ("ings", "ing", "ions", "ion", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    return w[:-1] if len(w) > 4 and w[-1] == w[-2] else w          # modelling -> model


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[A-Za-z]{3,}", text)}


# ---------------------------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------------------------

def validate_content(content: dict, cand: dict, job: dict) -> list[str]:
    errors: list[str] = []
    resume = content.get("resume") or {}
    letter = content.get("cover_letter") or {}
    corpus = _corpus(cand)
    allowed_nums = _numbers(corpus) | _numbers(f"{job.get('company', '')} {job.get('title', '')}")
    allowed_stems = _stems(corpus)

    summary = str(resume.get("summary") or "").strip()
    if not summary:
        errors.append("resume.summary is empty")
    elif len(summary) > MAX_SUMMARY:
        errors.append(f"resume.summary is {len(summary)} chars; limit {MAX_SUMMARY}")

    exp = resume.get("experience") or {}
    known = {j["id"]: j for j in cand["jobs"]}
    for jid in exp:
        if jid not in known:
            errors.append(f"resume.experience has unknown job id {jid!r}; allowed: {', '.join(known)}")
    if not exp.get("boeing"):
        errors.append("resume.experience.boeing needs bullets (it is the current job)")
    texts = [("summary", summary)]
    for jid, bullets in exp.items():
        if not isinstance(bullets, list):
            errors.append(f"resume.experience.{jid} must be a list of bullet strings")
            continue
        for i, b in enumerate(bullets, 1):
            b = str(b).strip()
            if len(b) > MAX_BULLET:
                errors.append(f"resume.experience.{jid}[{i}] is {len(b)} chars; limit {MAX_BULLET}")
            texts.append((f"experience.{jid}[{i}]", b))

    skills = resume.get("skills") or {}
    if not skills:
        errors.append("resume.skills is empty")
    for group, items in skills.items():
        for item in items if isinstance(items, list) else []:
            words = _stems(str(item))
            missing = sorted(w for w in words if w not in allowed_stems)
            if missing:
                errors.append(f"skill {item!r} is not supported by config/candidate.yaml (unknown: {', '.join(missing)})")
        texts.append((f"skills.{group}", ", ".join(map(str, items)) if isinstance(items, list) else str(items)))

    paragraphs = [str(p).strip() for p in (letter.get("paragraphs") or [])]
    if not 3 <= len(paragraphs) <= 4:
        errors.append(f"cover_letter.paragraphs needs 3 or 4 paragraphs, got {len(paragraphs)}")
    words = sum(len(p.split()) for p in paragraphs)
    if paragraphs and not LETTER_WORDS[0] <= words <= LETTER_WORDS[1]:
        errors.append(f"cover letter body is {words} words; aim for {LETTER_WORDS[0]}-{LETTER_WORDS[1]} so it fits one page")
    for i, p in enumerate(paragraphs, 1):
        texts.append((f"cover_letter[{i}]", p))
    if paragraphs and job.get("company") and job["company"].split()[0].lower() not in " ".join(paragraphs).lower():
        errors.append(f"cover letter never mentions {job['company']}")

    for where, text in texts:
        bad_nums = sorted(n for n in _numbers(text) if n not in allowed_nums)
        if bad_nums:
            errors.append(f"{where}: number(s) {', '.join(bad_nums)} are not in config/candidate.yaml")
        for label, rx in _NEVER_RX.items():
            if re.search(rx, text):
                errors.append(f"{where}: claims {label}, which the candidate does not hold")
        for sent in re.split(r"(?<=[.!?])\s+", text):
            if re.search(r"\bmaster(?:'?s)?\b|\bM\.?S\.?\b", sent, re.I) and re.search(
                    r"\b(completed|earned|holds?|received|graduated|obtained)\b", sent, re.I):
                errors.append(f"{where}: the Master's is in progress; do not write it as completed")
        if re.search(r"\[[^\]]*\]|TBD|lorem|\bXX+\b", text, re.I):
            errors.append(f"{where}: contains a placeholder")
    return errors


# ---------------------------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------------------------

_CSS = """
@page {{ size: Letter; margin: {margin}; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Times New Roman", Times, serif; font-size: {fs}pt; line-height: 1.22; color: #000; margin: 0; }}
h1 {{ font-size: 20pt; text-align: center; margin: 0 0 2pt 0; letter-spacing: 0.5pt; }}
.contact {{ text-align: center; margin-bottom: 4pt; }}
h2 {{ font-size: 11pt; text-transform: uppercase; border-bottom: 1px solid #000; margin: 9pt 0 4pt 0; padding-bottom: 1pt; }}
.row {{ display: flex; justify-content: space-between; font-weight: bold; margin-top: 5pt; }}
.sub {{ display: flex; justify-content: space-between; font-style: italic; }}
ul {{ margin: 2pt 0 0 15pt; padding: 0; }} li {{ margin: 0 0 1.5pt 0; }}
p {{ margin: 0 0 {pgap}pt 0; text-align: left; }}
.block {{ margin-bottom: 10pt; }}
"""


def _e(s) -> str:
    return html.escape(str(s), quote=False)


def _contact_line(c: dict) -> str:
    return _e(" | ".join([c["location"], c["phone"], c["linkedin"], c["email"]]))


def resume_html(content: dict, cand: dict, fs: float) -> str:
    c = cand["contact"]
    resume = content["resume"]
    out = [f"<html><head><meta charset='utf-8'><title>{_e(c['name'])} Resume</title>"
           f"<style>{_CSS.format(margin='0.5in 0.6in', fs=fs, pgap=4)}</style></head><body>",
           f"<h1>{_e(c['name'].upper())}</h1><div class='contact'>{_contact_line(c)}</div>",
           f"<h2>Professional Summary</h2><p>{_e(resume['summary'])}</p>", "<h2>Professional Experience</h2>"]
    for job in cand["jobs"]:
        bullets = (resume.get("experience") or {}).get(job["id"])
        if not bullets:
            continue
        out.append(f"<div class='row'><span>{_e(job['company'])}</span><span>{_e(job['dates'])}</span></div>")
        out.append(f"<div class='sub'><span>{_e(job['title'])}</span><span>{_e(job['location'])}</span></div><ul>")
        out += [f"<li>{_e(b)}</li>" for b in bullets]
        out.append("</ul>")
    out.append("<h2>Education</h2>")
    for e in cand["education"]:
        out.append(f"<div class='row'><span>{_e(e['school'])}</span><span>{_e(e['dates'])}</span></div>")
        right = _e(e["note"]) if e.get("note", "").startswith("GPA") else ""
        out.append(f"<div class='sub'><span>{_e(e['degree'])}</span><span>{right}</span></div>")
        if e.get("note") and not right:
            out.append(f"<div class='sub'><span>{_e(e['note'])}</span></div>")
    out.append("<h2>Skills</h2>")
    for group, items in resume["skills"].items():
        out.append(f"<p><b>{_e(group)}:</b> {_e(', '.join(items))}</p>")
    out.append("</body></html>")
    return "".join(out)


def letter_html(content: dict, cand: dict, job: dict, day: date, fs: float) -> str:
    c = cand["contact"]
    letter = content["cover_letter"]
    when = f"{day.strftime('%B')} {day.day}, {day.year}"
    body = "".join(f"<p>{_e(p)}</p>" for p in letter["paragraphs"])
    return (f"<html><head><meta charset='utf-8'><title>{_e(c['name'])} Cover Letter</title>"
            f"<style>{_CSS.format(margin='0.9in 1in', fs=fs, pgap=9)}</style></head><body>"
            f"<div class='block'><b>{_e(c['name'])}</b><br>{_e(c['location'])}<br>{_e(c['phone'])}<br>"
            f"{_e(c['email'])}<br>{_e(c['linkedin'])}</div>"
            f"<div class='block'>{_e(when)}</div>"
            f"<div class='block'>Hiring Team<br>{_e(job['company'])}<br><b>Re: {_e(job['title'])}</b></div>"
            f"<p>{_e(letter.get('salutation') or 'Dear Hiring Team,')}</p>{body}"
            f"<p>{_e(letter.get('closing') or 'Sincerely,')}<br><br>{_e(c['name'])}</p></body></html>")


# ---------------------------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------------------------

def _page_count(pdf: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf)).pages)


def pdf_text(pdf: Path) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)


def html_to_pdf(html_text: str, out_pdf: Path) -> None:
    node = shutil.which("node")
    script = Path(__file__).with_name("render_pdf.mjs")
    if not node:
        raise ApplyError("node is not on PATH; it is needed for the PDF renderer")
    if not (config.career_ops_dir() / "node_modules" / "playwright").exists():
        raise ApplyError("career-ops/node_modules/playwright is missing; run npm install in career-ops")
    fd, name = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(html_text, encoding="utf-8")
        proc = subprocess.run([node, str(script), str(config.career_ops_dir()), str(tmp), str(out_pdf)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        if proc.returncode != 0 or not out_pdf.exists():
            raise ApplyError(f"PDF render failed: {proc.stderr.strip()[:400]}")
    finally:
        tmp.unlink(missing_ok=True)


def render_one_page(build, sizes: list[float], out_pdf: Path) -> float:
    """Render with the largest font that fits on one page; raise if even the smallest does not."""
    for fs in sizes:
        html_to_pdf(build(fs), out_pdf)
        if _page_count(out_pdf) == 1:
            return fs
    out_pdf.unlink(missing_ok=True)
    raise ApplyError(f"{out_pdf.name} does not fit on one page even at {sizes[-1]}pt; shorten the content")


# ---------------------------------------------------------------------------------------------
# job lookup, keywords
# ---------------------------------------------------------------------------------------------

def find_job(num: int, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.output_dir()
    master = excel.read_master(out_dir / "JobBoard_Master.xlsx")
    row = master.board.get(num)
    if row is None:
        raise ApplyError(f"ID {num} is not on the Board in {out_dir / 'JobBoard_Master.xlsx'}")
    key = str(row.get("Key") or "")
    jd = review._jd_for(key)
    return {"id": num, "key": key, "company": row.get("Company"), "title": row.get("Title"),
            "url": row.get("Title__link"), "location": row.get("Location / Metro"), "status": row.get("Status") or "New",
            "jd_path": str(jd) if jd else "", "flags": row.get("Flags") or ""}


def keyword_guidance(jd_text: str, cand: dict) -> dict:
    """JD skill terms split into ones the candidate file supports (mirror them) and ones it does not."""
    corpus = _corpus(cand)
    mirror, unsupported = [], []
    for group in config.scoring_config()["keyword_groups"]:
        mir: set[str] = set()
        uns: set[str] = set()
        for term in group["terms"]:
            for m in re.finditer(term, jd_text, re.I):
                (mir if re.search(term, corpus, re.I) else uns).add(m.group(0).lower())
        if mir:
            mirror.append({"group": group["name"], "jd_words": sorted(mir)})
        if uns:
            unsupported.append({"group": group["name"], "jd_words": sorted(uns)})
    return {"mirror": mirror, "do_not_claim": unsupported}


def _folder_for(job: dict, day: date) -> Path:
    existing = excel.application_folder(job["id"])
    if existing:
        return existing
    return config.applications_dir() / f"{day.isoformat()}_{slug(job['company'], 24)}_{slug(job['title'], 48)}"


# ---------------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------------

def prepare(num: int, day: date | None = None, out_dir: Path | None = None) -> dict:
    day = day or date.today()
    job = find_job(num, out_dir)
    if not job["jd_path"]:
        raise ApplyError(f"no JD.md on file for ID {num}; run python -m board run --refresh to fetch it")
    cand = load_candidate()
    folder = _folder_for(job, day)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "job.json").write_text(json.dumps({"id": num, "key": job["key"], "company": job["company"],
                                                 "title": job["title"], "url": job["url"], "created": day.isoformat()},
                                                indent=2), encoding="utf-8")
    jd_text = review._jd_body(Path(job["jd_path"]))
    facts = review._read_json(Path(job["jd_path"]).parent / "facts.json", {})
    inputs = config.ROOT / "inputs"
    request = {
        "job": job, "folder": str(folder), "content_file": str(folder / "content.json"),
        "gap_check_file": str(folder / "GapCheck.md"),
        "sources": {
            "jd": job["jd_path"], "candidate_facts": str(config.CONFIG_DIR / "candidate.yaml"),
            "resume_pdf": str(next(inputs.glob("*Resume*.pdf"), "")), "linkedin_pdf": str(next(inputs.glob("Profile*.pdf"), "")),
            "harvard_guide": str(next(inputs.glob("*arvard*.pdf"), "")), "cv_markdown": str(config.data_root() / "cv.md"),
        },
        "jd_extraction": {k: (facts.get("extraction") or {}).get(k) for k in
                          ("years_required", "years_preferred", "degree_required", "degree_preferred", "certs", "work_mode")},
        "keywords": keyword_guidance(jd_text, cand),
        "limits": {"summary_chars": MAX_SUMMARY, "bullet_chars": MAX_BULLET, "letter_words": list(LETTER_WORDS),
                   "job_ids": [j["id"] for j in cand["jobs"]]},
        "content_schema": {
            "resume": {"summary": "2-3 sentences", "experience": {"boeing": ["4-6 bullets"], "fintax": ["1-2 bullets"], "forrest": ["1-2 bullets"]},
                       "skills": {"FP&A": ["..."], "Tools": ["..."], "Business": ["..."]}},
            "cover_letter": {"salutation": "Dear Hiring Team,", "paragraphs": ["3-4 paragraphs"], "closing": "Sincerely,"}},
    }
    (folder / "request.json").write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
    return request


def _load_job_folder(num: int) -> tuple[Path, dict]:
    folder = excel.application_folder(num)
    if folder is None:
        raise ApplyError(f"no application folder for ID {num}; run python -m board apply prepare {num} first")
    return folder, json.loads((folder / "job.json").read_text(encoding="utf-8"))


def render(num: int, day: date | None = None) -> dict:
    day = day or date.today()
    folder, job = _load_job_folder(num)
    content_path = folder / "content.json"
    if not content_path.exists():
        raise ApplyError(f"{content_path} not found; write it first (see request.json content_schema)")
    try:
        content = json.loads(content_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ApplyError(f"content.json is not valid JSON: {exc}") from exc
    cand = load_candidate()
    errors = validate_content(content, cand, job)
    if errors:
        raise ApplyError("content.json rejected, no PDFs written:\n  - " + "\n  - ".join(errors))

    company = slug(job["company"], 24).replace("-", "_")
    resume_pdf = folder / f"Shreyas_Kukanur_Resume_{company}.pdf"
    letter_pdf = folder / f"Shreyas_Kukanur_Cover_Letter_{company}.pdf"
    fs_r = render_one_page(lambda fs: resume_html(content, cand, fs), FONT_SIZES_RESUME, resume_pdf)
    fs_l = render_one_page(lambda fs: letter_html(content, cand, job, day, fs), FONT_SIZES_LETTER, letter_pdf)

    text = pdf_text(resume_pdf)
    ats = {
        "pages": _page_count(resume_pdf), "font_pt": fs_r,
        "text_extractable": all(s in text for s in ("PROFESSIONAL SUMMARY", "PROFESSIONAL EXPERIENCE", "EDUCATION", "SKILLS")),
        "name_and_location_present": cand["contact"]["name"].upper() in text and cand["contact"]["location"] in text,
        "garbled_text": "(cid:" in text,
    }
    jd_text = review._jd_body(Path(review._jd_for(job["key"]) or ""))
    resume_text = text + "\n" + pdf_text(letter_pdf)
    covered, missing = [], []
    for group in config.scoring_config()["keyword_groups"]:
        in_jd = any(re.search(t, jd_text, re.I) for t in group["terms"])
        if not in_jd:
            continue
        (covered if any(re.search(t, resume_text, re.I) for t in group["terms"]) else missing).append(group["name"])
    return {"resume": str(resume_pdf), "cover_letter": str(letter_pdf), "letter_font_pt": fs_l, "ats": ats,
            "jd_skill_groups_covered": covered, "jd_skill_groups_not_in_documents": missing, "folder": str(folder)}


def finish(num: int, now: datetime | None = None, out_dir: Path | None = None) -> dict:
    now = now or datetime.now()
    folder, _job = _load_job_folder(num)
    resume = next(iter(sorted(folder.glob("*Resume*.pdf"))), None)
    letter = next(iter(sorted(folder.glob("*Cover_Letter*.pdf"))), None)
    if not resume or not letter:
        raise ApplyError(f"both PDFs must exist in {folder}; run python -m board apply render {num}")
    updates: dict = {"Resume": ("Resume", str(resume)), "Cover Letter": ("Cover Letter", str(letter))}
    current = (excel.read_master((out_dir or config.output_dir()) / "JobBoard_Master.xlsx").board.get(num) or {}).get("Status") or "New"
    status_note = ""
    if current in ("New", "Interested"):
        updates["Status"] = "Docs Ready"
    else:
        status_note = f"Status left as {current} (only New or Interested move to Docs Ready)."
    res = excel.patch_board_row(num, updates, now=now, out_dir=out_dir)
    res.update(resume=str(resume), cover_letter=str(letter), status=updates.get("Status", current), status_note=status_note,
               gap_check=str(folder / "GapCheck.md") if (folder / "GapCheck.md").exists() else "")
    return res
