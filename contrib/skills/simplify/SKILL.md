---
name: simplify
description: Simplify the code named in the task (reuse, clarity, efficiency) without changing what it does. Written for unattended jobs run through vibe-mcp.
license: MIT
---

# Simplify

Make the code named in the task simpler without changing what it does. You run unattended: nobody
can answer questions, and your final report is all the reviewer reads about your work.

## Ground rules

- **Never ask questions.** When something is ambiguous, take the most conservative reading and say
  so in the report.
- **Stay in scope.** Edit only the files or paths the task names. If the task gives a git range
  (for example `HEAD~3..HEAD`), the scope is the files that range touches. If the task names no
  scope at all, change nothing and say so.
- **Keep behavior identical.** Public names and signatures, output and file formats, error messages
  and anything a test asserts stay as they are, unless the task says otherwise.
- **Run only what the task tells you to run.** The task may give a test or lint command: run it
  before and after your edits. Otherwise do not run the project's code.
- **Follow the project.** Match the conventions of the surrounding code and of any project
  documents included in the task.

## Method

1. Read the scope: the files, and `git diff <range>` if a range is given.
2. List candidate simplifications before editing anything. Look for:
   - code that repeats something that already exists elsewhere in the project (search first, then
     call the existing helper);
   - dead code: unused functions, parameters, imports, and branches that cannot run;
   - state that can be derived from other state;
   - deep nesting that early returns would flatten;
   - abstractions with a single caller, and special cases that one more general rule would absorb;
   - wasted work: repeated I/O or computation, independent steps done one after another.
3. Keep only the changes that clearly reduce complexity and are safe. When unsure, skip the idea
   and list it under "Skipped".
4. Apply the changes as small, separate edits. Do not rewrite code that is fine, rename for taste,
   reformat, add features, add dependencies, or add comments that restate the code.
5. Review your own work with `git diff` and undo anything that might change behavior.

## Final report

End with a short report, under 15 lines (the reviewer pays for every line):

- **Changed:** one line per file: what changed and why.
- **Skipped:** ideas you left out, with the reason.
- **Risks:** anything that could alter behavior, or `none`.
- **Commands run:** each command and its outcome, or `none`.
