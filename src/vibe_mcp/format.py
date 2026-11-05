"""Plain-text renderings of jobs for the MCP tools.

Everything a tool returns lands in Claude's context, so keep it short.
"""

import time

from vibe_mcp.config import Config
from vibe_mcp.jobs import CLOSED, Details, Job


def duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    if minutes == 0:
        return f"{secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{minutes}m{secs:02d}s" if hours == 0 else f"{hours}h{minutes:02d}m"


def status_line(job: Job, now: float | None = None) -> str:
    """One line: id, state, duration, live progress, cost, error, title."""
    parts = [job.id, job.state]
    if job.started_at:
        end = job.ended_at or (time.time() if now is None else now)
        parts.append(duration(end - job.started_at))
    if job.state == "running" and job.step:
        parts.append(f"step {job.step}: {job.last_action}")
    elif job.state == "queued" and job.last_action:  # e.g. "reviewers 2/4 done" for a fan-out
        parts.append(f"({job.last_action})")
    if not job.active and (cost := job.cost_usd + job.reviewers_cost):  # a fan-out counts its team
        parts.append(f"${cost:.4f}" + (" (incl. reviewers)" if job.reviewers_cost else ""))
    if job.error and not job.active:
        parts.append(f"({job.error.splitlines()[0][:80]})")
    line = " ".join(parts)
    return f"{line} - {job.title[:40]}" if job.title else line


def _cut(message: str, limit: int) -> str:
    """The message cut at a line boundary, with a note saying how much was left out."""
    if len(message) <= limit:
        return message
    hint = "vibe_result(job_id, full=true) returns everything"
    cut = message.rfind("\n", 0, limit)
    if cut <= 0:  # a single huge line: all we can do is cut it
        return f"{message[:limit]}… [truncated; {hint}]"
    left = len([line for line in message[cut:].splitlines() if line.strip()])
    return f"{message[:cut]}\n[{left} more line{'s' if left != 1 else ''} omitted; {hint}]"


def result_text(job: Job, details: Details, cfg: Config, *, full: bool = False) -> str:
    lines = [status_line(job)]
    message = job.final_message.strip()
    if message:
        lines.append(f"vibe says: {message if full else _cut(message, cfg.max_message_chars)}")
    if job.error:
        lines.append(f"error: {job.error}")
    if job.warning:
        lines.append(f"WARNING: {job.warning}")
    if details.violations:
        scope = ", ".join(job.scope)
        lines.append(f"SCOPE VIOLATIONS (outside {scope}): {', '.join(details.violations)}")
    if details.stat:
        lines.append(details.stat)
    elif not job.commit and not job.active and not job.readonly:
        lines.append("no changes committed")
    if details.added:
        lines.append("added: " + ", ".join(details.added))
    lines.append(f"tokens: {job.tokens}")
    if job.reviewers_cost:
        total = job.reviewers_cost + job.cost_usd
        lines.append(
            f"reviewers: {len(job.depends_on)} jobs, ${job.reviewers_cost:.4f} "
            f"(merge ${job.cost_usd:.4f}, total ${total:.4f})"
        )
    if job.worktree and job.state not in CLOSED:
        lines.append(f"branch: {job.branch}")
        lines.append(f"worktree: {job.worktree}")
    lines.append(f"run: {job.run_dir}")
    if details.diff:
        raw = details.diff.encode()
        shown = raw[: cfg.max_diff_bytes].decode(errors="ignore")
        note = ""
        if len(raw) > cfg.max_diff_bytes:
            note = f"\n[diff truncated: {cfg.max_diff_bytes} of {len(raw)} bytes]"
        lines.append("--- diff ---\n" + shown + note)
    return "\n".join(lines)


def apply_text(job: Job, stat: str) -> str:
    return (
        f"applied job {job.id}: changes are staged in the main checkout, not committed.\n"
        f"{stat or 'no changes'}"
    )


def conflict_text(job: Job, conflicts: list[str]) -> str:
    return (
        f"CONFLICT applying job {job.id} in: {', '.join(conflicts)}. Main checkout unchanged; "
        f"job kept (branch {job.branch}, worktree {job.worktree}). "
        "Merge it by hand or resubmit on top of the current HEAD."
    )
