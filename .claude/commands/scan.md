---
description: Run the career-ops scan and the negative-filter funnel, rebuild the Excel board, then review the top roles
argument-hint: "[days]   posting window for the scan, default 3"
allowed-tools: Bash, Read, Write
---

Run the full daily job scan. Work from `C:\ClaudeJobBoard`. This is a real scan, not a dry run: it writes to
`career-ops-data\data\scan-history.tsv`. Window: `$ARGUMENTS` days (default 3).

The terminal command `jobboard` and this command run the same script, `jobboard.ps1`, so they cannot drift apart.

## What the script does

1. **Scan:** `node scan.mjs --since <days> --json` reads whole boards with no search words and no title or location
   filter (`portals.yml` has an empty `title_filter`). Receipt: `logs\<date>_scan.json`.
2. **Funnel (`python -m board run`)**, cheapest steps first, count logged after each: 1 pull everything, 2 posted in the
   window, 3 location (8 metros), 4 negative titles (`config\negative_titles.yaml`), 5 finance relevance (keywords,
   "accounting track", Claude for the rest), 6 experience / degree / license from the JD, 7 career-ops liveness,
   evaluation and fit. Every dropped row goes to the Hidden sheet with its step and reason.
3. **Summary** with the funnel counts, boards that errored, roles found / kept / hidden.

## 1. Run it

Run `jobboard.ps1` with `-NoOpen` (do not open Excel from Claude Code) and without `-Review` (you do the review
yourself in step 2, in this session, instead of starting a second Claude). The first run takes a long time (whole
boards, a JD per survivor, Claude title classification, up to 25 career-ops evaluations); use a long timeout or run it
in the background and wait for it to finish.

```
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File C:\ClaudeJobBoard\jobboard.ps1 -NoOpen -Days <days> -LockWaitSeconds 60
```

(If the user gave no argument, leave out `-Days`; the default is 3.)

Read its exit code and output:

- **0**: all good. Continue.
- **2**: every board failed. Tell the user to check their internet connection (or VPN/proxy) and stop. Do not run step 2.
- **1**: a step failed. The output says which step and why. Report it exactly and stop.
- **3**: a prerequisite is missing (node, python or career-ops). Report it and stop.
- If the output says `JobBoard_Master.xlsx is open in Excel`, pass that on: the board was saved as a timestamped
  copy; the user should close the file and run `/scan` again (or `jobboard -SkipScan` in a terminal).

Do not edit `portals.yml`, add search terms or positive title filters, pass `--dry-run`, or start a scheduled task.

## 2. Review

Run `/review`. If you cannot invoke it as a command, read `.claude\commands\review.md` and follow it exactly.

## 3. Report

One short summary:

1. **Funnel:** the count after each step (pulled, date, location, titles, finance, years, fit) and the Funnel sheet totals.
2. **Scan:** boards that errored and boards only partly read.
3. **Safety check:** look at the 25 random titles printed for step 4 and the 25 for step 5. Say plainly if any looks like a
   good finance role that was wrongly dropped, and which rule dropped it.
4. **Board:** roles kept, the top five by career-ops score then Fit Score (ID, company, title, scores, one-line why).
5. **Review:** any re-ranked role with its reason; any rejected change.
6. Any NOTE or WARNING from the tools.

Do not mark statuses, apply to anything, or run `/apply`. The user picks roles.
