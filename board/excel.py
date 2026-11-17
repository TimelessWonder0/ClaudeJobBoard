"""Excel job board: a dated snapshot plus one master file you mark statuses in.

output/JobBoard_YYYY-MM-DD.xlsx   snapshot of this run
output/JobBoard_Master.xlsx       the file you edit; read back before every rewrite

User-owned cells that survive a rewrite (matched by the stable numeric ID): Status, Applied Date,
Resume, Cover Letter, Notes, and the Override column on the Hidden sheet. Rows you touched are
never dropped. If a target file is open in Excel (locked), the run saves a timestamped copy
beside it and says so; nothing is lost and nothing is overwritten.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import config

STATUSES = ["New", "Interested", "Docs Ready", "Applied", "Interviewing", "Rejected", "Offer", "Skip"]
KEEP_NEW_DAYS = 14   # an untouched "New" row that fell out of the scan window is dropped after this long

BOARD_COLS = ["ID", "Date Found", "Posted", "Days Old", "Company", "Title", "Track", "Location / Metro",
              "Fit Score (0-100)", "Career-ops score", "Report", "Years Req", "Flags", "Why it fits / Gaps", "Status",
              "Applied Date", "Resume", "Cover Letter", "Notes", "Key"]
HIDDEN_COLS = ["ID", "Step", "Date Found", "Posted", "Company", "Title", "Location", "Fit Score (0-100)", "Career-ops score",
               "Report", "Rule", "Hidden Reason", "Evidence", "Override", "Key"]
MANUAL_COLS = ["Company", "Group", "Metros", "Priority", "ATS", "Careers Page"]
LOG_COLS = ["Run Date", "Run Time", "Companies Scanned", "Jobs Seen by Scan", "Roles Found", "Kept", "Hidden",
            "New Since Last Run", "Boards Errored"]
USER_BOARD_COLS = ["Status", "Applied Date", "Resume", "Cover Letter", "Notes"]

GREEN, YELLOW, GREY = "C6EFCE", "FFEB9C", "D9D9D9"


class MasterUnreadable(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------

def _to_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().lstrip("~")
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def track_of(title: str, cfg: dict | None = None) -> str:
    from .score import track_for
    return track_for(title, cfg)["name"]


def metro_label(key: str) -> str:
    return config.filters_config()["metros"].get(key, {}).get("label", "") if key else ""


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------
# stable numeric IDs
# ----------------------------------------------------------------------------------------------

class Ledger:
    """hash id (from scan history) -> small integer ID you type with /apply 14. Never reused."""

    def __init__(self, path: Path):
        self.path = path
        data = _read_json(path, {})
        self.next_id: int = int(data.get("next_id", 1))
        self.jobs: dict[str, dict] = data.get("jobs", {})

    def num(self, key: str, first_found: date, company: str = "", title: str = "") -> int:
        rec = self.jobs.get(key)
        if rec is None:
            rec = {"id": self.next_id, "first_found": first_found.isoformat(), "company": company, "title": title}
            self.jobs[key] = rec
            self.next_id += 1
        return rec["id"]

    def first_found(self, key: str) -> date | None:
        rec = self.jobs.get(key)
        return _to_date(rec["first_found"]) if rec else None

    def adopt(self, key: str, num: int) -> None:
        """Rebuild a lost ledger entry from a master row (Key column)."""
        if key and key not in self.jobs and not any(r["id"] == num for r in self.jobs.values()):
            self.jobs[key] = {"id": num, "first_found": date.today().isoformat(), "company": "", "title": ""}
            self.next_id = max(self.next_id, num + 1)

    def save(self) -> None:
        _write_json(self.path, {"next_id": self.next_id, "jobs": self.jobs})


# ----------------------------------------------------------------------------------------------
# reading the master back
# ----------------------------------------------------------------------------------------------

@dataclass
class MasterState:
    board: dict[int, dict] = field(default_factory=dict)      # ID -> row dict (by header name)
    hidden_overrides: set[int] = field(default_factory=set)
    hidden_ids: set[int] = field(default_factory=set)
    hidden_rows: list[dict] = field(default_factory=list)
    funnel_rows: list[tuple[list, bool]] = field(default_factory=list)   # (values, bold) copied through re-renders
    run_log: list[dict] = field(default_factory=list)
    existed: bool = False


def _sheet_rows(ws) -> list[dict]:
    rows = list(ws.iter_rows())
    if not rows:
        return []
    headers = [c.value for c in rows[0]]
    out = []
    for r in rows[1:]:
        d: dict = {}
        for h, c in zip(headers, r):
            if h is None:
                continue
            d[h] = c.value
            if c.hyperlink is not None:
                d[f"{h}__link"] = c.hyperlink.target or c.hyperlink.location
        d["__bold"] = bool(r[0].font and r[0].font.bold)      # "new since last run" marker
        out.append(d)
    return out


def read_master(path: Path) -> MasterState:
    state = MasterState()
    if not path.exists():
        return state
    src = path
    tmp = None
    try:
        try:
            wb = load_workbook(src)
        except PermissionError:
            fd, name = tempfile.mkstemp(suffix=".xlsx")
            os.close(fd)
            tmp = Path(name)
            shutil.copyfile(path, tmp)          # Excel allows reading a copy while it holds the file
            wb = load_workbook(tmp)
    except Exception as exc:                    # unreadable: refuse to overwrite statuses we cannot see
        raise MasterUnreadable(f"{path}: {exc}") from exc
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
    state.existed = True
    if "Board" in wb.sheetnames:
        for row in _sheet_rows(wb["Board"]):
            n = _num(row.get("ID"))
            if n is not None:
                state.board[int(n)] = row
    if "Hidden" in wb.sheetnames:
        for row in _sheet_rows(wb["Hidden"]):
            n = _num(row.get("ID"))
            state.hidden_rows.append(row)
            if n is not None:
                state.hidden_ids.add(int(n))
                if str(row.get("Override") or "").strip():
                    state.hidden_overrides.add(int(n))
    if "Funnel" in wb.sheetnames:
        for r in wb["Funnel"].iter_rows():
            state.funnel_rows.append(([c.value for c in r], bool(r[0].font and r[0].font.bold)))
    if "Run Log" in wb.sheetnames:
        state.run_log = [r for r in _sheet_rows(wb["Run Log"]) if r.get("Run Date")]
    return state


# ----------------------------------------------------------------------------------------------
# building the row sets
# ----------------------------------------------------------------------------------------------

def application_folder(num: int) -> Path | None:
    """applications/<date>_<company>_<title>/ for this ID, found through its job.json marker."""
    base = config.applications_dir()
    if not base.exists():
        return None
    for marker in sorted(base.glob("*/job.json")):
        if _read_json(marker, {}).get("id") == num:
            return marker.parent
    return None


def _find_pdf(num: int, kind: str) -> str:
    folder = application_folder(num)
    if folder is None:
        return ""
    for pdf in sorted(folder.glob("*.pdf")):
        if kind in pdf.name.lower().replace("_", "").replace("-", ""):
            return str(pdf)
    return ""


def _years_cell(job: dict) -> float | None:
    y = job.get("years_required")
    return float(y) if y is not None else None


def build_rows(result: dict, master: MasterState, ledger: Ledger, today: date) -> tuple[list[dict], list[dict], set[int]]:
    """Return (board_rows, hidden_rows, new_ids) as dicts keyed by column name."""
    for num, old in master.board.items():                 # rebuild lost ledger entries from the Key column
        ledger.adopt(str(old.get("Key") or ""), num)

    board: dict[int, dict] = {}
    hidden: list[dict] = []
    seen_nums: set[int] = set()

    def base(job: dict) -> tuple[int, date]:
        found = _to_date(job.get("first_seen")) or today
        num = ledger.num(job["id"], found, job["company"], job["title"])
        led = ledger.first_found(job["id"])
        return num, min(found, led) if led else found

    def posted_cell(job: dict) -> str:
        if job.get("date_flag"):
            return f"unconfirmed: {job['date_flag']}"
        p = job.get("posted") or ""
        return f"~{p}" if p and job.get("date_approx") else p

    for job in result["shown"] + result["hidden"]:
        num, found = base(job)
        seen_nums.add(num)
        is_hidden = bool(job["reasons"]) and not (num in master.hidden_overrides)
        score = job.get("score")
        old = master.board.get(num, {})
        flags = list(job["flags"])
        touched =(old.get("Status") or "New") != "New" or str(old.get("Notes") or "").strip()
        if job["reasons"] and is_hidden and touched:
            is_hidden = False                       # you already worked this row; a filter change must not bury it
            flags.append("now filtered: " + job["reasons"][0]["text"])
        elif job["reasons"] and not is_hidden:
            flags.append("override: " + job["reasons"][0]["text"])
        if is_hidden:
            r0 = job["reasons"][0]
            hidden.append({
                "ID": num, "Step": _step_label(job.get("step")), "Date Found": found, "Posted": posted_cell(job),
                "Company": job["company"],
                "Title": job["title"], "Title__link": job["url"], "Location": job["location"],
                "Fit Score (0-100)": score, "Career-ops score": job.get("careerops_score"),
                "Report": "Report" if job.get("careerops_report") else "", "Report__link": job.get("careerops_report") or None,
                "Rule": ", ".join(r["rule"] for r in job["reasons"]),
                "Hidden Reason": "; ".join(r["text"] for r in job["reasons"]),
                "Evidence": next((r["evidence"] for r in job["reasons"] if r["evidence"]), ""),
                "Override": "", "Key": job["id"],
            })
            continue
        age = _to_date(job.get("posted"))
        board[num] = {
            "ID": num, "Date Found": found, "Posted": posted_cell(job),
            "Days Old": (today - age).days if age else job.get("age_days"),
            "Company": job["company"], "Title": job["title"], "Title__link": job["url"],
            "Track": track_of(job["title"]),
            "Location / Metro": f"{job['location']} — {metro_label(job['metro'])}" if job.get("metro") else job["location"],
            "Fit Score (0-100)": score, "Career-ops score": job.get("careerops_score"),
            "Report": "Report" if job.get("careerops_report") else "", "Report__link": job.get("careerops_report") or None,
            "Years Req": _years_cell(job), "Flags": "; ".join(flags),
            "Why it fits / Gaps": old.get("Why it fits / Gaps") or "",
            "Status": old.get("Status") or "New",
            "Applied Date": _to_date(old.get("Applied Date")),
            "Resume": old.get("Resume") or "", "Resume__link": old.get("Resume__link") or _find_pdf(num, "resume"),
            "Cover Letter": old.get("Cover Letter") or "", "Cover Letter__link": old.get("Cover Letter__link") or _find_pdf(num, "coverletter"),
            "Notes": old.get("Notes") or "", "Key": job["id"],
        }
        for col in ("Resume", "Cover Letter"):
            if board[num][f"{col}__link"] and not board[num][col]:
                board[num][col] = col

    # Carry rows from earlier runs that are not in this run, so marked statuses are never lost.
    for num, old in master.board.items():
        if num in board or num in seen_nums:
            continue
        status = old.get("Status") or "New"
        notes = str(old.get("Notes") or "").strip()
        found = _to_date(old.get("Date Found")) or today
        if status != "New" or notes or (today - found).days <= KEEP_NEW_DAYS:
            row = dict(old)
            p = _to_date(row.get("Posted"))
            row["Days Old"] = (today - p).days if p else row.get("Days Old")
            row["Date Found"] = found
            row["Applied Date"] = _to_date(row.get("Applied Date"))
            board[num] = row

    known = set(master.board) | master.hidden_ids
    new_ids = {n for n in board if n not in known} | {h["ID"] for h in hidden if h["ID"] is not None and h["ID"] not in known}
    rows = sorted(board.values(), key=rank_key)
    hidden.sort(key=rank_key)
    dropped = []
    for d in result.get("dropped", []):
        dropped.append({
            "ID": None, "Step": _step_label(d["step"]), "Date Found": _to_date(d.get("first_seen")) or today,
            "Posted": d.get("posted") or "", "Company": d["company"], "Title": d["title"], "Title__link": d["url"],
            "Location": d["location"], "Fit Score (0-100)": None, "Career-ops score": None, "Report": "", "Rule": d["rule"],
            "Hidden Reason": d["reason"], "Evidence": d.get("evidence", ""), "Override": "", "Key": d["id"],
        })
    dropped.sort(key=lambda r: (-int(str(r["Step"]).split()[0]), r["Company"], r["Title"]))
    return rows, hidden + dropped, new_ids


def _step_label(step) -> str:
    from .funnel import STEP_NAMES
    return f"{step} {STEP_NAMES.get(step, '')}".strip() if step else ""


def rank_key(row: dict):
    """Board order: career-ops score (best first, unscored last), then Fit Score, then ID."""
    co = _num(row.get("Career-ops score"))
    fit = _num(row.get("Fit Score (0-100)"))
    return (-(co if co is not None else -1), -(fit if fit is not None else -1), int(row["ID"]))


def manual_rows() -> list[dict]:
    out = []
    for c in config.companies_config():
        if c.get("scan_status") in ("not scannable", "manual"):
            out.append({"Company": c["name"], "Group": c.get("category", ""), "Metros": ", ".join(c.get("metros", [])),
                        "Priority": c.get("priority"), "ATS": c.get("ats", "") + (f" ({c['note']})" if c.get("note") else ""),
                        "Careers Page": c.get("careers_url") or "", "Careers Page__link": c.get("careers_url") or ""})
    out.sort(key=lambda r: (r["Priority"] or 9, r["Company"]))
    return out


def log_row(result: dict, board_rows: list[dict], hidden_rows: list[dict], new_ids: set[int], receipt: dict | None,
            now: datetime) -> dict:
    errors = [e.get("company", "?") + (f" ({e['error']})" if e.get("error") else "") for e in (receipt or {}).get("errors", [])]
    return {
        "Run Date": now.date().isoformat(), "Run Time": now.strftime("%H:%M"),
        "Companies Scanned": (receipt or {}).get("scanned", "n/a"),
        "Jobs Seen by Scan": (receipt or {}).get("found", "n/a"),
        "Roles Found": len(result["shown"]) + len(result["hidden"]),
        "Kept": len(board_rows), "Hidden": len(hidden_rows),
        "New Since Last Run": len(new_ids),
        "Boards Errored": ("; ".join(errors) or "none") if receipt else "n/a (no scan receipt)",
    }


# ----------------------------------------------------------------------------------------------
# writing the workbook
# ----------------------------------------------------------------------------------------------

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_WIDTHS = {"ID": 6, "Date Found": 12, "Posted": 12, "Days Old": 9, "Company": 22, "Title": 48, "Track": 17,
           "Location / Metro": 34, "Fit Score (0-100)": 11, "Career-ops score": 11, "Report": 9, "Years Req": 9, "Flags": 40, "Why it fits / Gaps": 46,
           "Status": 14, "Applied Date": 12, "Resume": 12, "Cover Letter": 13, "Notes": 36, "Key": 10,
           "Location": 30, "Rule": 18, "Hidden Reason": 36, "Evidence": 60, "Override": 10,
           "Group": 18, "Metros": 26, "Priority": 9, "ATS": 22, "Careers Page": 60,
           "Run Date": 12, "Run Time": 9, "Companies Scanned": 11, "Jobs Seen by Scan": 12, "Roles Found": 11,
           "Kept": 8, "Hidden": 8, "New Since Last Run": 12, "Boards Errored": 50}
_WRAP = {"Flags", "Why it fits / Gaps", "Notes", "Evidence", "Hidden Reason", "Boards Errored", "Location / Metro"}
_DATE_COLS = {"Date Found", "Applied Date"}


def _write_sheet(ws, cols: list[str], rows: list[dict], link_cols: set[str], bold_ids: set[int] | None = None,
                 hide_cols: tuple[str, ...] = ()) -> None:
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _HEADER_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append([r.get(c) for c in cols])
        row_idx = ws.max_row
        is_new = bool(bold_ids) and r.get("ID") in bold_ids
        for j, col in enumerate(cols, start=1):
            cell = ws.cell(row=row_idx, column=j)
            if col in link_cols and r.get(f"{col}__link"):
                cell.hyperlink = r[f"{col}__link"]
                cell.font = Font(color="0563C1", underline="single", bold=is_new)
            elif is_new:
                cell.font = Font(bold=True)
            if col in _DATE_COLS and cell.value is not None:
                cell.number_format = "yyyy-mm-dd"
            cell.alignment = Alignment(vertical="top", wrap_text=col in _WRAP)
    for j, col in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(j)].width = _WIDTHS.get(col, 14)
        if col in hide_cols:
            ws.column_dimensions[get_column_letter(j)].hidden = True
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(ws.max_row, 2)}"


def build_workbook(board_rows, hidden_rows, manual, log_rows, new_ids: set[int], funnel_rows=None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Board"
    _write_sheet(ws, BOARD_COLS, board_rows, {"Title", "Report", "Resume", "Cover Letter"}, bold_ids=new_ids, hide_cols=("Key",))
    last = max(ws.max_row, 2)
    col = {name: get_column_letter(i) for i, name in enumerate(BOARD_COLS, start=1)}

    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    dv.error, dv.errorTitle = "Pick a status from the list", "Status"
    ws.add_data_validation(dv)
    dv.add(f"{col['Status']}2:{col['Status']}{max(last, 500)}")

    # Greyed Applied/Skip rows first (stop), then the Fit Score shading.
    all_cols = f"A2:{col['Notes']}{max(last, 500)}"
    st = f"${col['Status']}2"
    ws.conditional_formatting.add(all_cols, FormulaRule(formula=[f'OR({st}="Applied",{st}="Skip")'], stopIfTrue=True,
                                                         font=Font(color="808080"), fill=PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")))
    f = col["Fit Score (0-100)"]
    rng = f"{f}2:{f}{max(last, 500)}"
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f"AND(ISNUMBER({f}2),{f}2>=70)"], fill=PatternFill(start_color=GREEN, end_color=GREEN, fill_type="solid")))
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f"AND(ISNUMBER({f}2),{f}2>=50,{f}2<70)"], fill=PatternFill(start_color=YELLOW, end_color=YELLOW, fill_type="solid")))
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f"AND(ISNUMBER({f}2),{f}2<50)"], fill=PatternFill(start_color=GREY, end_color=GREY, fill_type="solid")))

    wh = wb.create_sheet("Hidden")
    _write_sheet(wh, HIDDEN_COLS, hidden_rows, {"Title", "Report"}, bold_ids=new_ids, hide_cols=("Key",))
    ov = DataValidation(type="list", formula1='"Show"', allow_blank=True)
    wh.add_data_validation(ov)
    oc = get_column_letter(HIDDEN_COLS.index("Override") + 1)
    ov.add(f"{oc}2:{oc}{max(wh.max_row, 500)}")

    wf = wb.create_sheet("Funnel")
    for values, bold in funnel_rows or []:
        wf.append(values)
        if bold:
            for c in wf[wf.max_row]:
                c.font = Font(bold=True)
    wf.column_dimensions["A"].width = 34
    for letter in "BCDEFGH":
        wf.column_dimensions[letter].width = 14
    wf.freeze_panes = "A2"

    wm = wb.create_sheet("Check Manually")
    _write_sheet(wm, MANUAL_COLS, manual, {"Careers Page"})

    wl = wb.create_sheet("Run Log")
    _write_sheet(wl, LOG_COLS, log_rows, set())
    return wb


# ----------------------------------------------------------------------------------------------
# saving without clobbering a file that is open in Excel
# ----------------------------------------------------------------------------------------------

def save_safely(wb: Workbook, target: Path, now: datetime) -> tuple[Path, bool]:
    """Save to `target`; if it is locked (open in Excel) save a timestamped copy instead.

    Returns (path_written, was_locked).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", dir=str(target.parent), prefix=".~tmp_")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        wb.save(tmp)
        try:
            os.replace(tmp, target)
            return target, False
        except PermissionError:
            alt = target.with_name(f"{target.stem}_{now.strftime('%Y-%m-%d_%H%M%S')}{target.suffix}")
            os.replace(tmp, alt)
            return alt, True
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def funnel_rows(result: dict, hidden_total: int, hidden_listed: int) -> list[tuple[list, bool]]:
    """The Funnel sheet: counts per company per step, the step totals, and the scan's own numbers."""
    from .funnel import STAGES, STEP_NAMES
    f = result.get("funnel") or {}
    rows: list[tuple[list, bool]] = [([f"Funnel, run {result['run_date']} (window {result['window_days']} days). "
                                       "Counts of postings still standing after each step."], True), ([], False)]
    header = ["Company", "Pulled", "After date", "After location", "After titles", "After finance", "After years", "After fit"]
    rows.append((header, True))
    companies = f.get("companies", {})
    for name in sorted(companies, key=lambda n: (-companies[n]["pulled"], n)):
        rows.append(([name] + [companies[name][s] for s in STAGES], False))
    tot = f.get("totals", {})
    rows.append((["TOTAL"] + [tot.get(s, 0) for s in STAGES], True))
    rows += [([], False), (["Step", "Name", "In", "Out", "Dropped"], True)]
    for s in f.get("steps", []):
        rows.append(([s["step"], s["name"], s["in"], s["out"], s["in"] - s["out"] if s["step"] > 1 else None], False))
    scan = f.get("scan") or {}
    rows += [([], False), (["career-ops scan receipt"], True)]
    if scan:
        rows.append((["Boards scanned", scan.get("scanned")], False))
        rows.append((["Jobs read by the scan", scan.get("found")], False))
        rows.append((["Dropped by the scan's own date window", scan.get("filtered")], False))
        rows.append((["Boards that errored", len(scan.get("errors") or [])], True))
        for e in scan.get("errors") or []:
            rows.append(([f"  {e.get('company')}", e.get("error")], False))
        if scan.get("unverified_zero"):
            rows.append((["Boards that answered with zero jobs (unverified)", ", ".join(scan["unverified_zero"])], False))
    else:
        rows.append((["No scan receipt (state/last_scan.json) was found."], False))
    c = f.get("classifier") or {}
    if c.get("titles"):
        rows += [([], False), (["Step 5 Claude classification"], True),
                 (["Titles judged", c["titles"]], False), (["Calls", c.get("calls")], False),
                 (["Served from cache", c.get("cached")], False), (["Failed calls", c.get("failed_calls")], False),
                 (["Titles over the call cap (kept)", c.get("uncapped_titles")], False)]
    rows += [([], False), (["Hidden sheet"], True), (["Rows dropped in all steps", hidden_total], False)]
    if hidden_listed < hidden_total:
        rows.append(([f"Listed on the Hidden sheet: {hidden_listed}. The rest are in the output folder, Dropped_{result['run_date']}.csv "
                      f"(newest steps first)."], True))
    return rows


