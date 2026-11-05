"""Project documents put in front of a task, for files Vibe cannot see in the job's worktree.

A worktree holds only tracked files, so untracked or gitignored docs (an AGENTS.md kept out of
git, say) are read from the main checkout instead.
"""

from collections.abc import Iterable
from pathlib import Path

MAX_CHARS = 50_000  # per file; a runaway document would otherwise be paid for on every job


def blocks(repo: Path, files: Iterable[str]) -> str:
    """One prompt block per existing, non-empty file, in the given order."""
    text = ""
    for name in files:
        path = repo / name
        content = path.read_text(errors="replace").strip() if path.is_file() else ""
        if not content:
            continue
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS] + "\n[truncated]"
        text += f"## Project context: {name}\n\n{content}\n\n---\n\n"
    return text
