---
description: Review today's board from output\jobs_latest.md, give every role a verdict, and write the review workbook
argument-hint: "[anything extra to look at, e.g. 'only Bay Area' or 'compare roles 3 and 7']"
allowed-tools: Read, Write, Bash(python -m board.write_review:*)
---

Work from `C:\ClaudeJobBoard`. The board is already built: Python fetched, filtered and scored it, and wrote
`output\jobs_latest.md`. Start from that file. **Do not run a scan** (no `jobboard`, no `python -m board run`, no
`scan.mjs`) unless I ask for one. Never apply to anything, message anyone, or open an `.xlsx` file.

## 1. Read the board

Read `C:\ClaudeJobBoard\output\jobs_latest.md` first. Its header has the run time, the window, the boards that failed or
were partial, and the counts. If its run date is not today's date, say so in your first line ("This board is from
<date>, not today. Run `jobboard` for a fresh one."), then carry on with what is there.

Mention in one line any failed or partial boards from the header, because roles at those companies may be missing.

## 2. Give every role a verdict

Each role has a line `- First seen: <date> · Previously reviewed: yes (...) | no`, written by Python from earlier review
workbooks and the master's Status column. **Skip every role marked `Previously reviewed: yes`**: give it no verdict and no
reasoning. `board/write_review.py` lists it in Passed Over as "reviewed <date> (<verdict>)" or "status <Status>". Say in
your report how many roles were skipped this way.

The file lists roles best Python score first. Of the roles marked `Previously reviewed: no`, judge **only the first 30 under
Board and the first 10 under Date unconfirmed**. Do not reason about, or write verdicts for, anything past those caps:
`board/write_review.py` lists them in Passed Over as "below cap" with their Python score. Say in your report how many roles
were below the cap. For each of the roles inside the caps decide:

- **Apply now**: strong match on level, function and years; worth building materials today.
- **Strong**: good match with a gap that can be closed in the cover letter.
- **Stretch**: reaching on years, function or level, but possible.
- **Skip**: not worth the time. A Skip needs a one-line reason; it goes into the Passed Over sheet.

The score and tier in the file are a starting point (75+ Apply now, 60-74 Strong, 50-59 Stretch), not an order. You may
move a role up or down when the role itself says so; make the reason concrete. Use the quoted requirement lines under each
role. If a role is close and the quotes are not enough, read its saved JD file (the "JD file" line). Do not search the web.

For each role write exactly two short lines:

- **why**: one line tying something real in my background to the role's main need. Use only facts in `config\candidate.yaml`
  and `CLAUDE.md` (Boeing pricing and cost models, variance and scenario analysis, FAR/CAS documentation, Excel, SQL, Python,
  DCF at the SJSU Spartan Fund, BS Finance, MS CS in progress). Never invent a metric, tool, employer or credential.
- **gap**: one line naming the biggest requirement I do not clearly meet, quoted or paraphrased from the role's lines, or
  "None obvious". Examples: "SaaS metrics ownership", "4 yrs required, I have 1.5", "CFA preferred".

**Graduating class.** When a role has an `- Eligibility:` or `- Grad class:` line, or its title or JD names a graduating
class or a dated program ("2027 Early Career", "Class of 2026", "on track to graduate in Spring 2027"), the gap line must
state that eligibility requirement against my graduations, e.g. "Requires graduating Spring 2027; I graduated Dec 2024".
The `Grad class:` line is Python's comparison with `config\candidate.yaml` ("likely ineligible" or "possible fit"); it is a
hint, not a filter. You make the call: if the class excludes me, the verdict is Skip with that as the reason.

Things to weigh: a "Senior" title is allowed but reaching; the Training line shows a rotational, development or mentoring
signal (good for someone moving out of estimating); Date unconfirmed roles need their posting date checked before applying,
so say in the gap line if the date is the main risk. For Date unconfirmed roles, anything other than Skip means "worth
checking".

## 3. Write the review workbook

Write the verdicts as JSON to `C:\ClaudeJobBoard\state\review_input.json` (use the Write tool):

```json
{"verdicts": [
  {"id": 12, "verdict": "Apply now", "why": "Boeing pricing models and variance analysis match the FP&A forecasting need.", "gap": "4 yrs FP&A preferred, I have 1.5"},
  {"id": 31, "verdict": "Skip", "why": "Sales-adjacent client role, not finance analysis.", "gap": ""}
]}
```

`id` is the number in square brackets in the file (the same ID as `JobBoard_Master.xlsx`). `verdict` is one of Apply now,
Strong, Stretch, Skip. Include every role inside the caps (30 Board, 10 Date unconfirmed, previously reviewed roles not
counted); a role inside the caps without a verdict is listed under Passed Over with a warning, and a role past the caps or
previously reviewed needs none (a verdict for a previously reviewed role is ignored with a warning).

Then run:

```
python -m board.write_review state\review_input.json
```

Write the review workbook with `board/write_review.py` and tell me the path. It creates
`output\reviews\Claude_Review_<run date>.xlsx` with the sheets Picks (best first), Date Unconfirmed, Passed Over and Run
Info. It also records the verdicts in `state\reviews.json`, so the next run marks these roles as previously reviewed. It
never writes to `JobBoard_Master.xlsx`. If today's review file already exists or is open in Excel it saves a copy with
a time suffix and says so; pass that on.

## 4. Report

End with the path of the workbook, then at most ten lines: the Apply now and Strong picks as `[ID] company, title, score`
with the one-line why, and any warning the script printed. Remind me that `/apply <id>` builds the resume and cover letter
for one pick.

$ARGUMENTS
