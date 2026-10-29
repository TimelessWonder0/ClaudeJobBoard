# ClaudeJobBoard

A personal job board for finance roles. One command scans 49 career sites for the companies in `config/companies.yaml`, keeps the roles posted in the last 4 days that fit (Financial Analyst, FP&A, Pricing, Equity Research; 0 to 3 years; 8 metros; on-site or hybrid), scores them against a resume, and writes an Excel board you can sort and mark. When you pick a role, it builds a tailored one-page resume and cover letter as PDFs.

It never submits an application and never contacts anyone. You decide and send.

## What you get

- `output\JobBoard_Master.xlsx`: the file you work in. Sheets: **Board** (roles that passed the filters, best Fit Score first), **Hidden** (filtered roles with the reason and the sentence it came from, so you can override), **Check Manually** (companies with no scannable API, with careers links), **Run Log**.
- `output\JobBoard_YYYY-MM-DD.xlsx`: a snapshot of each run.
- Status, Applied Date, Resume, Cover Letter and Notes that you set in the master survive every run (matched by the ID column).
- `applications\<date>_<company>_<title>\`: resume PDF, cover letter PDF, `GapCheck.md`.

## Run it

From any PowerShell window:

```powershell
jobboard              # scan, build the board, print a summary, open Excel
jobboard -Review      # also write "Why it fits" / "Gaps" for the top 25 (uses Claude Code)
jobboard -NoOpen      # do not open Excel
jobboard -Days 7      # scan window in days (default 4)
jobboard -SkipScan    # rebuild the board from the last scan
```

Or double-click `jobboard.cmd`. From Claude Code, `/scan` runs the same script.

Steps, in order: (1) `node scan.mjs --since 4 --json` in `career-ops\`, receipt to `logs\<date>_scan.json`; (2) `python -m board run`; (3) `claude -p "/review"` with `-Review`; (4) summary: companies scanned, boards that errored, roles found / kept / hidden, new since last run; (5) open the master in Excel.

- Everything is logged to `logs\<date>.log`. If a step fails, the summary names the step and why.
- If every board fails (no internet, VPN, proxy), the run stops after step 1 with exit code 2 and tells you to check your connection.
- If `JobBoard_Master.xlsx` is open in Excel, the script warns you to close it and waits (up to 5 minutes). If it stays open, the board saves a timestamped copy next to it and says so.
- Exit codes: 0 ok, 1 a step failed, 2 every board failed, 3 a prerequisite is missing.
- Nothing is scheduled. There is no Task Scheduler entry; you run it when you want.

### Slash commands (Claude Code, run from this folder)

| Command | What it does |
|---|---|
| `/scan [days]` | Runs `jobboard.ps1` (scan and board), then `/review`. |
| `/review [N]` | Reads the top 25 rows, each `JD.md` and your resume; writes one-line "Why it fits" and "Gaps". Re-ranks only if a JD clearly contradicts the rule score, with a reason and a verbatim JD quote. |
| `/apply <id>` | Builds the resume and cover letter PDFs for that role, writes `GapCheck.md`, puts the paths in Excel, sets Status = Docs Ready. |
| `/mark <id> <status>` | Sets Status (New, Interested, Docs Ready, Applied, Interviewing, Rejected, Offer, Skip); Applied also sets Applied Date. |

## How roles are judged

- **Hidden (reason kept):** quant roles; Master's/MBA required; more than 3 years required; titles with director, manager, VP, intern; outside the 8 metros; fully remote; older than 4 days. Anything ambiguous is shown with a flag instead.
- **Fit Score 0 to 100:** title track, years vs about 1.5, skill overlap with the resume, metro (Bay Area highest), company priority, freshness. Weights are in `config\scoring.yaml`; rules in `config\filters.yaml`.
- **Facts only:** application documents are built from `config\candidate.yaml`. Employers, titles, dates, places and degrees are printed by the tool; any number, skill or credential it does not support is rejected before a PDF is written. Review both PDFs before you send them.

## Setup (once)

Requirements: Windows 10/11, Windows PowerShell 5.1, Node 22+, Python 3.12+, Excel, and optionally Claude Code for `/review`, `/apply`, `/mark`.

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
board/                        Python package (ingest, jd, extract, filters, score, excel, review, applications)
config/                       companies.yaml, filters.yaml, scoring.yaml, candidate.yaml
.claude/commands/             /scan /review /apply /mark
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