def load_receipt(path: Path | None) -> dict | None:
    path = path or (config.state_dir() / "last_scan.json")
    data = _read_json(path, None)
    return data if isinstance(data, dict) else None


def write_outputs(result: dict, now: datetime | None = None, out_dir: Path | None = None, state_dir: Path | None = None,
                  scan_receipt: Path | None = None) -> dict:
    """Merge user edits from the master, then write the snapshot and the master."""
    now = now or datetime.now()
    today = now.date()
    out_dir = out_dir or config.output_dir()
    sdir = state_dir or config.state_dir()
    master_path = out_dir / "JobBoard_Master.xlsx"
    snap_path = out_dir / f"JobBoard_{today.isoformat()}.xlsx"

    unreadable = ""
    try:
        master = read_master(master_path)
    except MasterUnreadable as exc:              # never overwrite a master whose statuses we cannot see
        master, unreadable = MasterState(), str(exc)
    ledger = Ledger(sdir / "ids.json")
    overrides = set(_read_json(sdir / "overrides.json", []))
    master.hidden_overrides |= overrides

    board_rows, hidden_rows, new_ids = build_rows(result, master, ledger, today)
    _write_json(sdir / "overrides.json", sorted(master.hidden_overrides))
    ledger.save()

    receipt = load_receipt(scan_receipt)
    entry = log_row(result, board_rows, hidden_rows, new_ids, receipt, now)
    log = [r for r in master.run_log if str(r.get("Run Date")) != entry["Run Date"]] + [entry]
    log_rows = [{c: r.get(c) for c in LOG_COLS} for r in log]

    from .funnel import funnel_config
    cap = int(funnel_config().get("hidden_sheet_max_rows", 25000))
    listed = hidden_rows[:cap]
    wb = build_workbook(board_rows, listed, manual_rows(), log_rows, new_ids,
                        funnel_rows=funnel_rows(result, len(hidden_rows), len(listed)))
    snap, snap_locked = save_safely(wb, snap_path, now)
    notes = []
    if unreadable:
        master_written, master_locked = master_path, False
        notes.append(f"Master NOT updated: could not read it ({unreadable}). Statuses in it are untouched; "
                     "fix or move the file and run again.")
    else:
        master_written, master_locked = save_safely(wb, master_path, now)
    for label, path, locked in (("snapshot", snap, snap_locked), ("master", master_written, master_locked)):
        if locked:
            notes.append(f"{label} is open in Excel; saved a timestamped copy instead: {path}")
    if master_locked:
        notes.append("Close JobBoard_Master.xlsx and run again to update it. Your statuses in it are untouched.")
    ids = {key: rec["id"] for key, rec in ledger.jobs.items()}
    return {"snapshot": str(snap), "master": str(master_written), "locked": snap_locked or master_locked,
            "notes": notes, "board": len(board_rows), "hidden": len(hidden_rows), "new": len(new_ids),
            "ids": ids, "board_nums": [r["ID"] for r in sorted(board_rows, key=rank_key)]}


