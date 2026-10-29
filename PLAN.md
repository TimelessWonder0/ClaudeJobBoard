# PLAN

## Current state

- `C:\ClaudeJobBoard` is a git repo linked to `TimelessWonder0/ClaudeJobBoard` (private). The initial commit holds three PDFs at the repo root.
- There is **no `inputs/` folder**. The three files are at the root: resume (`FP&A_Resume_--_Shreyas_Kukanur (1).pdf`), LinkedIn profile (`Profile (4).pdf`), Harvard guide (`harvard CV and cover letter guide.pdf`). Phase 0 moves them into `inputs/`.
- All three files were read. Notes:
  - Resume and LinkedIn agree on all facts except: JHU end date (resume Jun 2028, LinkedIn Dec 2028) and resume header location (St. Louis, MO vs your San Jose, CA rule).
  - LinkedIn has facts the resume lacks that tailoring can use: trained/onboarded 3 analysts, AI-assisted workflows, FAR/CAS, Spartan Fund buy recommendations approved (ROST, HSY, DLB), Sophia University study abroad, LBO/JV/acquisition strategy at Fintax.
  - The Harvard guide is aimed at PhD CVs and academic cover letters. Reusable parts: active-verb lists (incl. Financial and Achievement lists), reverse-chronological order, fragments not sentences, important content first, tailor each letter. Cover letter structure: opening (role + status) → middle (evidence mapped to the job) → close.
- career-ops layout (checked via GitHub API): `scan.mjs`, `portals.yml` (from `templates/portals.example.yml`), `data/scan-history.tsv` with columns `url, first_seen, portal, title, company, status, location, fingerprint, posted_at, trust_score, trust_flags, normalized_company, requisition_id, language, listing_key`. Providers cover Greenhouse, Lever, Ashby, etc. Workday-hosted boards (many big finance employers) may need extra work; see Risks.

## Decisions

- **JHU end date:** June 2028 (resume date).
- **Boeing line location:** St. Louis County, MO. Header location stays San Jose, CA.
- **Scheduling:** none. No Task Scheduler, no cron. Scans run only on `/scan`.
- **Target companies:** user supplies the list later in the build. Until then `portals.yml` uses career-ops' defaults.
- **Data root:** `C:\ClaudeJobBoard\career-ops-data` via `CAREER_OPS_ROOT` (outside the repo).

## Open questions (need your answer before the phase that needs them)

1. **Applied status in Excel** (Phase 5/6). Type "Applied" directly in the Excel and have `/scan` read it back, or only use `/mark`? Recommendation: support both — `/scan` reads the Status column of the latest board before writing the new one, `/mark` updates the state file and the workbook.
2. **PDF engine** (Phase 6). Use career-ops' `generate-pdf.mjs` (Playwright, HTML templates, already installed) or Python (reportlab / WeasyPrint)? Recommendation: career-ops' Playwright path; Python only builds the HTML.
3. **Compensation placeholder.** `onboard-write.mjs` required a compensation field, so `config/profile.yml` says `target_range: "not specified"`. Tell me a real range if you want it used.

## Phases

### Phase 0 — Prep
- Create `inputs/`, move the 3 PDFs into it (`git mv`).
- Extract text copies to `inputs/resume.txt`, `inputs/profile.txt` (for the scorer and `/apply`; PDFs stay the source).
- Add `.gitignore`: `career-ops/`, `output/`, `state/`, `.cache/`, `__pycache__/`, `.venv/`, `.env`.
- Install check: Node 20+, `git`, Python 3.13 (present), `pip install openpyxl pyyaml httpx beautifulsoup4 pytest`.

