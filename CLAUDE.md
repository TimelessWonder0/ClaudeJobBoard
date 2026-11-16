# ClaudeJobBoard — project rules

Personal daily job board for Shreyas Kukanur. Runs on Windows 11 (Windows PowerShell 5.1 primary, Git Bash available). Repo: `TimelessWonder0/ClaudeJobBoard` (private). Setup, layout and usage are in `README.md`; design notes and company-resolution results are in `PLAN.md`.

## Goal

Every day: find finance roles at my target companies posted in the last 3 days and within my reach, score them against my resume, and write a dated Excel board I can sort and mark Applied. When I pick a role, build a tailored resume and cover letter PDF for it.

## How to run it

**One command, from any folder** (the `jobboard` function lives in `$PROFILE`; `jobboard.cmd` does the same on double-click):

```
jobboard             # scan, build the Excel board, summary, open Excel
jobboard -Review     # same, plus claude -p "/review" so the top 25 get Why it fits / Gaps
jobboard -NoOpen     # do not open Excel at the end
jobboard -Days 7     # scan window in days (default 3)
jobboard -SkipScan   # rebuild the board from the last scan only
```

`C:\ClaudeJobBoard\jobboard.ps1` runs, in order: (1) `node scan.mjs --since 3 --json` (whole boards, about 10-20 minutes) in `career-ops\`, receipt saved to `logs\<date>_scan.json` and `state\last_scan.json`; (2) `python -m board run`; (3) `claude -p "/review"` only with `-Review`; (4) a summary (companies scanned, boards that errored, roles found / kept / hidden, new since last run); (5) open `output\JobBoard_Master.xlsx` unless `-NoOpen`. Everything is logged to `logs\<date>.log`.

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

- Roles: Financial Analyst, FP&A, Pricing / Deal Finance, Equity Research Associate and finance-adjacent roles. Entry level to 2 years required.
- Metros: Bay Area, Seattle, Austin, Chicago, New York, Boston, Miami, Hawaii. On-site or hybrid only.
- Companies: `config/companies.yaml` (69 companies, source of truth for names, metros, priority). `career-ops-data/portals.yml` is generated from it; resolution status is in PLAN.md. Do not add companies the user did not list.

## The negative-filter funnel (replaces keyword search)

The board does not search for finance words. It pulls **every job at every company**, then removes what clearly does not fit, cheapest steps first. A good posting with an unusual title survives unless a stated rule removes it. Implemented in `board/funnel.py` (steps 1-5) and `board/pipeline.py` (steps 6-7); config in `config/funnel.yaml`, `config/negative_titles.yaml`, `config/filters.yaml`, `config/careerops.yaml`.

1. **Pull everything.** In `career-ops-data/portals.yml`: `title_filter.positive` and `negative` are EMPTY, no `location_filter`, no `experience_filter`, no search queries (Amazon and Google use a blank search; Apple's provider refuses an empty one, so it uses the single letter "a"), page limits raised (`max_pages`). career-ops then records every job, with `max_posting_age_days: 3`.
2. **Posted within `window_days` (3).** The scan uses `--since 3`. Undated postings stay only if first seen within 3 days, flagged "date approx".
3. **Location.** Keep the 8 metros and multi-location postings that include one. Drop fully remote and other places. Empty or vague locations stay, flagged.
4. **Negative titles** (`config/negative_titles.yaml`, whole words only, so "lead" does not drop "leadership"): senior, sr, lead, principal, staff, manager, mgr, director, head, vp, vice president, president, chief, officer, partner (not "business partner"), III and above, engineer, engineering, developer, software, scientist, architect, designer, technician, mechanic, nurse, physician, pharmacist, driver, warehouse, associate only next to a retail/warehouse word, sales representative, account executive, account manager, recruiter, attorney, counsel, paralegal, intern, internship, co-op. Sector words (software, engineering, warehouse) are ignored when the title is clearly a finance role ("Equity Research Associate, Software").
5. **Finance relevance, generous.** Keep if the title or ATS department matches the keyword list (finance, FP&A, planning, corporate development, M&A, strategy, business operations, pricing, deal desk, revenue, treasury, investor relations, investment, equity, research, valuation, capital, credit, risk analyst, commercial analyst). Accounting, audit, tax and payroll titles go to Hidden as "accounting track". Titles matching nothing are judged by Claude (batches of 50, keep or drop, cached by title in `state/title_class.json`); if Claude fails or is capped the title is KEPT with a flag. ATS departments are not stored by career-ops' scan history, so the department test only works where the URL carries one (Apple `?team=`).
6. **Experience, degree, license (needs the JD, survivors only).** Required years above `max_required_years` (**2**) are dropped; 2 is kept as "stretch"; "not stated" is kept and flagged. "Bachelor's + 3 or Master's + 1" is 3. Required section only. Career-ops `jd-years.mjs` cross-checks; the higher number wins and is flagged. Also dropped: MBA, Master's, CPA or CFA required; Series 7/63/65/66 required before hire (sponsored after hire is kept, "licenses sponsored"); a new-grad window that clearly excludes a Dec 2024 graduate; fully remote per the JD; quant roles.
7. **Fit and ranking.** Liveness check (`check-liveness.mjs`) drops closed postings; a career-ops evaluation (1-5 score and report) runs on survivors, at most `max_evaluations_per_run` (25) new ones per run, best Fit Score first, the rest flagged "career-ops not evaluated yet"; below `min_score` (3.0) is hidden as "career-ops: low fit". Board order: career-ops score, then Fit Score.

**Safety net.** Every dropped row goes to the Hidden sheet with its step and reason, and to `output/Dropped_<date>.csv` (the sheet is capped at `hidden_sheet_max_rows`; the CSV is complete). Rows dropped before the JD stage (steps 2-5) have no ID and cannot be overridden from Excel; change the config instead. The **Funnel** sheet has counts per company per step (pulled, after date, after location, after titles, after finance, after years, after fit) plus the scan's own totals and boards that errored. Each run ends by printing 25 random titles dropped at step 4 and 25 at step 5. `python -m board table --ids 1-17` prints row, company, title, kept/hidden and reason.

**Tests.** `tests/test_regression_2026_10_29.py` (17 real rows, JDs in `tests/fixtures/`) and `tests/test_funnel.py` keep senior and 3+ year roles hidden and prove that unusual finance titles ("Analyst, Corporate Development", "Treasury Analyst", "Investor Relations Associate", ...) survive steps 4-5. Run `python -m pytest tests -q` before committing.

## Career-ops evaluation (step 7)

A headless `claude -p` worker follows `modes/oferta.md`, writes `career-ops-data/reports/<n>-<company>-<date>.md`, and the 1-5 score is read from its `**Score:**` header. The worker gets Read/Write/Glob/Grep only (no shell), because JD text is untrusted. Results are cached in `state/careerops.json`; an evaluation failure flags the row and never hides it. The candidate's targeting and score caps are in `career-ops-data/modes/_profile.md`. `python -m board run --no-gate` skips steps 5(c) and 7's external calls; `--refresh-careerops` re-evaluates.

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
- Funnel steps, filters and scoring are pure functions with unit tests. Every drop returns a step and a reason; every dropped row is logged. Run `python -m pytest tests -q` before committing.
- Job IDs are stable (8-hex hash of `listing_key` or `url`; the Excel ID is a separate small integer from `state/ids.json`).
- Be polite to job sites: obey career-ops' rate limits, cache JD fetches on disk, do not scrape LinkedIn, respect each site's ToS.
- Do not commit secrets, `career-ops/`, `career-ops-data/`, `output/`, `state/`, `jobs/`, `applications/`, `logs/`. `inputs/` and `config/candidate.yaml` hold personal data; the repo stays private.

## Working style

- Plan before code. Ask me before: changing filter rules, changing how location is shown on materials, pushing to GitHub (unless I just asked for it), or anything that applies to a job for me. **Never submit an application.**
- Report honestly: if a scan returns zero results, a fetch fails, or a score is low-confidence, say so.
