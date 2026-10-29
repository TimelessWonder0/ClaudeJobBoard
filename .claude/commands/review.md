---
description: Review the top 25 roles on today's board; write "Why it fits" and "Gaps" into Excel
argument-hint: "[N] [--all]   (default 25; --all includes roles that already have a review)"
allowed-tools: Bash(python -m board review:*), Read, Write
---

Review today's job board for Shreyas. Work from `C:\ClaudeJobBoard`. Arguments: `$ARGUMENTS`
(an optional number overrides 25; `--all` also re-reviews roles that already have a review).

## 1. Prepare

Run `python -m board review prepare --top <N> [--all]`.

- It reads today's `output\JobBoard_<date>.xlsx`, takes the top rows by Fit Score, skips roles whose Status is not
  New / Interested / Docs Ready (Applied, Skip, Rejected, Interviewing, Offer are already worked), skips roles that
  already have a review unless `--all`, and writes `state\review_packet.json`.
- If it says the board file is missing, tell the user to run `python -m board run` first and stop.

Read `state\review_packet.json`. Each row has: `id`, `company`, `title`, `location`, `rule_score`, `score_parts`,
`flags`, the extractor's `years_evidence` / `degree_evidence` / `certs`, and `jd_path`.

## 2. Read the sources

- The resume: `career-ops-data\cv.md` (the packet's `resume_path`). Also read `Profile (4).pdf` in the repo root for
  extra facts (AI-assisted workflows, FAR/CAS audit documentation, onboarding 3 analysts, SJSU Spartan Fund buy
  recommendations).
- Every row's `JD.md` (`jd_path`). Read the whole file, not just the qualifications. Read several in parallel.
- A row with no `jd_path` (JD not fetched): write the lines from the title and flags only, say so in Gaps ("JD not
  fetched, unverified"), and do not change its score.

## 3. Write the two lines per role

Write only facts you can point to in the resume or the JD. Never invent experience, tools, metrics or credentials,
and never claim the user has something the resume does not show.

- **Why it fits**: one line, at most 160 characters. Name the concrete overlap between this JD and the resume
  (for example pricing and cost-proposal models at Boeing, variance and scenario analysis, Excel/VBA/Power Query,
  DCF work). No praise, no filler.
- **Gaps**: one line, at most 160 characters. Name what the JD asks for that the resume does not show: years above
  1.5, a required license or certification (CFA, Series 7/63/86/87, CPA), degree, domain (SaaS, ads, supply chain,
  equity coverage), a tool. If the only gap is minor, say that. Never write "none" for a role with a flag.

## 4. Re-rank only on a clear contradiction

Keep the rule score unless the JD clearly contradicts it. A change is allowed only when the JD itself shows that
the rule score is wrong, for example:

- the title matches a finance track but the JD describes another job (sales quota, data engineering, bookkeeping,
  month-end accounting only, legal);
- a hard requirement the extractor missed: a mandatory license or clearance, a degree, a years figure in a table or
  odd phrasing;
- the work is the same as the user's current job (cost and pricing proposals, variance analysis for programs) and
  the rule score treats it as a weak match.

Do not change a score for company prestige, taste, small gaps, or because a role "feels" better. When unsure, keep
the rule score.

A change needs all of: a new `score` (at most 25 points from `rule_score`, 0 to 100), a one-sentence `reason`, and
an `evidence` string copied word for word from the JD. The tool checks the quote against `JD.md` and rejects the
change if it is not there. If the JD shows a hard-filter violation the extractor missed (for example "7 years
required"), lower the score, say so in the reason, and tell the user; do not hide the role.

## 5. Save and apply

Write `state\review_input.json`:

```json
{"reviews": [
  {"id": 14, "why": "...", "gaps": "..."},
  {"id": 9, "why": "...", "gaps": "...", "score": 52, "reason": "JD is a quota-carrying sales role", "evidence": "exact JD sentence"}
]}
```

Include every `id` from the packet. Then run `python -m board review apply state\review_input.json`.

The tool writes Fit Score, "Why it fits / Gaps" and a `reviewed: old -> new` flag into `output\JobBoard_Master.xlsx`
and today's snapshot, re-sorts the Board, and leaves Status, Applied Date, Resume, Cover Letter and Notes as they
were. If the master is open in Excel it saves a timestamped copy and prints a NOTE; pass that on to the user.

## 6. Report

Show the user:

1. A table of the reviewed roles: id, company, title, rule score, final score.
2. Every re-rank with its reason and the JD quote, from the tool's output. If none: "No scores changed."
3. Any WARNING the tool printed (rejected changes, truncated lines, ids not reviewed) and any file-lock NOTE.

Do not mark statuses, apply to anything, or edit any file other than `state\review_input.json`.
