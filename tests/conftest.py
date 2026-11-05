import subprocess
from pathlib import Path

import pytest


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
