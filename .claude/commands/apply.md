---
description: Build a tailored one-page resume and cover letter PDF for a board role, gap-check it, update Excel
argument-hint: "<id>   (the ID column on the Board sheet)"
allowed-tools: Bash(python -m board apply:*), Read, Write
---

Build application documents for Shreyas for role ID `$ARGUMENTS`. Work from `C:\ClaudeJobBoard`.
This command never submits an application and never contacts anyone. It produces files and updates Excel.

## 1. Prepare

Run `python -m board apply prepare <id>`. It creates `applications\<date>_<company>_<title>\` and writes
`request.json` there. If it fails (unknown ID, no JD on file), tell the user and stop.

Read `request.json`. It lists the source files, what the extractor found in the JD (`jd_extraction`), the limits,
and `keywords`: `mirror` = JD words the candidate file supports, `do_not_claim` = JD words it does not.

## 2. Read

- The JD: `request.json -> sources.jd` (whole file).
- The resume: `inputs\FP&A_Resume_--_Shreyas_Kukanur (1).pdf`, and `inputs\Profile (4).pdf` for the extra facts.
- The facts you may use: `config\candidate.yaml`. Every employer, title, date, place, degree, number and skill in the
  documents must come from it. Its `bank` lines are the evidence you reword, select and reorder.
- The Harvard guide: `inputs\harvard CV and cover letter guide.pdf`. Use pages 3-6 (active verbs, fragments,
  reverse-chronological order, most important content first, tailor to the posting) and pages 17-20 (cover letter
  structure: opening with the position and your status, evidence paragraphs, tailoring, close). The guide is written
  for academic CVs; take the principles, not the academic content or length.

## 3. Write `content.json` in the application folder

```json
{
  "resume": {
    "summary": "2-3 sentences",
    "experience": {"boeing": ["4-6 bullets"], "fintax": ["0-2 bullets"], "forrest": ["0-2 bullets"]},
    "skills": {"FP&A": ["..."], "Tools": ["..."], "Business": ["..."]}
  },
  "cover_letter": {"salutation": "Dear <Company> Hiring Team,", "paragraphs": ["3-4 paragraphs"], "closing": "Sincerely,"}
}
```

Resume rules (ATS-friendly, one page):

- Start every bullet with a strong active verb (the guide's lists), as a fragment, not a sentence. One idea per bullet,
  at most 280 characters. Put the bullets that answer this JD's requirements first.
- Mirror the JD's wording only for words in `keywords.mirror` (for example write "variance analysis" if the JD says so,
  and the bank supports it). Never use a word from `keywords.do_not_claim`, and never add a tool, license or
  credential the candidate file does not list.
- Numbers: only figures that appear in `config\candidate.yaml`. No new percentages, dollar amounts or counts.
- Summary names the target track (FP&A, pricing, equity research, financial analysis) and the strongest matching fact.
- Skills: reorder so the JD's terms come first; keep only items from the candidate file.
- Boeing is the current job and always needs bullets. Include Fintax or Forrest only if they help this JD and the page
  allows; the Master's at Johns Hopkins is in progress (the tool prints it that way).
- Location is San Jose, CA everywhere. Do not type locations, dates or titles yourself; the tool prints them.

Cover letter rules (one page, 230-380 words, 3-4 paragraphs):

1. Opening: the exact role title and company, who the user is (B.S. Finance, SJSU; currently Estimating and Pricing
   Specialist at Boeing; M.S. Computer Science at Johns Hopkins in progress).
2. One or two evidence paragraphs: take the two or three things this JD asks for most and answer each with a concrete
   fact from the bank.
3. Tailoring: why this role fits, using only what the JD itself says about the team and work. Do not invent facts
   about the company.
4. Close: short, confident, no begging. Address the letter to the company's hiring team unless the JD names a person.
   No placeholders.

If the user has a gap against a requirement, do not hide it by overstating. Leave the claim out.

## 4. Render

Run `python -m board apply render <id>`. If it prints `content.json rejected`, fix exactly what it lists (invented
number, unsupported skill, credential, wrong length) and run it again. Stop after three rejections and show the user
the errors. On success it writes `Shreyas_Kukanur_Resume_<Company>.pdf` and
`Shreyas_Kukanur_Cover_Letter_<Company>.pdf`, checks both are one page, and prints an ATS check and which JD skill
groups the documents cover.

Read both PDFs back and check the text is complete and reads well.

## 5. Gap check

Write `GapCheck.md` in the application folder (path in `request.json -> gap_check_file`). Compare every requirement in
the JD with what the documents and the candidate file show:

| Requirement (JD wording) | Evidence in the resume | Status: Met / Partial / Gap |

Then three short lists: **Gaps to be honest about** (years, license or certification, degree, domain, tool),
**JD keywords left out on purpose** (from `do_not_claim`, and why), **Risks before applying** (anything that could make
the user a long shot; say so plainly, including a years requirement above 1.5). Facts only.

## 6. Update Excel

Run `python -m board apply finish <id>`. It puts the two PDF paths in the Resume and Cover Letter cells and sets
Status = Docs Ready (only if the status is New or Interested; an Applied role is left alone). If the master is open in
Excel it saves a timestamped copy and prints a NOTE; pass that on.

## 7. Report

Give the user: the folder path, the two PDF paths, the top three gaps from the gap check, any keyword you left out,
and any NOTE. Remind them to read both PDFs before sending anything. Do not mark Applied; that is `/mark`.
