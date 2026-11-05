---
name: simplify-fanout
description: "Simplify the code a task names, using the findings of four independent reviewers (duplication, dead weight, structure, wasted work) that vibe-mcp runs in parallel first. Edits files in place and never changes behavior."
license: MIT
---

# Simplify, with independent reviewers

Four reviewers have already examined the target in parallel, each from one angle, and their reports
are at the end of this prompt. You are the one who edits: you decide which findings are right and make
the changes. Nobody can answer questions, and your final message is all the reviewer of your work reads.

## Ground rules

- **Never ask questions.** When something is ambiguous, take the most conservative reading and say so.
- **Behavior stays identical.** Public names and signatures, output and file formats, error messages
  and anything a test asserts must not change, unless the task says otherwise.
- **Stay inside the target** the task names (a git range or a list of files). Edit nothing else.
  Never commit, push or create branches: the server commits your work.
- **Run only commands the task gives you** (for example a test command). Otherwise do not run the
  project's code. Reading commands such as `git diff`, `grep -n` and `nl -ba` are always fine.
- **Reviewers can be wrong, duplicate each other, or be missing.** Trust the code, not the report.

## Process

1. Read the task and the "Findings from independent reviewers" section. If that section is missing
   (you were started directly), review the target yourself for duplication, dead weight, structure
   and wasted work, and continue from step 3.
2. Merge the reports: findings that point at the same code or the same cause become one entry.
3. Check every entry against the code. Open the lines. Drop a finding that is wrong, that something
   else already handles, or whose fix would change behavior.
4. Sort what is left. Outside the target: report it, do not touch it. A bug rather than a
   simplification: report it as "Noticed, not fixed".
5. Apply the remaining fixes as small, separate edits. Prefer deleting to rewriting. No renames for
   taste, no reformatting, no new dependencies, no new features, no comments that restate code.
6. Read your own change with `git diff` and undo anything you are not sure about. If the task gave
   you a test command, run it.

## Final report

Under 20 lines (the reader pays for every line):

- **Changed:** one line per change: file, and what improved.
- **Skipped:** each finding left alone, with the reason (wrong, changes behavior, outside the target).
- **Noticed, not fixed:** bugs you saw, or `none`.
- **Risks:** anything that could alter behavior, or `none`.
- **Commands run:** each command and its outcome, or `none`.
