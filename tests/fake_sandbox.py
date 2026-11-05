"""Stand-in for vibe-sandbox: same command line, canned behaviour, no network, no cost.

The task file (--prompt-file) may hold a JSON directive; anything else is a plain successful run.
Directive keys (all optional):
  write {path: text}  files to create in the cwd (one streamed "effect" each)
  delete [path]       files to remove
  sleep seconds       pause before the final message
  message text        final assistant message (default "Done.")
  exit code           exit status (default 0)
  outcome, stop_reason, error, cost, tokens   values for export.json
  no_export           skip export.json (like a sandbox failure)
  huge_line n         stream one effect line of about n bytes
  spawn_child         start `sleep 300` in the same process group, pid written to RUN/child.pid
  ignore_term         ignore SIGTERM (for this process and its child)
Always writes RUN/argv.json with the cwd and arguments it received.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

VALUE_FLAGS = {"--prompt-file", "--model", "--max-turns", "--time-limit", "--max-price", "--output"}


def emit(entry: dict) -> None:
    print(json.dumps(entry), flush=True)


def effect(summary: str, **extra: object) -> dict:
    return {
        "type": "effect",
        "title": "file_system.edit",
        "detail": {"display": {"summary": summary}},
        "state": {"status": "completed", **extra},
    }


def main(argv: list[str]) -> int:
    run, args = Path(argv[0]), argv[1:]
    opts: dict[str, str] = {}
    for i, arg in enumerate(args):
        if arg in VALUE_FLAGS:
            opts[arg] = args[i + 1]
    (run / "argv.json").write_text(json.dumps({"cwd": os.getcwd(), "args": args, "opts": opts}))

    try:
        directive = json.loads(Path(opts["--prompt-file"]).read_text())
    except ValueError:
        directive = {}
    if not isinstance(directive, dict):
        directive = {}

    if directive.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if directive.get("spawn_child"):
        child = subprocess.Popen(["sleep", "300"])
        (run / "child.pid").write_text(str(child.pid))

    emit({"type": "message", "role": "user", "content": [{"type": "text", "text": "task"}]})
    emit({"type": "reasoning", "text": "thinking"})
    for name, text in directive.get("write", {}).items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        emit(effect(f"Editing {name}"))
    for name in directive.get("delete", []):
        Path(name).unlink()
        emit(effect(f"Deleting {name}"))
    if size := directive.get("huge_line"):
        emit(effect("Reading big file", output={"content": "x" * size}))

    time.sleep(directive.get("sleep", 0))
    message = directive.get("message", "Done.")
    emit({"type": "message", "role": "assistant", "content": [{"type": "text", "text": message}]})

    code = directive.get("exit", 0)
    if not directive.get("no_export"):
        tokens = directive.get("tokens", 1000)
        export = {
            "outcome": directive.get("outcome", "finished"),
            "stop_reason": directive.get("stop_reason"),
            "error": directive.get("error"),
            "session_id": "fake-session",
            "usage": {"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens},
            "cost_usd": directive.get("cost", 0.01),
            "steps": 1,
            "schema_version": 1,
            "exit_code": code,
            "config": {"bulky": "x" * 100},
        }
        (run / "export").mkdir(exist_ok=True)
        (run / "export" / "export.json").write_text(json.dumps(export))
    print(f"vibe-sandbox: exit={code}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
