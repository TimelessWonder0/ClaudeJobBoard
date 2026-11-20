# ClaudeJobBoard — project rules

Personal daily job board for Shreyas Kukanur. Runs on Windows 11 (Windows PowerShell 5.1 primary, Git Bash available). Repo: `TimelessWonder0/ClaudeJobBoard` (private). Setup, layout and usage are in `README.md`; design notes and company-resolution results are in `PLAN.md`.

## Goal

Every day: find finance roles at my target companies posted in the last 4 days and within my reach, score them against my resume, and write `output\jobs_latest.md` (the file Claude reads) plus a dated Excel board I can sort and mark Applied. When I pick a role, build a tailored resume and cover letter PDF for it.

**Rebuild in progress (branch `rebuild/python-pipeline`).** Python does all fetching, filtering and scoring. `jobboard` runs `python -m board run`, which fetches the ATS APIs directly, applies the rules below, scores, and writes `jobs_latest.md`, `jobs_<date>.md` and the Excel board, with **zero Claude calls** unless `--with-claude` is passed to `python -m board run` (the `jobboard` command never passes it). `/apply` reads that role's entry in `jobs_latest.md` and its saved JD file, never the xlsx; Python updates the master.

## How to run it

**One command, from any folder** (the `jobboard` function lives in `$PROFILE`; `jobboard.cmd` does the same on double-click):

```
jobboard                  # fetch, filter, score; writes output\jobs_latest.md and the Excel board; 20-line summary
jobboard -Open            # same, then open JobBoard_Master.xlsx in Excel
jobboard -Only Stripe,greenhouse   # fetch only these companies / ATS names (testing)
jobboard -Full            # pull every posting from Workday / Eightfold / Oracle / SmartRecruiters / Amazon (slow, testing)
jobboard -SkipProviders   # skip the node scan.mjs step for Apple, Google and Pinpoint (that step runs by default and is slow)
```

`C:\ClaudeJobBoard\jobboard.ps1` first runs `node scan.mjs --company <name>` for each provider the Python fetchers do not cover (Apple, Google, Pinpoint; skipped with `-SkipProviders`), then `python -m board run`. Nothing else, and no Claude. The console shows the board's own summary, 20 lines at most; the log is `logs\<date>.log` and the detail is `logs\board_<date>.log`. The posting window is `days` in `config\filters.yaml`, not a script parameter.

- Exit codes: 0 ok, 1 the run failed, 2 every board failed (check the internet), 3 prerequisite missing or the workbook could not be written.
- If `JobBoard_Master.xlsx` is open in Excel the script warns, waits for it to be closed (`-LockWaitSeconds`, default 300), then continues. If it is still open the board saves a timestamped copy instead of overwriting.
- Apple, Google and Pinpoint have no Python fetcher; `jobboard` refreshes them with `scan.mjs` by default; with `-SkipProviders` they show only what the last scan saw. A failed provider scan is a warning, not a failure.
- **No Windows Task Scheduler task and no cron.** Runs happen only when I start them.
- Keep `jobboard.ps1`, `jobboard.cmd` and `tests\jobboard.tests.ps1` plain ASCII (Windows PowerShell 5.1 reads BOM-less files as ANSI).

