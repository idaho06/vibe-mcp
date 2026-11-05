"""Fan-out: reviewers examine the same target in parallel and read-only, then one merge job collects
their reports. The MCP drives this itself, so it does not depend on the model choosing to spawn
subagents.

Everything here is a pure helper; the Manager owns scheduling and process handling.
"""

import subprocess
from collections.abc import Collection, Mapping
from pathlib import Path

from vibe_mcp.config import Config
from vibe_mcp.jobs import USABLE_BASE, Job, new_id, run_dir_for

# One reviewer's report, as pasted into the merge job's prompt. Separate from max_message_chars,
# which only limits what comes back to Claude.
MAX_REPORT_CHARS = 8000

_RULES = """## Reviewer role

You are one of {count} independent reviewers examining the same target in parallel. Each reviewer
has a different angle; yours is given below. A later step merges the reports, so report only what
your own angle finds.

Rules:

- You are read-only: do not modify, create or delete any file, and never commit.
- The "Task" section below describes the target and what the final result is for. It is **not** your
  assignment, so do not carry it out. Your assignment is to examine the target from your angle and
  report findings.
- Never ask questions. When something is ambiguous, take the most conservative reading and say so in
  a finding.
- Run only read-only commands such as `git diff`, `grep -n` and `nl -ba`. Do not run the project's
  code.
- Take every line number from tool output (`grep -n` or `nl -ba`), never by counting lines yourself.
- Report one finding per line: `file:line - [HIGH|MEDIUM|LOW] what is wrong or could be better - why
  it matters`. Most important first, at most 12. If your angle finds nothing, write `(none)`. Write
  nothing else.

## Your angle: {angle}

{text}

---

"""


def pin(repo: Path, ref: str) -> str:
    """The commit `ref` names right now, so every job of a group works on the same code."""
    done = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if done.returncode:
        raise ValueError(f"cannot resolve {ref!r}: {done.stderr.strip()}")
    return done.stdout.strip()


def reviewer_preamble(angle: str, text: str, count: int, context: str) -> str:
    """The reviewer's prompt up to and including the heading that introduces the task.

    The role and its rules come first, because the task text usually reads "Simplify X" and a
    reviewer that treated it as its assignment would edit files instead of reporting.
    """
    return _RULES.format(count=count, angle=angle, text=text) + context + "## Task\n\n"


def appendix(reviewers: list[Job]) -> str:
    """The reviewers' reports, for the end of the merge job's prompt."""
    parts = [
        "\n\n## Findings from independent reviewers\n\n"
        "Each reviewer examined the target from one angle. Reports can overlap, be wrong, or be "
        "missing: check each finding against the code before acting on it.\n"
    ]
    for r in reviewers:
        parts.append(f"\n### {r.angle} (job {r.id}, {r.state})\n\n")
        report = r.final_message.strip()
        if r.state not in USABLE_BASE:
            parts.append(f"(no report: {r.error or r.state})\n")
        elif not report:
            parts.append("(the reviewer gave no report)\n")
        else:
            if len(report) > MAX_REPORT_CHARS:
                report = report[:MAX_REPORT_CHARS] + "\n[report truncated]"
            note = (
                "(stopped at its limit; the report may be incomplete)\n"
                if r.state == "limit"
                else ""
            )
            parts.append(f"{note}{report}\n")
    return "".join(parts)


def progress(reviewers: list[Job]) -> str:
    return f"reviewers {sum(not r.active for r in reviewers)}/{len(reviewers)} done"


def build(
    *,
    cfg: Config,
    repo: Path,
    taken: Collection[str],
    skill: str,
    angles: Mapping[str, str],
    context: str,
    merge_preamble: str,
    spec: str,
    title: str,
    scope: tuple[str, ...],
    max_turns: int,
    time_limit: int,
    max_price: float,
    base_ref: str,
    readonly: bool,
) -> list[Job]:
    """One read-only reviewer per angle, then the merge job that depends on all of them."""
    ids = set(taken)

    def fresh_id() -> str:
        ids.add(job_id := new_id(ids))
        return job_id

    shared = {
        "repo": repo,
        "spec": spec,
        "skill": skill,
        "base_ref": base_ref,
        "max_turns": max_turns,
        "time_limit": time_limit,
        "max_price": max_price,
    }
    merge_id = fresh_id()
    reviewers = []
    for angle, text in angles.items():
        job_id = fresh_id()
        reviewers.append(
            Job(
                id=job_id,
                run_dir=run_dir_for(cfg.data_dir, job_id),
                title=f"{angle} review",
                preamble=reviewer_preamble(angle, text, len(angles), context),
                readonly=True,
                hidden=True,
                parent=merge_id,
                angle=angle,
                **shared,
            )
        )
    merge = Job(
        id=merge_id,
        run_dir=run_dir_for(cfg.data_dir, merge_id),
        title=title,
        preamble=merge_preamble,
        scope=scope,
        depends_on=tuple(r.id for r in reviewers),
        readonly=readonly,
        last_action=f"waiting for {len(reviewers)} reviewers",
        **shared,
    )
    return [*reviewers, merge]
