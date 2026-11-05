"""Run one Vibe job through vibe-sandbox and interpret its output."""

import asyncio
import contextlib
import json
import os
import shlex
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vibe_mcp.config import Config
from vibe_mcp.jobs import Job

_ACTION_CHARS = 100
_STDERR_TAIL = 300  # characters of stderr quoted in an error message
# Vibe enforces --time-limit itself; this is the backstop if it hangs.
HARD_TIMEOUT_GRACE = 60.0
# After SIGTERM, how long the process group gets before SIGKILL.
KILL_AFTER = 10.0
# Streaming lines can hold whole file contents; asyncio's default limit is 64 KiB.
_LINE_LIMIT = 64 * 1024 * 1024


@dataclass(frozen=True)
class Event:
    """What we use from Vibe's streaming output: tool calls (progress) and the final message."""

    kind: str  # "effect" | "final"
    text: str


def build_command(job: Job, cfg: Config) -> list[str]:
    """The vibe-sandbox command line for this job (cwd is the job's worktree)."""
    run = str(job.run_dir)
    cmd = [
        *shlex.split(cfg.sandbox_cmd),
        run,
        "--prompt-file",
        str(job.run_dir / "task.md"),
        "--model",
        cfg.model,
        "--auto-approve",
        "--trust",
        "--max-turns",
        str(job.max_turns),
        "--time-limit",
        str(job.time_limit),
    ]
    if job.max_price > 0:
        cmd += ["--max-price", str(job.max_price)]
    return [*cmd, "--output", "streaming"]


def _shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _ACTION_CHARS else text[: _ACTION_CHARS - 1] + "…"


def parse_stream_line(raw: str) -> Event | None:
    """Parse one JSONL line of `--output streaming`. The schema is undocumented, so be lenient."""
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "effect":
        detail = entry.get("detail")
        display = detail.get("display") if isinstance(detail, dict) else None
        summary = display.get("summary") if isinstance(display, dict) else None
        return Event("effect", _shorten(str(summary or entry.get("title") or "")))
    if entry.get("type") == "message" and entry.get("role") == "assistant":
        content = entry.get("content")
        if not isinstance(content, list):
            return None
        texts = [
            p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)
        ]
        return Event("final", "\n".join(texts))
    return None


def read_export(run_dir: Path) -> dict | None:
    """RUN/export/export.json (without the bulky `config`), or None if missing or unreadable."""
    try:
        export = json.loads((run_dir / "export" / "export.json").read_text())
    except OSError, ValueError:
        return None
    if not isinstance(export, dict):
        return None
    export.pop("config", None)
    return export


def apply_export(job: Job, export: dict | None) -> None:
    """Copy the authoritative outcome from export.json into the job."""
    if not export:
        return
    usage = export.get("usage")
    job.exit_code = export.get("exit_code")
    job.stop_reason = export.get("stop_reason")
    job.cost_usd = float(export.get("cost_usd") or 0)
    job.tokens = int(usage.get("total_tokens") or 0) if isinstance(usage, dict) else 0
    if error := export.get("error"):  # never erase an error recorded earlier (e.g. a hard timeout)
        job.error = error if isinstance(error, str) else json.dumps(error)


def final_state(exit_code: int) -> str:
    """vibe-sandbox returns Vibe's exit code: 0 finished, 3 stopped at a limit, else failed."""
    return {0: "finished", 3: "limit"}.get(exit_code, "failed")


def exit_error(run_dir: Path, code: int) -> str:
    """Error text for a failed run that gave none in export.json: exit code and end of stderr."""
    try:
        with (run_dir / "stderr.txt").open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4 * _STDERR_TAIL))  # never read a huge file whole
            tail = f.read().decode(errors="replace")[-_STDERR_TAIL:].strip()
    except OSError:
        tail = ""
    return f"vibe-sandbox exited with code {code}" + (f": {tail}" if tail else "")


def _open_logs(job: Job):
    """Create the run dir, write task.md, and open the stdout/stderr logs."""
    job.run_dir.mkdir(parents=True, exist_ok=True)
    (job.run_dir / "task.md").write_text(job.spec)
    return (job.run_dir / "stdout.jsonl").open("wb"), (job.run_dir / "stderr.txt").open("wb")


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the process group, then SIGKILL it (and any stragglers) after KILL_AFTER."""
    _signal_group(proc.pid, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), KILL_AFTER)
    _signal_group(proc.pid, signal.SIGKILL)
    await proc.wait()


async def _drain(
    proc: asyncio.subprocess.Process, job: Job, log, on_progress: Callable[[], None] | None
) -> None:
    """Copy stdout to the log, track progress, then wait for the process to exit."""
    assert proc.stdout
    while True:
        try:
            raw = await proc.stdout.readline()
        except ValueError:  # a line over the limit; asyncio already discarded it
            continue
        if not raw:
            break
        log.write(raw)
        log.flush()
        event = parse_stream_line(raw.decode(errors="replace"))
        if event is None:
            continue
        if event.kind == "effect":
            job.step += 1
            job.last_action = event.text
        else:
            job.final_message = event.text
        if on_progress:
            on_progress()
    await proc.wait()


async def run(job: Job, cfg: Config, on_progress: Callable[[], None] | None = None) -> int:
    """Run the job's spec in its worktree through vibe-sandbox and return the exit code.

    Fills job.pid, step, last_action and final_message while it runs (calling `on_progress`
    after each change). The caller reads export.json afterwards. Cancelling the task or
    exceeding the hard timeout kills the whole process group. A missing sandbox command
    raises OSError.
    """
    assert job.worktree is not None
    stdout_log, stderr_log = _open_logs(job)
    try:
        proc = await asyncio.create_subprocess_exec(
            *build_command(job, cfg),
            cwd=job.worktree,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_log,
            start_new_session=True,
            limit=_LINE_LIMIT,
        )
        job.pid = proc.pid
        if on_progress:
            on_progress()
        try:
            await asyncio.wait_for(
                _drain(proc, job, stdout_log, on_progress), job.time_limit + HARD_TIMEOUT_GRACE
            )
        except TimeoutError:
            job.error = f"killed: still running {HARD_TIMEOUT_GRACE:g}s past the time limit"
            await _terminate(proc)
        except asyncio.CancelledError:
            await _terminate(proc)
            raise
    finally:
        stdout_log.close()
        stderr_log.close()
    assert proc.returncode is not None
    return proc.returncode