**Slash commands** (`.claude\commands\`):

- `/jobs` reads `output\jobs_latest.md` first (warns if its run date is not today; never scans), skips roles marked "Previously reviewed: yes" (an earlier verdict in `state/reviews.json` or an old review workbook, or a Status other than New; Python prints this and "First seen" per role, so Claude never reads the xlsx; they go to Passed Over as "reviewed <date>" / "status <Status>"), gives the top 30 remaining Board roles and the top 10 remaining Date unconfirmed roles (by Python score) a verdict (Apply now / Strong / Stretch / Skip) with a one-line why and gap (for a graduating-class posting the gap states the eligibility requirement; Python prints the sentence as "Eligibility:" and compares the class with candidate.yaml's graduations as "Grad class: Spring 2027 — likely ineligible (...)", flag text only, no filter; /jobs makes the call); anything past the caps goes to Passed Over as "below cap" with its Python score, and writes `output\reviews\Claude_Review_<date>.xlsx` with `board/write_review.py` (sheets Picks, Date Unconfirmed, Passed Over, Run Info; never writes to the master).
- **Claude never runs or waits on the pipeline.** There is no `/scan` command; I run `jobboard` myself.
- `/apply <id>` builds the resume and cover letter PDFs into `applications\<date>_<company>_<title>\`, writes `GapCheck.md`, puts the paths in Excel, sets Status = Docs Ready.
- `/mark <id> <status>` sets Status (and Applied Date for Applied) in the master.

## Candidate facts (source of truth: `config/candidate.yaml`, built from `inputs/`)

- Estimating & Pricing Specialist (Level 2), Boeing, since Jun 2025 (~1.5 yrs). 2024 roles: Fintax (consulting), Forrest Systems / SJSU Spartan Fund (analyst).
- BS Finance, SJSU (GPA 3.88, Aug 2021 – Dec 2024). MS Computer Science, Johns Hopkins, in progress (Jan 2026 – Jun 2028, part-time).
- US citizen. Strengths: pricing/cost models, variance and scenario analysis, FAR/CAS audit documentation, Excel (XLOOKUP, Power Query, VBA), SQL, Python, DCF valuation.
- Location on **all application materials: San Jose, CA**. The Boeing line shows **St. Louis County, MO**.

## Targets

- Roles: Financial Analyst, FP&A, Pricing / Deal Finance, Equity Research Associate and finance-adjacent roles. Entry level to **3 years** required (a Senior title is kept up to 4).
- Metros (14): Bay Area, Seattle, Austin, Chicago, New York, Boston, Miami, Hawaii, Dallas, Denver, Atlanta, DC-Baltimore, Los Angeles, San Diego. On-site or hybrid only.
- Companies: `config/companies.yaml` (about 1,186 entries; `scan_status: OK` = fetched by the Python fetchers or by scan.mjs, `not scannable` = manual, `off` = not scanned, with a `note`). Defense primes (Lockheed Martin, RTX, Northrop Grumman, General Dynamics, L3Harris) are off ("defense: moves backward"); Boeing is not listed; defunct and acquired companies are off. `sector_tier` / `as_of` / `approx` are static facts used only by the future-facing score. **Company size and market cap are never used to filter or score.** `career-ops-data/portals.yml` is generated from it; resolution status is in PLAN.md. Do not add companies the user did not list.

## The negative-filter funnel (replaces keyword search)

The board does not search for finance words. It pulls **every job at every scannable company**, then removes what clearly does not fit, cheapest steps first. Implemented in `board/fetch/` (fetch and prefilter), `board/filters.py` (rules), `board/score.py` (score) and `board/pipeline.py` (the one pipeline); config in `config/filters.yaml`, `config/negative_titles.yaml`, `config/scoring.yaml`, `config/companies.yaml`. **No step calls Claude** (unless `--with-claude`, see below), and no step looks anything up on the web.

1. **Pull everything.** Python fetchers read each board's public API: Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Amazon, Eightfold, Oracle HCM. Apple, Google and Pinpoint have no Python fetcher; `node scan.mjs` still reads them into `scan-history.tsv`. Workday tenants with a cached Finance facet (`workday_facet` in companies.yaml, found once by `python -m board workday-facets`, paced, run by hand) are queried with that facet and no search text plus `fetch.workday_fallback_keywords` without the facet (merged by job ID, for roles filed under another category); a tenant with no finance category uses the fallback keywords only; one never discovered keeps `fetch.workday_keywords`. Raw list responses go to `logs\raw\<date>\`. A board has a per-board time limit (`fetch.board_time_limit_seconds`, 90); a board that hits it keeps what it listed and is reported as "partial". A board that errors is named in the summary.
2. **Posted within `days` (4), set once in `config/filters.yaml`.** Measured from run start in America/Chicago; dates are stored as UTC. A confirmed publish date more than 4 days old is dropped (exactly 4 days is kept, 4 days + 1 hour is dropped). Date fields: Greenhouse `first_published` (never `updated_at`), Lever `createdAt`, Ashby `publishedAt`, Workday `postedOn` ("Posted N Days Ago" turned into a date, "30+" is old), SmartRecruiters `releasedDate`, Amazon `posted_date`, Eightfold `postedTs`, Oracle `PostedDate`, scan.mjs `posted_at`. A role with no confirmed date that passes every other filter goes to **"Date unconfirmed"** with the reason ("updated_at only", "first seen 2026-11-14", "no date field"); one that fails the role filters is dropped.
3. **Location.** Keep the 14 metros and multi-location postings that include one. Drop fully remote and other places. Empty or vague locations ("2 Locations", "United States") stay, flagged; at the JD stage the JD's own "Location:" line replaces a vague one, and the metro rule then judges the real cities (all outside the metros is hidden with an ID).
4. **Titles.** Negative words (`config/negative_titles.yaml`, whole words only): lead, principal, staff, manager, director, head, vp, officer, engineer, software, ... **Senior / Sr is not dropped here**; the years rule decides. Then sales-like titles (client service, advisor, relationship, sales, banker, broker), "moves backward" titles (estimator, proposal pricing, cost/price analyst, EVM, program control), wrong function (accounting, tax, audit, AP/AR, payroll, AML / financial crimes, insurance, credit / underwriting / credit portfolio, commercial banking / commercial associate / relationship banking), and staffing agencies (Robert Half and the rest of `staffing_agencies`).
5. **Finance title allow-list** (`allowed_title_patterns`). A title not on it is dropped. If it still contains finance, financial, pricing, FP&A, treasury or analyst it is listed in the Hidden section with the reason "title not on list" and an ID, so it can be overridden. Other drops (Software Engineer, Operations Coordinator) are only in `Dropped_<date>.csv`.
6. **JD rules (survivors only).** The ATS list usually returns the JD; otherwise it is fetched and cached in `jobs\<id>_<company>_<title>\JD.md`. Required years above **3** are dropped; 3 is kept as "stretch"; a **Senior** title is kept up to **4**; "not stated" is kept and flagged. "Bachelor's + 4 or Master's + 2" is 4. Required section only. Also dropped: MBA, Master's, CPA or CFA required; Series 7/63/65/66 required before hire (sponsored after hire is kept); a new-grad window that clearly excludes a Dec 2024 graduate; fully remote per the JD; quant roles; staffing or contract postings ("our client", contract-to-hire); a cost / pricing analyst on government contracts (FAR, DFARS, DCAA).
7. **Score and dedupe.** The same posting seen twice is listed once, keeping the confirmed date. A role below `min_score` (50) is hidden as "Score below 50" with an ID.

**The 100-point score** (`config/scoring.yaml`, pure Python): role fit 35 (Track A FP&A or B strategic / product finance 35; C pricing or D any other finance role 25), level fit 20 (0-3 years 20, 4 years 12, unstated 15), skills overlap 15 (3 points per group of forecasting, variance / scenario, modeling, cross-functional, SQL / Python, automation / AI), **training signal 15** (rotational / development / leadership program, "Analyst I", new grad, early career +8; mentorship, structured training, "we will train", coaching, career development +4; explicit career ladder +3; capped at 15; the patterns are in `scoring.yaml` and the matched phrases are printed as "Training: ..."), future-facing sector 10 (`sector_tier`: future 10, adjacent 6, traditional 2), location 5 (California 5, Washington / New York 4, other metro 3). Tiers: Apply now 75+, Strong 60-74, Stretch 50-59.

**Output.** `output\jobs_latest.md` (and `jobs_<date>.md`): header (run time, window, boards scanned / failed / partial, counts), **Board** (kept roles with confirmed dates, best score first), **Date unconfirmed**, **Hidden** (one line per role with an ID). Each role shows ID, score, track, title, company, city, work mode, posted date, years required, pay, the training phrase, up to 6 requirement lines quoted from the JD, the link and the saved JD path. The IDs are the Excel IDs in `JobBoard_Master.xlsx`; the Board sheet and the md list the same roles. The console summary is 20 lines at most; everything else goes to `logs\board_<date>.log`.

**Safety net.** Every dropped row goes to `output/Dropped_<date>.csv` (complete) and, up to `hidden_sheet_max_rows`, to the Hidden sheet with its step and reason. Date drops are counted, not listed. Rows dropped before the JD stage have no ID and cannot be overridden from Excel; change the config instead. The **Funnel** sheet has counts per company per step plus boards that errored. The log ends with 25 random titles dropped at step 4 and 25 at step 5. `python -m board table --ids 1-17` prints row, company, title, kept/hidden and reason.

**Tests.** `tests/test_regression_2026_10_29.py` (17 real rows, JDs in `tests/fixtures/`, re-baselined for years ≤ 3 and Senior ≤ 4), `tests/test_funnel.py`, `tests/test_score.py`, `tests/test_dates.py`, `tests/test_fetch.py` and `tests/test_board_outputs.py` cover the window edge cases, each rule, the score, the markdown / Board sheet match and the partial-board time limit, and prove that unusual finance titles ("Analyst, Corporate Development", "Treasury Analyst", "Investor Relations Associate") survive. A test fails if the default run calls `claude`. Run `python -m pytest tests -q` before committing.

## Optional Claude gate (`--with-claude`, off by default)

`python -m board run --with-claude` also runs the career-ops gate: a liveness check, a `jd-years.mjs` cross-check, and a headless `claude -p` evaluation (1-5 score and report from `modes/oferta.md`, at most `max_evaluations_per_run` new ones, below `min_score` 3.0 hidden as "career-ops: low fit"). The worker gets Read/Write/Glob/Grep only, because JD text is untrusted. Results are cached in `state/careerops.json`; a failure flags the row and never hides it. The default run never does this.

## Architecture

1. `career-ops/` — clone of https://github.com/career-ops-hq/career-ops, used for the providers the Python fetchers do not cover (Apple, Google, Pinpoint) and for the optional `--with-claude` gate: `node scan.mjs` + `portals.yml` → `scan-history.tsv`. Never edit its source. Keep the clone git-ignored.
   - **Data root: `C:\ClaudeJobBoard\career-ops-data`**, set by the user-level environment variable `CAREER_OPS_ROOT` (`setx`; new shells only). It holds `portals.yml`, `cv.md`, `config/profile.yml`, `modes/`, and `data/` (including `scan-history.tsv`). It is outside version control on purpose. `jobboard.ps1` and `board/` set the variable themselves when they spawn node.
   - `onboard-write.mjs` writes to the checkout by default; always pass `--target C:\ClaudeJobBoard\career-ops-data`.
2. `board/` — Python package: `fetch/` (per-ATS fetchers, date handling, prefilter), `dates` (UTC, America/Chicago window), `jd` (job descriptions, cached in `jobs\<id>_<company>_<title>\JD.md`), `extract` (years, degree, certs, work mode with the source sentence), `filters`, `score`, `pipeline`, `markdown` (`jobs_latest.md`), `excel`, `review`, `applications`, `cli`. `ingest` reads `scan-history.tsv` for the scan.mjs providers only.
3. `jobboard.ps1` / `jobboard.cmd` — the one-command run. `.claude/commands/` — `/jobs`, `/apply`, `/mark`.

State: `state/` (ID ledger `ids.json`, `overrides.json`, `last_scan.json`, `last_run.json`), `output/` (workbooks), `applications/` (PDFs), `jobs/` (JDs), `logs/`. All git-ignored.

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
