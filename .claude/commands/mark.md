---
description: Set a role's Status in the master Excel (Applied also sets Applied Date)
argument-hint: "<id> <status> [YYYY-MM-DD]   status: New, Interested, Docs Ready, Applied, Interviewing, Rejected, Offer, Skip"
allowed-tools: Bash(python -m board mark:*)
---

Update the Status of one role in the master job board. Arguments: `$ARGUMENTS`.

1. Parse `<id> <status> [date]`. The status may be written in any case and with a space, hyphen or underscore
   ("docs ready", "docs-ready"). The optional date is the Applied Date (default: today); it only matters for `Applied`.
2. If the id or status is missing, show the argument hint and the eight valid statuses, and stop. Do not guess.
3. Run, from `C:\ClaudeJobBoard`:

   `python -m board mark <id> <status> [--date YYYY-MM-DD]`

4. Report the tool's output in one or two lines: company, title, old status -> new status, and the Applied Date when
   the status is Applied.
   - `error: ID n is on the Hidden sheet`: tell the user to type Show in that row's Override cell on the Hidden sheet,
     run `python -m board run`, then mark it.
   - A NOTE that the master is open in Excel: pass it on (the change was saved in a timestamped copy; close the master
     and run `/mark` again).

The tool updates the master and today's snapshot in place. It changes only the Status cell (and Applied Date for
Applied); notes, links and every other row are left untouched. Do nothing else.
