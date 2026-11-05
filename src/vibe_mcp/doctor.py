"""`vibe-mcp doctor`: check the things a first-time user usually gets wrong."""

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from vibe_mcp.config import Config

MIN_GIT = (2, 22)
TESTED_VIBE = "3.1"
_INSTALL_VIBE = "install it with `uv tool install mistral-vibe`"


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str  # what was found, or how to fix it


def _run(
    run: Callable, cmd: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess | None:
    try:
        return run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15)
    except OSError, subprocess.TimeoutExpired:
        return None


def _succeeded(done: subprocess.CompletedProcess | None) -> bool:
    return done is not None and done.returncode == 0


def run_checks(
    cfg: Config,
    *,
    cwd: Path | None = None,
    environ: Mapping[str, str] = os.environ,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable = subprocess.run,
) -> list[Check]:
    """Probe the machine. The keyword arguments exist so tests can fake the machine."""
    home, cwd = home or Path.home(), cwd or Path.cwd()
    checks: list[Check] = []

    def add(name: str, ok: bool, good: str, bad: str, bad_status: str = "fail") -> None:
        checks.append(Check(name, "ok" if ok else bad_status, good if ok else bad))

    # git
    done = _run(run, ["git", "--version"]) if which("git") else None
    found = re.search(r"(\d+)\.(\d+)", done.stdout) if _succeeded(done) else None
    version = tuple(int(n) for n in found.groups()) if found else None
    add(
        "git",
        version is not None and version >= MIN_GIT,
        done.stdout.strip() if done else "",
        f"git {MIN_GIT[0]}.{MIN_GIT[1]} or newer is required; install or upgrade git",
    )

    # bubblewrap
    if not which("bwrap"):
        add(
            "bwrap",
            False,
            "",
            "bubblewrap is not installed; install your distro's `bubblewrap` package",
        )
    else:
        probe = _run(run, ["bwrap", "--ro-bind", "/", "/", "true"])
        add(
            "bwrap",
            _succeeded(probe),
            "bubblewrap works",
            "bwrap is installed but cannot start; unprivileged user namespaces may be disabled",
        )

    # the sandbox command
    command = shlex.split(cfg.sandbox_cmd)[0]
    reference = Path(command).name == "vibe-sandbox"
    add(
        "sandbox",
        bool(which(command)),
        which(command) or "",
        f"{command} not found; install the reference one with "
        "`install -m755 contrib/vibe-sandbox ~/.local/bin/`",
    )

    # vibe
    vibe = which("vibe")
    if not vibe:
        add("vibe", False, "", f"vibe not found; {_INSTALL_VIBE}")
    else:
        done = _run(run, [vibe, "--version"])
        text = done.stdout.strip() if _succeeded(done) else ""
        add(
            "vibe",
            TESTED_VIBE in text,
            text,
            f"{text or 'vibe --version failed'}: only tested with Vibe {TESTED_VIBE}.x",
            "warn",
        )

    # what the reference sandbox binds unconditionally
    if reference:
        fixes = {
            ".local/bin": f"~/.local/bin is missing; {_INSTALL_VIBE}",
            ".local/share/uv": f"~/.local/share/uv is missing; {_INSTALL_VIBE} (uv-managed tools)",
            ".vibe": "~/.vibe is missing; run `vibe --setup`",
        }
        missing = [fix for path, fix in fixes.items() if not (home / path).is_dir()]
        add(
            "sandbox paths",
            not missing,
            "~/.local/bin, ~/.local/share/uv and ~/.vibe exist",
            "; ".join(missing),
        )

    # credentials
    if environ.get("MISTRAL_API_KEY"):
        checks.append(Check("auth", "ok", "MISTRAL_API_KEY is set (API billing)"))
    elif reference:
        problems = []
        if not which("xdg-dbus-proxy"):
            problems.append("xdg-dbus-proxy is not installed")
        if not environ.get("DBUS_SESSION_BUS_ADDRESS"):
            problems.append("DBUS_SESSION_BUS_ADDRESS is not set")
        add(
            "auth",
            not problems,
            "keyring login via D-Bus (not verified; run `vibe --setup` if Vibe asks you to log in)",
            "; ".join(problems) + "; or set MISTRAL_API_KEY",
        )
    else:
        checks.append(
            Check(
                "auth",
                "warn",
                "MISTRAL_API_KEY is not set; cannot verify a login for a custom sandbox",
            )
        )

    # the repo in the current directory
    top = _run(run, ["git", "rev-parse", "--show-toplevel"], cwd)
    if not _succeeded(top):
        add(
            "repo",
            False,
            "",
            "not inside a git repository (run doctor in the repo you want to use)",
            "warn",
        )
    else:
        has_commit = _succeeded(_run(run, ["git", "rev-parse", "--verify", "HEAD"], cwd))
        add(
            "repo",
            has_commit,
            top.stdout.strip(),
            "repo has no commits yet; jobs branch from HEAD",
            "warn",
        )
    return checks
