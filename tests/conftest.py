import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from vibe_mcp.config import Config
from vibe_mcp.jobs import Job, run_dir_for


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def sh(*args: str, cwd: Path) -> str:
    """Run git (or any command) synchronously for test setup."""
    done = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo on branch main with one commit: a.txt = "1\\n", sub/b.txt = "b\\n"."""
    path = tmp_path / "repo"
    (path / "sub").mkdir(parents=True)
    sh("git", "init", "-q", "-b", "main", cwd=path)
    sh("git", "config", "user.name", "Test", cwd=path)
    sh("git", "config", "user.email", "test@example.com", cwd=path)
    sh("git", "config", "commit.gpgsign", "false", cwd=path)
    (path / "a.txt").write_text("1\n")
    (path / "sub" / "b.txt").write_text("b\n")
    sh("git", "add", "-A", cwd=path)
    sh("git", "commit", "-q", "-m", "init", cwd=path)
    return path


FAKE_SANDBOX = Path(__file__).parent / "fake_sandbox.py"


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """A config that runs the fake sandbox and keeps all data under tmp_path."""
    command = shlex.join([sys.executable, str(FAKE_SANDBOX)])
    return Config(sandbox_cmd=command, data_dir=tmp_path / "data")


@pytest.fixture
def new_job(cfg: Config, tmp_path: Path):
    """Factory for a Job whose worktree is a plain directory and whose spec is a fake directive."""

    def factory(directive: dict | str | None = None, job_id: str = "a1b2c3", **kw) -> Job:
        worktree = tmp_path / "wt" / job_id
        worktree.mkdir(parents=True, exist_ok=True)
        spec = directive if isinstance(directive, str) else json.dumps(directive or {})
        run_dir = run_dir_for(cfg.data_dir, job_id)
        return Job(id=job_id, run_dir=run_dir, spec=spec, worktree=worktree, **kw)

    return factory