# ----------------------------------------------------------------------------------------------
# /mark and /apply: change cells on one Board row in place
# ----------------------------------------------------------------------------------------------

def normalize_status(text: str) -> str:
    key = re.sub(r"[\s_\-]+", "", str(text)).lower()
    for s in STATUSES:
        if re.sub(r"[\s_\-]+", "", s).lower() == key:
            return s
    raise ValueError(f"unknown status {text!r}; choose one of: {', '.join(STATUSES)}")


def _patch_workbook(path: Path, num: int, updates: dict, now: datetime) -> tuple[Path, bool, dict] | None:
    """Edit Board row `num` in an existing workbook. Returns (saved_path, locked, old_values) or None if absent."""
    if not path.exists():
        return None
    wb = load_workbook(path)
    ws = wb["Board"]
    headers = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if _num(row[0].value) == num:
            old = {}
            for col, val in updates.items():
                cell = row[headers.index(col)]
                old[col] = cell.value
                link = None
                if isinstance(val, tuple):                       # (display text, link target)
                    val, link = val
                cell.value = val
                if col in _DATE_COLS and val is not None:
                    cell.number_format = "yyyy-mm-dd"
                if link:
                    cell.hyperlink = link
                    cell.font = Font(color="0563C1", underline="single", bold=bool(cell.font and cell.font.bold))
            saved, locked = save_safely(wb, path, now)
            return saved, locked, old
    return None


