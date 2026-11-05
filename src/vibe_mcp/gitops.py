"""Thin async wrappers around the git commands the server needs."""

import asyncio
import os
from pathlib import Path

_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "LC_ALL": "C"}
_IDENTITY = ("-c", "user.name=vibe-mcp", "-c", "user.email=vibe-mcp@localhost")


class GitError(Exception):
    """A git command failed."""


async def git(*args: str, cwd: Path) -> str:
    """Run `git *args` in `cwd` and return stdout without the trailing newline."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        env=_ENV,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    stdout = out.decode(errors="replace").rstrip("\n")
    if proc.returncode:
        detail = err.decode(errors="replace").strip() or stdout
        raise GitError(f"git {' '.join(args)} (in {cwd}) failed: {detail}")
    return stdout


async def _diff_names(*args: str, cwd: Path) -> list[str]:
    """Paths printed by `git diff --name-only -z *args` (NUL-separated, so any name is safe)."""
    output = await git("diff", "--name-only", "-z", *args, cwd=cwd)
    return [name for name in output.split("\0") if name]


async def toplevel(cwd: Path) -> Path:
    return Path(await git("rev-parse", "--show-toplevel", cwd=cwd))


async def head(cwd: Path, ref: str = "HEAD") -> str:
    return await git("rev-parse", "--verify", ref, cwd=cwd)


async def current_branch(cwd: Path) -> str:
    """The checked-out branch, or "" when HEAD is detached."""
    return await git("branch", "--show-current", cwd=cwd)


async def is_clean(cwd: Path, *, untracked: bool = False) -> bool:
    """No staged or unstaged changes to tracked files (and no untracked files if asked)."""
    mode = "all" if untracked else "no"
    return not await git("status", "--porcelain", f"--untracked-files={mode}", cwd=cwd)


async def add_worktree(repo: Path, path: Path, branch: str | None, base: str) -> None:
    """Check `base` out in a new worktree at `path`, on a new `branch` (None: detached)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = ["-b", branch] if branch else ["--detach"]
    await git("worktree", "add", "-q", *target, str(path), base, cwd=repo)


async def commit_all(worktree: Path, message: str) -> str | None:
    """Commit everything (new, modified, deleted; .gitignore respected). None if nothing changed."""
    await git("add", "-A", cwd=worktree)
    if await is_clean(worktree, untracked=True):
        return None
    await git(
        *_IDENTITY,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--no-verify",
        "-q",
        "-m",
        message,
        cwd=worktree,
    )
    return await head(worktree)


async def diff_stat(repo: Path, base: str, tip: str) -> str:
    return await git("diff", "--stat", base, tip, cwd=repo)


async def diff(repo: Path, base: str, tip: str) -> str:
    return await git("diff", base, tip, cwd=repo)


async def changed_files(repo: Path, base: str, tip: str) -> list[str]:
    """Every path touched between the commits; a rename lists both its old and new path."""
    return await _diff_names("--no-renames", base, tip, cwd=repo)


async def added_files(repo: Path, base: str, tip: str) -> list[str]:
    return await _diff_names("--no-renames", "--diff-filter=A", base, tip, cwd=repo)


async def merge_squash(repo: Path, branch: str) -> list[str]:
    """Stage `branch`'s changes in the checkout at `repo` without committing.

    Returns [] on success. On a content conflict, undoes the merge and returns the
    conflicting paths. Any other failure raises GitError.
    """
    try:
        await git("merge", "--squash", branch, cwd=repo)
    except GitError:
        conflicts = await _diff_names("--diff-filter=U", cwd=repo)
        if not conflicts:
            raise
        await git("reset", "--merge", cwd=repo)
        return conflicts
    return []


async def _branch_exists(repo: Path, branch: str) -> bool:
    try:
        await git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo)
    except GitError:
        return False
    return True


async def remove_worktree(repo: Path, path: Path, branch: str | None) -> None:
    """Delete the worktree (even if dirty) and its branch, if any. Safe to call twice."""
    if await asyncio.to_thread(path.exists):
        await git("worktree", "remove", "--force", str(path), cwd=repo)
    await git("worktree", "prune", cwd=repo)
    if branch and await _branch_exists(repo, branch):
        await git("branch", "-D", branch, cwd=repo)