### Phase 1 — Discovery (career-ops) — SETUP DONE, first scan pending
Done:
- Cloned career-ops v1.38.0 (shallow) into `career-ops\`, `npm install` (0 vulnerabilities). Playwright chromium was already installed.
- `CAREER_OPS_ROOT=C:\ClaudeJobBoard\career-ops-data` set at user level with `setx` (applies to new shells; in the current shell it must be exported by hand).
- `npm run doctor`: all checks pass. Fixed: repaired the 6 skill-entrypoint stubs (Windows has no symlink support here); created `cv.md` (from the resume text, header San Jose, Boeing St. Louis County, JHU Jun 2028), `config/profile.yml`, `portals.yml`, `modes/_profile.md`, `_custom.md`, `_brief.md`, `voice-dna.md` in the data root. `validate-portals.mjs`: 0 errors, 0 warnings.
- Left as warnings on purpose: (1) web UI deps not installed (we do not use `npm run ui`); (2) Playwright MCP not configured (only the agent-driven scan needs it; `scan.mjs` does not); (3) `modes/_profile.md` and `_brief.md` are still templates (they only steer career-ops' own A-F evaluations, which we replace with `board/score.py`).
- `portals.yml` as written: `title_filter.positive` = financial/finance analyst, FP&A, financial planning, pricing, deal finance/desk, equity research, research associate, corporate/strategic/business finance, cost analyst. `negative` adds director, manager, VP, vice president, head of, quant, quantitative, intern. `location_filter.allow` = the metro cities (Bay Area cities, Seattle area, Austin, Chicago, New York, Boston, Cambridge MA, Miami, Honolulu/Hawaii). `max_posting_age_days: 4`. `experience_filter: {stretch_years: 1.5, action: label}` (labels, never drops).
To do:
- Run `node scan.mjs --dry-run`, then a real scan, then inspect `scan-history.tsv`. Record how often `posted_at` is empty per provider (decides the date fallback in Phase 3).
- Replace the default company list when the user sends theirs.

#### How career-ops scanning works (summary of README, modes/scan.md, docs/AUTOMATION.md, portals.example.yml, scan.mjs)

`node scan.mjs` is zero-token: it reads public ATS feeds (Greenhouse, Ashby, Lever, Workday, BambooHR, Teamtailor, Breezy, and others in `providers/`) plus optional per-company local parsers. It does not use an LLM or Playwright unless `--verify` is passed. Companies with no parser or supported ATS are silently skipped by `scan.mjs` (the agent-driven scan covers them with Playwright/WebSearch; we are not using that). Config is `portals.yml` in the data root.

Pipeline inside one run: provider fetch → `title_filter` → `location_filter` → posting-age → salary/content/visa/country filters → dedup → (optional) experience label, liveness verify → append `data/pipeline.md` + `data/scan-history.tsv`.

| Setting | Where | What it does |
|---|---|---|
| `title_filter.positive` | portals.yml | At least one keyword must appear in the title (case-insensitive substring). Keywords of 2-3 letters ("VP", "AI") match on word boundaries. Prefixes `word:` (whole word) and `stem:` (must start a word) are available. |
| `title_filter.negative` | portals.yml | Any match rejects. Beware substring hits: bare `Intern` also kills "Internal"/"International", hence `word:Intern`. |
| `title_filter.seniority_boost` | portals.yml | Priority only, never required. |
| `skip_tiers` | portals.yml (global or per company) | Drops by seniority tier (intern, entry...). Not enabled for us; entry-level roles are wanted. |
| `location_filter` | portals.yml | Order: empty location → pass; `block_hard` → reject; `always_allow` → pass; `block` → reject; empty `allow` → pass; else must match an `allow` keyword. Substring, case-insensitive. `strict: true` rejects empty locations (not set; we want missing locations shown with a flag). Our config is allow-list only (cities). A posting labeled just "Remote" fails the allow list, which also hides fully-remote roles. Multi-location strings that name a target city pass. |
| `max_posting_age_days` | portals.yml | Skips a posting only if the provider supplied `postedAt` AND it is older than N days. Undated postings always pass. Applies to every source. We set 4. |
| `--since <days>` | CLI | Same bound, relative. Also stops Workday-style providers from paging past the window. Combines with `--posted-after` and `max_posting_age_days`: bounds AND together and the newest wins. Off by default. Invalid values exit with an error. Early stop can cut off undated postings on mixed Workday tenants. |
| `--posted-after / --posted-before YYYY-MM-DD` | CLI | Absolute inclusive date window. |
| `--json` | CLI | Sends all normal logging to stderr and prints one receipt on stdout: `{version: "careerops.scan.receipt@1", date, scanned, skipped, found, filtered, duplicates, added, added_urls[], errors[{company,error}], unverified_zero, dry_run}`. Exit codes: 0 ok, 1 config/fatal, 2 every target failed (with `--json`: any target failed). |
| other flags | CLI | `--dry-run` (no writes), `--company <name>`, `--verify` (liveness via API, Playwright fallback), `--include-blacklisted`, `--quiet`. |
| `experience_filter` | portals.yml | Reads each new JD's stated minimum years; labels `above band` (or drops with `action: drop`). Floors ranges ("3-5" = 3). Preferred-only, skill-specific and "or equivalent" bars are not counted. Costs one JD request per new posting. |
| `content_filter`, `visa_filter`, `salary_filter`, `country_eligibility_filter` | portals.yml | Optional; unused. |

Dedup: URLs in `scan-history.tsv` are never re-added (rows with `skipped_title/location/age/...` statuses carry no dedup weight, so loosening a filter brings them back). `scan_history.recheck_after_days` can re-admit old URLs.

Not part of scan.mjs but in the repo: `docs/AUTOMATION.md` shows cron/launchd/Task Scheduler recipes (we are not using them) and a zero-token triage prompt over `pipeline.md` (we replace it with `board/`).

#### `data/scan-history.tsv` columns (append-only; read by name, rows may be short)

Location: `C:\ClaudeJobBoard\career-ops-data\data\scan-history.tsv`. Tab-separated; header written once at file creation and may be shorter than newer rows. Cells that would start a spreadsheet formula (`=`, `+`, `-`, `@`) get one extra leading `'`; read rows through career-ops' `parseScanHistoryLine` logic (strip that one apostrophe).

