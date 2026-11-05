"""Run one Vibe job through vibe-sandbox and interpret its output."""

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from vibe_mcp.config import Config
from vibe_mcp.jobs import Job

_ACTION_CHARS = 100


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
    error = export.get("error")
    job.error = (error if isinstance(error, str) else json.dumps(error)) if error else None


def final_state(exit_code: int) -> str:
    """vibe-sandbox returns Vibe's exit code: 0 finished, 3 stopped at a limit, else failed."""
    return {0: "finished", 3: "limit"}.get(exit_code, "failed")
