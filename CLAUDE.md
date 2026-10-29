# ClaudeJobBoard — project rules

Personal daily job board for Shreyas Kukanur. Runs on Windows 11 (Windows PowerShell 5.1 primary, Git Bash available). Repo: `TimelessWonder0/ClaudeJobBoard` (private). Setup, layout and usage are in `README.md`; design notes and company-resolution results are in `PLAN.md`.

## Goal

Every day: find finance roles at my target companies posted in the last 4 days and within my reach, score them against my resume, and write a dated Excel board I can sort and mark Applied. When I pick a role, build a tailored resume and cover letter PDF for it.

## How to run it

**One command, from any folder** (the `jobboard` function lives in `$PROFILE`; `jobboard.cmd` does the same on double-click):

```
jobboard             # scan, build the Excel board, summary, open Excel
jobboard -Review     # same, plus claude -p "/review" so the top 25 get Why it fits / Gaps
jobboard -NoOpen     # do not open Excel at the end
jobboard -Days 7     # scan window in days (default 4)
jobboard -SkipScan   # rebuild the board from the last scan only
```

`C:\ClaudeJobBoard\jobboard.ps1` runs, in order: (1) `node scan.mjs --since 4 --json` in `career-ops\`, receipt saved to `logs\<date>_scan.json` and `state\last_scan.json`; (2) `python -m board run`; (3) `claude -p "/review"` only with `-Review`; (4) a summary (companies scanned, boards that errored, roles found / kept / hidden, new since last run); (5) open `output\JobBoard_Master.xlsx` unless `-NoOpen`. Everything is logged to `logs\<date>.log`.

- Exit codes: 0 ok, 1 a step failed (the message names the step and why), 2 every board failed (check the internet; the run stops before step 2), 3 prerequisite missing.
- `scan.mjs --json` exits 2 when **any** board failed, so exit 2 alone is not "all failed". The script decides from the receipt (errors plus `unverified_zero` against `scanned`).
- If `JobBoard_Master.xlsx` is open in Excel the script warns, waits for it to be closed (`-LockWaitSeconds`, default 300), then continues. If it is still open the board saves a timestamped copy instead of overwriting.
- **No Windows Task Scheduler task and no cron.** Runs happen only when I start them.
- Keep `jobboard.ps1`, `jobboard.cmd` and `tests\jobboard.tests.ps1` plain ASCII (Windows PowerShell 5.1 reads BOM-less files as ANSI).

**Slash commands** (`.claude\commands\`, same steps as the terminal):

- `/scan [days]` runs `jobboard.ps1 -NoOpen`, then runs `/review` in the current session instead of spawning a nested `claude -p`.
- `/review` writes "Why it fits" and "Gaps" for the top 25 rows; it may re-rank only with a reason and a verbatim JD quote.
- `/apply <id>` builds the resume and cover letter PDFs into `applications\<date>_<company>_<title>\`, writes `GapCheck.md`, puts the paths in Excel, sets Status = Docs Ready.
- `/mark <id> <status>` sets Status (and Applied Date for Applied) in the master.

## Candidate facts (source of truth: `config/candidate.yaml`, built from `inputs/`)

- Estimating & Pricing Specialist (Level 2), Boeing, since Jun 2025 (~1.5 yrs). 2024 roles: Fintax (consulting), Forrest Systems / SJSU Spartan Fund (analyst).
- BS Finance, SJSU (GPA 3.88, Aug 2021 – Dec 2024). MS Computer Science, Johns Hopkins, in progress (Jan 2026 – Jun 2028, part-time).
- US citizen. Strengths: pricing/cost models, variance and scenario analysis, FAR/CAS audit documentation, Excel (XLOOKUP, Power Query, VBA), SQL, Python, DCF valuation.
- Location on **all application materials: San Jose, CA**. The Boeing line shows **St. Louis County, MO**.

## Targets

- Roles: Financial Analyst, FP&A, Pricing / Deal Finance, Equity Research Associate. Entry level to 3 years.
- Metros: Bay Area, Seattle, Austin, Chicago, New York, Boston, Miami, Hawaii. On-site or hybrid only.
- Companies: `config/companies.yaml` (69 companies, source of truth for names, metros, priority). `career-ops-data/portals.yml` is generated from it; resolution status is in PLAN.md. Do not add companies the user did not list.

## Hard filters (hide from the board, but LOG the reason in the Hidden sheet)

1. Quant roles.
2. Master's / MBA **required** (preferred is fine).
3. More than 3 years of experience **required** (preferred is fine).
4. Title contains director, manager, VP / vice president, or intern.
5. Outside my metros.
6. Fully remote.
7. Posted more than 4 days ago.

Everything else is shown with a score and flags. I want to choose, so **do not over-filter**. When a rule is ambiguous (e.g. "3-5 years", unclear location, no posted date), show the job and add a flag instead of hiding it. Titles like "Senior" are flagged, not hidden. Only high- and medium-confidence evidence can hide a job. The rules live in `config/filters.yaml`; scoring weights in `config/scoring.yaml`.

## Architecture

1. `career-ops/` — clone of https://github.com/career-ops-hq/career-ops, used only for discovery: `node scan.mjs` + `portals.yml` → `scan-history.tsv`. Never edit its source. Keep the clone git-ignored.
   - **Data root: `C:\ClaudeJobBoard\career-ops-data`**, set by the user-level environment variable `CAREER_OPS_ROOT` (`setx`; new shells only). It holds `portals.yml`, `cv.md`, `config/profile.yml`, `modes/`, and `data/` (including `scan-history.tsv`). It is outside version control on purpose. `jobboard.ps1` and `board/` set the variable themselves when they spawn node.
   - `onboard-write.mjs` writes to the checkout by default; always pass `--target C:\ClaudeJobBoard\career-ops-data`.
2. `board/` — Python package: `ingest` (scan history), `jd` (job descriptions, cached in `jobs\<id>_<company>_<title>\JD.md`), `extract` (years, degree, certs, work mode with the source sentence), `filters`, `score`, `pipeline`, `excel`, `review`, `applications`, `cli`.
3. `jobboard.ps1` / `jobboard.cmd` — the one-command run. `.claude/commands/` — `/scan`, `/review`, `/apply`, `/mark`.

State: `state/` (ID ledger `ids.json`, `reviews.json`, `overrides.json`, `last_scan.json`, `last_run.json`), `output/` (workbooks), `applications/` (PDFs), `jobs/` (JDs), `logs/`. All git-ignored.

## Excel

`output\JobBoard_Master.xlsx` is the file I edit; `output\JobBoard_YYYY-MM-DD.xlsx` is each run's snapshot. A run reads the master back first, so Status, Applied Date, Resume, Cover Letter and Notes survive, matched by the numeric ID. Never overwrite a locked file: save a timestamped copy and say so. Rows I marked are never dropped.

## Rules for building tailored materials (`/apply`)

- **Never invent facts.** Employers, titles, dates, places, degrees and skills come from `config/candidate.yaml` and are printed by the tool. Reword and reorder the `bank` lines; do not fabricate metrics, tools, employers or credentials. The renderer rejects numbers, skills and credentials the file does not support. If the JD wants something I lack, say so in `GapCheck.md`; do not claim it.
- Header location is San Jose, CA. The Boeing line shows St. Louis County, MO (decided).
- MS CS at Johns Hopkins is listed as in progress, January 2026 - June 2028. Never imply it is finished.
- Style follows the Harvard guide in `inputs/`: reverse-chronological, concise, active verbs, fragments for bullets, most important content first, tailored to the posting. Resume 1 page, ATS-friendly (single column, real text). Cover letter 1 page: opening (role + who I am), evidence paragraphs mapped to the JD, close.
- Output PDFs: `Shreyas_Kukanur_Resume_<Company>.pdf` and `Shreyas_Kukanur_Cover_Letter_<Company>.pdf` in `applications\<date>_<company>_<title>\`.

## Code rules

- Python 3.12+ (3.13 installed), `openpyxl`, `pyyaml`, `requests`, `pypdf` (pinned in `requirements.txt`; `pytest` in `requirements-dev.txt`). Node only for career-ops and the PDF renderer.
- Windows: use `pathlib`, UTF-8 everywhere (`encoding="utf-8"`, `PYTHONIOENCODING=utf-8`), no bash-only assumptions in scripts. When writing regexes or Windows paths through a shell, prefer the Write/Edit tools; shell heredocs mangle backslashes.
- Parse scan-history.tsv by **column name** (the column order is append-only and rows can be short). Columns: `url, first_seen, portal, title, company, status, location, fingerprint, posted_at, trust_score, trust_flags, normalized_company, requisition_id, language, listing_key`.
- Filters and scoring are pure functions with unit tests. Every filter returns a reason string; every hidden job is logged. Run `python -m pytest tests -q` before committing.
- Job IDs are stable (8-hex hash of `listing_key` or `url`; the Excel ID is a separate small integer from `state/ids.json`).
- Be polite to job sites: obey career-ops' rate limits, cache JD fetches on disk, do not scrape LinkedIn, respect each site's ToS.
- Do not commit secrets, `career-ops/`, `career-ops-data/`, `output/`, `state/`, `jobs/`, `applications/`, `logs/`. `inputs/` and `config/candidate.yaml` hold personal data; the repo stays private.

## Working style

- Plan before code. Ask me before: changing filter rules, changing how location is shown on materials, pushing to GitHub (unless I just asked for it), or anything that applies to a job for me. **Never submit an application.**
- Report honestly: if a scan returns zero results, a fetch fails, or a score is low-confidence, say so.