def patch_board_row(num: int, updates: dict, now: datetime | None = None, out_dir: Path | None = None) -> dict:
    """Apply `updates` ({column: value | (text, link)}) to the master and today's snapshot, in place."""
    now = now or datetime.now()
    out_dir = out_dir or config.output_dir()
    master = out_dir / "JobBoard_Master.xlsx"
    state = read_master(master)                    # raises MasterUnreadable
    if num not in state.board:
        if num in state.hidden_ids:
            raise KeyError(f"ID {num} is on the Hidden sheet. Type Show in its Override cell, run python -m board run, then retry.")
        raise KeyError(f"ID {num} is not on the Board in {master}")
    notes: list[str] = []
    before: dict = {}
    for label, path in (("master", master), ("snapshot", out_dir / f"JobBoard_{now.date().isoformat()}.xlsx")):
        res = _patch_workbook(path, num, updates, now)
        if res is None:
            continue
        saved, locked, old = res
        before = before or old
        if locked:
            notes.append(f"{label} is open in Excel; saved a timestamped copy instead: {saved}")
            if label == "master":
                notes.append("Close JobBoard_Master.xlsx and run the command again to update it.")
    row = state.board[num]
    return {"company": row.get("Company"), "title": row.get("Title"), "before": before, "notes": notes}


def mark_status(num: int, status: str, applied_date: date | None = None, now: datetime | None = None,
                out_dir: Path | None = None) -> dict:
    now = now or datetime.now()
    status = normalize_status(status)
    updates: dict = {"Status": status}
    if status == "Applied":
        out_dir_ = out_dir or config.output_dir()
        existing = read_master(out_dir_ / "JobBoard_Master.xlsx").board.get(num, {}).get("Applied Date")
        updates["Applied Date"] = applied_date or _to_date(existing) or now.date()
    elif applied_date:
        updates["Applied Date"] = applied_date
    res = patch_board_row(num, updates, now=now, out_dir=out_dir)
    res.update(status=status, applied_date=updates.get("Applied Date"))
    return res
