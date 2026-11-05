---
name: code-review
description: Review the code named in the task for correctness bugs, security problems and broken edge cases, and report findings without changing any file. Written for unattended jobs run through vibe-mcp.
license: MIT
---

# Code review

Find real defects in the code named in the task and report them. You run unattended and you are
read-only: your report is the whole result.

## Ground rules

- **Do not modify, create or delete any file.**
- **Never ask questions.** State your assumptions in the report.
- **Scope:** the files or git range named in the task (`git diff <range>` shows a range). If no
  scope is given, say so and stop.
- **Defects only:** wrong results, crashes, data loss, races, resource leaks, security problems
  (injection, path traversal, unsafe deserialization, secrets in code), missing error handling for
  errors that can actually happen, off-by-one and boundary errors, and broken contracts between
  callers and callees. Not style, naming, formatting or taste.
- **Run only what the task tells you to run** (for example a test command). Otherwise read code only;
  read-only commands such as `git diff`, `grep -n` and `nl -ba` are always fine.

## Method

1. Read the scope, then read the callers and callees around anything suspicious. Defects usually
   live at the seams between pieces of code.
2. For each suspicion, build a concrete failure: the input or state that triggers it and what goes
   wrong. If you cannot, drop it.
3. Re-read the surrounding code to make sure nothing else already handles the case.
4. Get every line number from tool output: run `grep -n` or `nl -ba <file>` on the exact line and copy
   the number. Never count lines yourself; miscounted line numbers are the most common error in reports.
5. Rank what remains by impact.

## Final report

Under 25 lines. One entry per finding, at most 10, most serious first:

`file:line - one-sentence defect. Trigger: <input or state>. Effect: <what breaks>. Confidence: high|medium.`

If you found nothing, write `No findings` and one line on what you checked. Finish with
`Not checked:` and anything in scope you could not review.
