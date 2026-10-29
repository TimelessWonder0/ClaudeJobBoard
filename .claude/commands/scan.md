---
description: Run the career-ops scan, rebuild the Excel board, then review the top roles
argument-hint: "[days]   posting window for the scan, default 4"
allowed-tools: Bash, Read, Write
---

Run the full daily job scan. Work from `C:\ClaudeJobBoard`. This is a real scan, not a dry run: it writes to
`career-ops-data\data\scan-history.tsv`. Window: `$ARGUMENTS` days (default 4).

The terminal command `jobboard` and this command run the same script, `jobboard.ps1`, so they cannot drift apart.

## 1. Scan and build the board

Run `jobboard.ps1` with `-NoOpen` (do not open Excel from Claude Code) and without `-Review` (you do the review
yourself in step 2, in this session, instead of starting a second Claude). It takes about five to ten minutes: use a
long timeout, or run it in the background and wait for it to finish.

```
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File C:\ClaudeJobBoard\jobboard.ps1 -NoOpen -Days <days> -LockWaitSeconds 60
```

(If the user gave no argument, leave out `-Days`; the default is 4.) The script does: `node scan.mjs --since <days> --json`
(receipt in `logs\<date>_scan.json`), then `python -m board run`, then prints a summary. It logs everything to
`logs\<date>.log`.

Read its exit code and output:

- **0**: all good. Continue.
- **2**: every board failed. Tell the user to check their internet connection (or VPN/proxy) and stop. Do not run
  step 2.
- **1**: a step failed. The output says which step and why. Report it exactly and stop. If it is step 1, the last
  lines of `logs\<date>_scan.err` help; if it is step 2, the error line is in the output.
- **3**: a prerequisite is missing (node, python or career-ops). Report it and stop.
- If the output says `JobBoard_Master.xlsx is open in Excel`, pass that on: the board was saved as a timestamped
  copy; the user should close the file and run `/scan` again (or `jobboard -SkipScan` in a terminal).

Do not edit `portals.yml`, pass `--dry-run`, or start a scheduled task.

## 2. Review

Run `/review`. If you cannot invoke it as a command, read `.claude\commands\review.md` and follow it exactly.

## 3. Report

One short summary:

1. Scan: companies scanned, boards that errored, boards only partly read (from the script's summary).
2. Board: roles found / kept / hidden (and the top hidden reasons), new since last run.
3. The top five roles by Fit Score: ID, company, title, score, one-line why.
4. Review: any re-ranked role with its reason; any rejected change.
5. Any NOTE or WARNING from the tools.

Do not mark statuses, apply to anything, or run `/apply`. The user picks roles.