| # | Column | Notes |
|---|---|---|
| 1 | `url` | Canonical posting URL. |
| 2 | `first_seen` | ISO date our scanner first saw it (not the employer's posting date). |
| 3 | `portal` | Source name from portals.yml. |
| 4 | `title` | Title as returned by the ATS. |
| 5 | `company` | Company name. |
| 6 | `status` | `added`, `skipped_dup`, `skipped_title`, `skipped_expired`, `skipped_location`, `skipped_age`, `skipped_no_apply_control`, `skipped_content`, `skipped_visa`, `skipped_country_eligibility`, `skipped_experience`, `skipped_date`, `skipped_invalid_url`, `skipped_blocked_host`, `cooldown:{company}:{until}`. |
| 7 | `location` | Location string; may be empty. |
| 8 | `fingerprint` | 64-bit SimHash of the JD (16 hex); empty without a body (Lever ships one; most providers do not). |
| 9 | `posted_at` | ISO date the employer posted it; empty when the provider gives none. This is the real age signal. |
| 10 | `trust_score` | Only when the scanner flagged the posting (<100). |
| 11 | `trust_flags` | Comma-joined flags, same condition. |
| 12 | `normalized_company` | Canonical company key. |
| 13 | `requisition_id` | Employer req id when the provider has one. |
| 14 | `language` | Posting language when reported. |
| 15 | `listing_key` | Strong ATS identity key (`listing_v1_…`); blank when unavailable. Preferred input for our `job_id`. |

Implications for `board/`: the TSV holds no JD text and no experience/degree info, so `board.jd` must fetch descriptions itself (career-ops has `fetch-jd.mjs <url>` for supported ATSs: prints JD on stdout, exit 1 when it cannot). `skipped_title`, `skipped_location` and `skipped_age` rows are the career-ops-side "hidden with reason" records; `board` merges them into the Hidden sheet. Because career-ops already drops by title/location/age, `board` re-checks the same rules on `added` rows (cheap, and catches rules career-ops does not know, such as quant JD text or degree required).

### Phase 2 — `board` package: ingest
- `board/ingest.py`: read scan-history.tsv by column name, keep rows with status "added"/new, dedupe by `listing_key`/`fingerprint`, assign stable `job_id`.
- `board/jd.py`: fetch the job description page (or provider API field), cache to `.cache/jd/<job_id>.json`, extract text, posted date, location lines, remote/hybrid hints. Rate-limited, retried, failures logged (job stays on board with a "JD not fetched" flag).

### Phase 3 — Filters
`board/filters.py`, one pure function per rule, each returns `None` (pass) or a reason string. All reasons are written to the Hidden sheet.

| Rule | Detection | Ambiguity → flag, not hide |
|---|---|---|
| Quant | title/JD: quantitative, quant researcher/trader/developer/analyst | "quantitative analysis" in a generic FP&A JD |
| Degree required | "Master's/MBA required", "advanced degree required"; ignore "preferred", "or equivalent" | unclear wording |
| Years required | regex on required/qualifications section; min > 3 hides ("5+ years") | "3-5 years", years only under "preferred" |
| Title | director, manager, VP, vice president, intern | "Senior", "Lead", "Principal" |
| Metro | city/state map: Bay Area (SF, San Jose, Oakland, Palo Alto, Mountain View, Sunnyvale, Santa Clara, Cupertino, Menlo Park, etc.), Seattle/Bellevue/Redmond, Austin, Chicago, New York (+NJ/CT nearby: flag), Boston/Cambridge, Miami, Honolulu/Hawaii | missing location → show with flag |
| Remote | "remote" with no hybrid/on-site; JD says fully remote | "remote-friendly", multiple locations incl. a target metro |
| Age | `posted_at` > 4 days | `posted_at` empty → use JD page date; else `first_seen` and flag "date unverified" |

Unit tests with fixture JDs for each rule, including the ambiguous cases.

### Phase 4 — Scoring
`board/score.py`, 0–100, deterministic, explained per job in a "Why" column.

- Title fit (30): Financial Analyst / FP&A / Pricing / Deal Finance / ERA exact or close.
- Skill/keyword overlap with resume + profile (30): forecasting, variance, pricing, cost, budgeting, DCF, Excel/VBA/Power Query, SQL, Python, scenario analysis, cost accounting, GAAP.
- Seniority fit (15): required years 0–2 best, 3 ok; flags for "Senior".
- Domain fit (15): pricing, cost, contracts, government/aerospace, tech FP&A.
- Location/work mode (10): hybrid/on-site in a target metro; Bay Area weighted highest because materials say San Jose.
- Flags (not score): degree preferred, clearance mentioned, date unverified, JD not fetched, ambiguous years, "Senior" title.
- Weights live in `board/config.yml` so you can tune them without code changes.

### Phase 5 — Excel output
`board/excel.py` → `board_output/job_board_YYYY-MM-DD.xlsx` (one file per day).
- Sheet **Board**: ID, Score, Company, Title, Location, Posted, Days Old, Work Mode, Flags, Why, URL (hyperlink), Status (dropdown: New, Interested, Applied, Interview, Rejected, Skip), Notes. Frozen header, autofilter, sorted by score, conditional color on score.
- Sheet **Hidden**: ID, Company, Title, Location, URL, Reason.
- Sheet **Run**: scan date, rows scanned, new, shown, hidden by reason, fetch failures.
- Status and Notes carry over from the previous board by `job_id`, so Applied persists across days.

### Phase 6 — State + slash commands
- `state/jobs.json` (or SQLite): per-job status, notes, first/last seen, links to output PDFs. Source of truth; Excel Status is read back at each `/scan` (open question 3).
- `.claude/commands/scan.md` → `/scan`: run career-ops `scan.mjs`, then `python -m board run`, then report counts + top 10 and the path to the workbook.
- `.claude/commands/mark.md` → `/mark <id> <status>`: validate id/status, update state and workbook, confirm.
- `.claude/commands/apply.md` → `/apply <id>`: load JD from cache, run `python -m board apply <id>` to produce the fact pack (JD requirements ↔ resume facts mapping), then Claude writes tailored resume + cover letter text from that pack following CLAUDE.md rules, renders PDFs to `output/<job_id>/`, and lists any JD requirement I could not truthfully claim. Does not submit anything.

### Phase 7 — Hardening
- pytest suite, a sample-data smoke test, README with run instructions.
- No scheduler (decided).
- `portals.yml` maintenance: weekly check of dead boards via career-ops `portal-health.tsv`.

## Board package (built: ingest, JD fetch, extract, filters, score)

Run: `python -m board run [--today YYYY-MM-DD] [--limit N] [--refresh] [--no-fetch] [--history PATH] [--out DIR]`. It needs `career-ops-data\data\scan-history.tsv`, which only exists after a real (non-dry) `node scan.mjs`. Output: `output\run_<date>.json` (shown and hidden rows with reasons and evidence), a console summary, and the Excel files below. All four commands are built in `.claude\commands\`: `/scan` (career-ops scan with `--json` into `state\last_scan.json`, then `python -m board run`, then `/review`), `/mark <id> <status>` (`python -m board mark`, patches the master and today's snapshot in place), `/apply <id>` (`python -m board apply prepare | render | finish`; wording comes from Claude, but employers, titles, dates, places and degrees are printed from `config\candidate.yaml`, and any number, skill or credential it does not support is rejected before a PDF is written; PDFs are rendered by Playwright via `board\render_pdf.mjs`, one page each, into `applications\<date>_<company>_<title>\` with `GapCheck.md`), and `/review`. The resume PDFs now live in `inputs\`. `/review` is built (`.claude\commands\review.md`, `board/review.py`): `python -m board review prepare` picks the top 25 reviewable rows (Status New/Interested/Docs Ready, not yet reviewed) and writes `state\review_packet.json`; Claude reads each JD.md plus `career-ops-data\cv.md` and writes `state\review_input.json`; `python -m board review apply` validates it and patches the master and today's snapshot. A score change needs a reason, a verbatim JD quote (checked against JD.md) and at most 25 points of movement, or the rule score stays.

### Excel (`board/excel.py`, built)

- Writes `output\JobBoard_YYYY-MM-DD.xlsx` (snapshot) and `output\JobBoard_Master.xlsx` (the file you edit). Sheets: Board, Hidden, Check Manually, Run Log, laid out per the spec you gave (frozen header, filters, Fit Score green 70+ / yellow 50-69 / grey below 50, Applied and Skip rows greyed, new-since-last-run rows bold, Status dropdown).
- Before rewriting, the run reads the master by header name. Status, Applied Date, Resume, Cover Letter, Notes survive, matched by the numeric ID. Roles you marked (any status other than New, or any note) are kept even after they leave the 4-day window or a later filter would hide them; untouched New rows are dropped after 14 days.
- IDs are small integers from `state\ids.json` (never reused). A hidden `Key` column in the master lets the ledger be rebuilt if `state\` is lost.
- Locked file: if the master or snapshot is open in Excel, the run saves `JobBoard_Master_YYYY-MM-DD_HHMMSS.xlsx` next to it, leaves the locked file untouched, and prints a note. If the master cannot be read at all it is never overwritten; only the snapshot is written.
- Hidden sheet has an Override column (dropdown "Show"). Marking a row moves it to the Board on the next run and the choice sticks (`state\overrides.json`).
- Hooks for later steps: `state\reviews.json` (`{"14": {"score": 78, "why": "...", "gaps": "..."}}`) replaces the rule score and fills Why it fits / Gaps; PDFs in `applications\<ID>_*\` whose names contain "resume" or "cover letter" are linked automatically; `state\last_scan.json` (a `node scan.mjs --json` receipt) feeds the Run Log's companies scanned and errored boards.
- Deviation from the spec: "Resume / Cover Letter" is two columns (Resume, Cover Letter), because one cell can hold only one link.

Modules (`board/`): `ingest.py` (TSV by column name, window selection, stable 8-hex job id), `jd.py` (fetch-jd.mjs, then Apple and Oracle HCM fetchers, then HTML/JSON-LD; cache in `jobs\<id>_<company>_<title>\JD.md` + `facts.json`), `extract.py` (years, degree, certs, work mode, each with its source sentence), `filters.py` (hard filters), `score.py` (0-100), `pipeline.py`, `cli.py`.

Config: `config\scoring.yaml` (weights, tiers, skills, metro and priority scores, adjustments), `config\filters.yaml` (window, years limit, title patterns, quant patterns, metro map).

Extractor rules worth knowing:
- Required vs preferred comes from headings (Basic/Minimum/Required vs Preferred/Nice to have/Beneficial) and inline words. Confidence: heading + explicit word = high, generic heading = medium, no heading = low. Only high and medium can hide a job; low only flags.
- Several years figures: the lowest required bar is the minimum. Degree routes ("Bachelor's + 3 or Master's + 1", "7 years, or 5 with an advanced degree") keep both numbers: the minimum is the lowest, and `years_bachelors` is the route for a BS holder (used for scoring).
- Years tied to one tool ("3+ years of SQL") are recorded but not counted. "5 years old", "past 5 years", "100 years" are ignored.
- "or an advanced degree" after a years figure is a route, not a Master's requirement. "MBA, graduate degree, or CFA" is a credential menu, not an MBA requirement.
- Certs: required / obtainable after hire / preferred / mentioned. Required certs flag and cost 8 points; they do not hide.
- Work mode: only explicit phrases count (hybrid, N days in office, fully remote, remote-first, on-site). Office-setting boilerplate does not.

Tested on real postings (Google, Amazon, Apple, Visa, Citi, Wells Fargo, JPMorgan, NVIDIA, Northern Trust): 27 test rows, 17 shown, 10 hidden for years, 1 outside the metros. Known gap: HTML-only pages (Google, Amazon) rarely state work mode, so most rows show "work mode unknown".

## Company resolution (done)

Source list: `config/companies.yaml` (69 companies: name, category, metros, priority, the user's own board hint, and, after resolution, `careers_url`, `ats`, `scan_status`). Output: `C:\ClaudeJobBoard\career-ops-data\portals.yml` with 49 enabled entries (Amazon has two). The default career-ops company list, search queries and job boards were removed; the original is kept as `portals.yml.default-backup`.

How each board was resolved:
- `node discover-ats.mjs` for Greenhouse, Ashby, Pinpoint and Workday (`--write`). Name-only probing gave wrong matches (Google to a Breezy board, Microsoft/BlackRock/Zillow to BambooHR boards, Ramp to a 1-job Greenhouse stub), so each company was probed per vendor with explicit slugs or Workday coordinates, and the wrong matches were not written.
- By hand in `portals.yml`, because discover cannot resolve them: Apple, Google, Amazon (career-ops providers), Microsoft and Netflix (Eightfold), JPMorgan Chase and Oracle (Oracle HCM).
- `npm run validate:portals`: 0 errors, 0 warnings. `npm run verify:portals`: 49 live, 0 live-but-empty, 0 unresolved, 0 no-provider. (A Workday "20 live" is only the first page, not the board total.)

Where the user's hint was wrong: Plaid is on Ashby (hint said Lever); Visa is on Workday (hint said SmartRecruiters); Ramp is on Ashby as hinted but name-only probing found a decoy Greenhouse board; CFRA has a small Pinpoint board (hint said manual; 5 jobs, one board of unconfirmed ownership beyond its job titles being CFRA-like finance research roles).

### Board table

| Company | ATS | Board URL | Status |
|---|---|---|---|
| Apple | Apple (career-ops provider) | https://jobs.apple.com/en-us/search | OK |
| Google | Google (career-ops provider) | https://www.google.com/about/careers/applications/jobs/results/ | OK |
| Amazon | amazon.jobs (career-ops provider) | https://www.amazon.jobs/en/search | OK |
| Microsoft | Eightfold | https://apply.careers.microsoft.com/careers?domain=microsoft.com | OK |
| NVIDIA | Workday | https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite | OK |
| Adobe | Workday | https://adobe.wd5.myworkdayjobs.com/external_experienced | OK |
| Salesforce | Workday | https://salesforce.wd12.myworkdayjobs.com/External_Career_Site | OK |
| PayPal | Workday | https://paypal.wd1.myworkdayjobs.com/jobs | OK |
| Netflix | Eightfold | https://explore.jobs.netflix.net/careers | OK |
| Visa | Workday | https://visa.wd5.myworkdayjobs.com/Visa | OK |
| Meta | own site | https://www.metacareers.com/jobsearch/ | not scannable |
| Intel | Workday | https://intel.wd1.myworkdayjobs.com/External | OK |
| Cisco | Workday | https://cisco.wd5.myworkdayjobs.com/Cisco_Careers | OK |
| Intuit | own site | https://jobs.intuit.com/ | not scannable |
| Uber | own site | https://www.uber.com/us/en/careers/ | not scannable |
| Workday | Workday | https://workday.wd5.myworkdayjobs.com/Workday | OK |
| OpenAI | Ashby | https://jobs.ashbyhq.com/openai | not scannable |
| Anthropic | Greenhouse | https://job-boards.greenhouse.io/anthropic | OK |
| Stripe | Greenhouse | https://job-boards.greenhouse.io/stripe | OK |
| Databricks | Greenhouse | https://job-boards.greenhouse.io/databricks | OK |
| Robinhood | Greenhouse | https://job-boards.greenhouse.io/robinhood | OK |
| Airbnb | Greenhouse | https://job-boards.greenhouse.io/airbnb | OK |
| Pinterest | Greenhouse | https://job-boards.greenhouse.io/pinterest | OK |
| DoorDash | Greenhouse | https://job-boards.greenhouse.io/doordashusa | OK |
| Figma | Greenhouse | https://job-boards.greenhouse.io/figma | OK |
| Brex | Greenhouse | https://job-boards.greenhouse.io/brex | OK |
| Affirm | Greenhouse | https://job-boards.greenhouse.io/affirm | OK |
| SoFi | Greenhouse | https://job-boards.greenhouse.io/sofi | OK |
| Plaid | Ashby | https://jobs.ashbyhq.com/plaid | OK |
| Ramp | Ashby | https://jobs.ashbyhq.com/ramp | OK |
| Chime | Greenhouse | https://job-boards.greenhouse.io/chime | OK |
| Scale AI | Greenhouse | https://job-boards.greenhouse.io/scaleai | OK |
| Expedia | Workday | https://expedia.wd108.myworkdayjobs.com/search | OK |
| Zillow | Workday | https://zillow.wd5.myworkdayjobs.com/Zillow_Group_External | OK |
| T-Mobile | Workday | https://tmobile.wd1.myworkdayjobs.com/External | OK |
| Dell | Oracle HCM on dell.com host | https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers | not scannable |
| Oracle | Oracle HCM | https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/jobs | OK |
| BlackRock | Workday | https://blackrock.wd1.myworkdayjobs.com/BlackRock_Professional | OK |
| JPMorgan Chase | Oracle HCM | https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/jobs | OK |
| Goldman Sachs | own site | https://www.goldmansachs.com/careers/ | not scannable |
| Morgan Stanley | own site | https://www.morganstanley.com/people | not scannable |
| Fidelity | Workday | https://fmr.wd1.myworkdayjobs.com/FidelityCareers | OK |
| S&P Global | Workday | https://spgi.wd5.myworkdayjobs.com/SPGI_Careers | OK |
| Bank of America | Workday | https://ghr.wd1.myworkdayjobs.com/lateral-us | OK |
| Wells Fargo | Workday | https://wf.wd1.myworkdayjobs.com/WellsFargoJobs | OK |
| Citi | Workday | https://citi.wd5.myworkdayjobs.com/2 | OK |
| Capital One | Workday | https://capitalone.wd12.myworkdayjobs.com/Capital_One | OK |
| State Street | Workday | https://statestreet.wd1.myworkdayjobs.com/Global | OK |
| Northern Trust | Workday | https://ntrs.wd1.myworkdayjobs.com/northerntrust | OK |
| Mastercard | Workday | https://mastercard.wd1.myworkdayjobs.com/CorporateCareers | OK |
| Moody's | unknown | https://careers.moodys.com/en | not scannable |
| Morningstar | Workday | https://morningstar.wd5.myworkdayjobs.com/Americas | OK |
| William Blair | unknown | https://www.williamblair.com/careers | not scannable |
| Piper Sandler | unknown | https://www.pipersandler.com/careers | not scannable |
| Baird | own site | https://www.bairdcareers.com/ | not scannable |
| Raymond James | Workday | https://raymondjames.wd1.myworkdayjobs.com/RaymondJamesCareers | OK |
| FactSet | Workday | https://factset.wd108.myworkdayjobs.com/FactSetCareers | OK |
| PitchBook | unknown | https://pitchbook.com/careers | not scannable |
| Bloomberg | Avature | https://bloomberg.avature.net/careers | not scannable |
| Jefferies | unknown | https://www.jefferies.com/careers/ | not scannable |
| Evercore ISI | unknown | https://www.evercore.com/careers/ | not scannable |
| AlphaSense | Greenhouse | https://job-boards.greenhouse.io/alphasense | OK |
| Needham | unknown | https://www.needhamco.com/ | not scannable |
| Wolfe Research | unknown | https://www.wolferesearch.com/ | not scannable |
| CFRA | Pinpoint | https://cfra.pinpointhq.com | OK |
| Zacks | unknown | https://www.zacks.com/ | not scannable |
| Bank of Hawaii | unknown | https://www.boh.com/careers | not scannable |
| First Hawaiian Bank | unknown | https://www.fhb.com/en/careers | not scannable |
| Royal Caribbean | unknown | https://www.royalcaribbeangroup.com/careers | not scannable |

### Check manually (not scannable)

| Company | Careers page | Why |
|---|---|---|
| OpenAI | https://jobs.ashbyhq.com/openai | Board is real (Ashby), but its API reply is 14 MB, over the scanner's 10 MB cap. Kept in portals.yml with enabled: false. |
| Meta | https://www.metacareers.com/jobsearch/ | No public API. |
| Uber | https://www.uber.com/us/en/careers/ | No ATS board found (only a 1-job SmartRecruiters stub). Link not confirmed (HTTP 406 to curl). |
| Intuit | https://jobs.intuit.com/ | No Workday/Greenhouse/Lever board found. |
| Dell | https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers | Oracle HCM on Dell's own host; the oraclecloud provider only accepts oraclecloud.com hosts. Workday guess failed. |
| Moody's | https://careers.moodys.com/en | Workday guesses failed; no other board found. |
| Goldman Sachs | https://www.goldmansachs.com/careers/ | No scannable board found. Link not confirmed (no response); search site is https://higher.gs.com/ |
| Morgan Stanley | https://www.morganstanley.com/people | No public API. /careers redirects here. |
| William Blair | https://www.williamblair.com/careers | No board found. |
| Piper Sandler | https://www.pipersandler.com/careers | No board found. |
| Baird | https://www.bairdcareers.com/ | No board found. |
| PitchBook | https://pitchbook.com/careers | No board found. Link not confirmed (HTTP 403 to curl). |
| Bloomberg | https://bloomberg.avature.net/careers | Avature has no career-ops provider. |
| Jefferies | https://www.jefferies.com/careers/ | No board found. |
| Evercore ISI | https://www.evercore.com/careers/ | No board found. Link not confirmed (HTTP 403 to curl). |
| Needham | https://www.needhamco.com/ | No board found; careers path not found, homepage only. |
| Wolfe Research | https://www.wolferesearch.com/ | No board found; careers path not found, homepage only. |
| Zacks | https://www.zacks.com/ | No board found; /careers returns 404, homepage only. |
| Bank of Hawaii | https://www.boh.com/careers | No board found. |
| First Hawaiian Bank | https://www.fhb.com/en/careers | No board found. |
| Royal Caribbean | https://www.royalcaribbeangroup.com/careers | No board found; Workday guesses failed. Link not confirmed (HTTP 500 when checked). |

Notes: "Link not confirmed" means the page returned an error or blocked the check; open it by hand. Rechecking later: Dell, Moody's, Royal Caribbean and Intuit may be on Workday or Oracle HCM boards under tenant names I could not guess; send the board URL from your browser's address bar and discover will confirm it.

## Files

```
C:\ClaudeJobBoard\
  CLAUDE.md
  PLAN.md
  README.md                  (phase 7)
  requirements.txt
  .gitignore
  inputs\                    resume, profile, Harvard guide (+ .txt extracts)
  career-ops\                clone, git-ignored (system code only)
  career-ops-data\           CAREER_OPS_ROOT, outside the repo's tracking (git-ignored)
    portals.yml  cv.md  config\profile.yml  modes\
    data\scan-history.tsv    discovery output
  board\
    __init__.py  __main__.py  cli.py
    config.yml               metros, keywords, weights
    ingest.py  jd.py  filters.py  score.py  excel.py  state.py  apply.py
    templates\               resume.html, cover_letter.html
  tests\                     fixtures + test_filters.py, test_score.py, test_ingest.py
  .claude\commands\          scan.md  apply.md  mark.md
  .cache\jd\                 fetched job descriptions (git-ignored)
  state\jobs.json            status ledger (git-ignored)
  board_output\              job_board_YYYY-MM-DD.xlsx (git-ignored)
  output\<job_id>\           tailored PDFs (git-ignored)
```

## Data flow

```
/scan
  └─ node career-ops/scan.mjs --json     (CAREER_OPS_ROOT/portals.yml → provider APIs)
       └─ career-ops-data/data/scan-history.tsv
            └─ board.ingest              parse by column name, dedupe, job_id
                 └─ board.jd             fetch + cache JD text, posted date, location
                      └─ board.filters   hide + reason | pass + flags
                           └─ board.score  0-100 + "Why"
                                └─ board.excel  merge status from state/prior board
                                     └─ board_output/job_board_<date>.xlsx

/mark <id> <status> ─► state/jobs.json ─► workbook Status cell
/apply <id> ─► .cache JD + inputs/ facts ─► fact pack ─► Claude drafts ─► HTML ─► PDF
              └─► output/<job_id>/Kukanur_<Co>_<Role>_{Resume,CoverLetter}.pdf
```

## Risks

- **posted_at is often empty** in scan-history for some providers → date filter relies on the JD page date, falls back to `first_seen`, and flags it. First-run results will show how common this is.
- **Workday and other JS-heavy boards** (common for big finance employers) may not be covered by career-ops providers; may need `--verify` Playwright or a custom provider. Decide after the first real scan.
- **Years/degree regex errors** are the main hide-wrongly risk. Mitigation: Hidden sheet shows the matched text so you can audit it; ambiguous cases are flagged, not hidden.
- **Resume PDFs are in the git history** (first commit). Repo is private. If it ever goes public, history must be scrubbed.
- **ToS / rate limits**: scanning is limited to career-ops' public ATS feeds; no LinkedIn scraping.

## What I will do next

Phase 1 setup is done. Next, on your go: Phase 0 leftovers (move PDFs to `inputs/`, `.gitignore`), then `node scan.mjs --dry-run` and a first real scan to measure how often `posted_at` is empty. The company list can wait until you send it.
