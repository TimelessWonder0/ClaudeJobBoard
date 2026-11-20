# ClaudeJobBoard

A personal job board for finance roles. One command scans 49 career sites for the companies in `config/companies.yaml`, keeps the roles posted in the last 3 days that a Bachelor's-only candidate with about 1.5 years can get (finance and finance-adjacent; up to 2 years required; 8 metros; on-site or hybrid), scores them against a resume, and writes an Excel board you can sort and mark. When you pick a role, it builds a tailored one-page resume and cover letter as PDFs.

It never submits an application and never contacts anyone. You decide and send.

## What you get

- `output\JobBoard_Master.xlsx`: the file you work in. Sheets: **Board** (roles that passed the filters, best Fit Score first), **Hidden** (filtered roles with the reason and the sentence it came from, so you can override), **Check Manually** (companies with no scannable API, with careers links), **Run Log**.
- `output\JobBoard_YYYY-MM-DD.xlsx`: a snapshot of each run.
- Status, Applied Date, Resume, Cover Letter and Notes that you set in the master survive every run (matched by the ID column).
- `applications\<date>_<company>_<title>\`: resume PDF, cover letter PDF, `GapCheck.md`.

## Run it

From any PowerShell window:

```powershell
jobboard              # fetch, filter, score; writes output\jobs_latest.md and the Excel board; prints a 20-line summary
jobboard -Open        # same, then open JobBoard_Master.xlsx in Excel
jobboard -Only Stripe,greenhouse   # fetch only these companies or ATS names (testing)
jobboard -Full        # pull every posting from Workday / Eightfold / Oracle / SmartRecruiters / Amazon (slow, testing)
jobboard -SkipProviders             # skip the node scan.mjs step for Apple, Google and Pinpoint (that step runs by default and is slow)
```

Or double-click `jobboard.cmd`. In Claude Code, open the board with `/jobs`. Claude never runs or waits on the pipeline; you run `jobboard`.

`jobboard.ps1` first refreshes Apple, Google and Pinpoint with `node scan.mjs --company <name>` (skip with `-SkipProviders`), then runs `python -m board run`: no Claude calls, no tokens. Python fetches the ATS APIs, filters, scores and writes `output\jobs_latest.md` (the file Claude reads), `output\jobs_<date>.md`, `output\JobBoard_Master.xlsx` and a dated snapshot. The posting window (4 days) is `days` in `config\filters.yaml`.

- Everything is logged to `logs\<date>.log`; the long detail (every board, the funnel, 25 sample drops) is in `logs\board_<date>.log`.
- If every board fails (no internet, VPN, proxy), the run exits with code 2 and tells you to check your connection.
- If `JobBoard_Master.xlsx` is open in Excel, the script warns you to close it and waits (up to 5 minutes). If it stays open, the board saves a timestamped copy next to it and says so.
- Exit codes: 0 ok, 1 the run failed, 2 every board failed, 3 a prerequisite is missing or the workbook could not be written.
- Nothing is scheduled. There is no Task Scheduler entry; you run it when you want.

### Slash commands (Claude Code, run from this folder)

| Command | What it does |
|---|---|
| `/jobs` | Reads `output\jobs_latest.md` (warns if it is not from today, never scans), skips roles already reviewed or with a Status other than New ("Previously reviewed: yes" in the file), gives the top 30 remaining Board and top 10 remaining Date unconfirmed roles a verdict (Apply now / Strong / Stretch / Skip) with a one-line why and gap, and writes `output\reviews\Claude_Review_<date>.xlsx` through `board/write_review.py`. |
| `/apply <id>` | Builds the resume and cover letter PDFs for that role, writes `GapCheck.md`, puts the paths in Excel, sets Status = Docs Ready. |
| `/mark <id> <status>` | Sets Status (New, Interested, Docs Ready, Applied, Interviewing, Rejected, Offer, Skip); Applied also sets Applied Date. |

## How roles are judged

The board does not search for finance words. It pulls every job at every company, then removes what clearly does not fit, cheapest steps first (`config\funnel.yaml`, `config\negative_titles.yaml`). A good posting with an unusual title survives unless a stated rule removes it.

| Step | What happens |
|---|---|
| 1 Pull everything | career-ops reads whole boards with no search words and no title or location filter. |
| 2 Date | Posted within 3 days. Undated postings stay if first seen within 3 days ("date approx"). |
| 3 Location | Your 8 metros, plus multi-location postings that include one. Fully remote and other places are dropped. |
| 4 Negative titles | Dropped if the title contains a listed whole word: senior, sr, lead, principal, staff, manager, director, head, vp, president, chief, officer, partner (not "business partner"), III and above, engineer, developer, software, scientist, intern, recruiter, account executive and so on. Edit `config\negative_titles.yaml`. |
| 5 Finance relevance | Kept if the title (or ATS department) mentions finance, FP&A, planning, corporate development, M&A, strategy, business operations, pricing, deal desk, revenue, treasury, investor relations, investment, equity, research, valuation, capital, credit, risk analyst or commercial analyst. Accounting, audit, tax and payroll go to Hidden as "accounting track". Everything else is classified by Claude (batches of 50, keep or drop; if it cannot answer, the title is kept). |
| 6 Experience | The JD is fetched only now, for survivors. Required years above 2 are dropped (2 = "stretch", "not stated" = flagged); "Bachelor's + 3 or Master's + 1" counts as 3; career-ops `jd-years.mjs` cross-checks and the higher number wins. Also dropped: MBA, Master's, CPA or CFA required; Series 7/63/65/66 required before hire (licenses sponsored after hire are kept and flagged); a new-grad window that excludes a December 2024 graduate. |
| 7 Fit | career-ops liveness check drops closed postings; a career-ops evaluation (1-5 score and report, `config\careerops.yaml`) runs on survivors, at most 25 new ones per run; below 3.0 is hidden as "career-ops: low fit". The Board is sorted by career-ops score, then Fit Score. |

- **Nothing disappears.** Every dropped row is on the **Hidden** sheet with its step and reason (and in `output\Dropped_<date>.csv`, which is always complete). The **Funnel** sheet shows counts per company after each step, the scan's totals, and boards that errored. Each run ends by printing 25 random titles dropped at step 4 and 25 at step 5 so you can spot anything wrongly filtered. Rows dropped before the JD stage (steps 2-5) cannot be overridden from Excel; change the config.
- **Fit Score 0 to 100:** title track, years vs about 1.5 (a low requirement is rewarded, "stretch" costs points), skill overlap with the resume, metro (Bay Area highest), company priority, freshness. Weights are in `config\scoring.yaml`.
- **Facts only:** application documents are built from `config\candidate.yaml`. Employers, titles, dates, places and degrees are printed by the tool; any number, skill or credential it does not support is rejected before a PDF is written. Review both PDFs before you send them.
- **Limits worth knowing:** Google lets a scanner read only the first results page; Apple's search needs a term (the scan uses "a"); career-ops does not store ATS departments, so the department test works only where the URL carries one. The first run is slow (whole boards, then a JD per survivor); later runs only touch new postings.

## Setup (once)

Requirements: Windows 10/11, Windows PowerShell 5.1, Node 22+, Python 3.12+, Excel, and optionally Claude Code for `/jobs`, `/apply`, `/mark`.

1. Clone this repo to `C:\ClaudeJobBoard`.
2. `pip install -r requirements-dev.txt`
3. `git clone https://github.com/career-ops-hq/career-ops career-ops`, then `cd career-ops`, `npm install`, `npx playwright install chromium`. (career-ops is git-ignored and never edited.)
4. Create the data folder `career-ops-data\` and set the environment variable once: `setx CAREER_OPS_ROOT "C:\ClaudeJobBoard\career-ops-data"`. It holds `portals.yml`, `cv.md`, `config\profile.yml`, `modes\` (see `PLAN.md` for how they were generated) and the scan history. It is git-ignored.
5. Put the `jobboard` command in your PowerShell profile (add to `$PROFILE`):
   `function jobboard { & 'C:\ClaudeJobBoard\jobboard.ps1' @args }`
6. If PowerShell refuses to run the script, allow local scripts for your user only: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. `jobboard.cmd` already runs with `-ExecutionPolicy RemoteSigned`.

## Layout

```
jobboard.ps1 / jobboard.cmd   the one-command run
board/                        Python package (fetch, dates, jd, extract, filters, score, pipeline, markdown, excel, write_review, applications)
config/                       companies.yaml, filters.yaml, scoring.yaml, candidate.yaml
.claude/commands/             /jobs /apply /mark
inputs/                       resume, LinkedIn profile, Harvard CV guide (personal; repo is private)
tests/                        pytest suite and PowerShell helper tests
career-ops/                   scanner clone (git-ignored)
career-ops-data/              scanner data root (git-ignored)
output/ state/ jobs/ applications/ logs/   generated, git-ignored
```

## Tests

```
python -m pytest tests -q
```

Covers the years/degree/cert extractor (10 realistic JD snippets plus regressions found on real postings), filters, scoring, the Excel read-back, locked-file handling, `/mark`, `/apply` validation and PDF rendering, and the helper functions in `jobboard.ps1`.

## More

`CLAUDE.md` has the project rules (filters, fact rules for application documents, code conventions). `PLAN.md` has the design, the company-resolution table and the list of companies to check by hand.
