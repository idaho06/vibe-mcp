import shlex
import subprocess
from pathlib import Path

import pytest

from vibe_mcp.config import Config
from vibe_mcp.doctor import Check, run_checks

NAMES = ["git", "bwrap", "sandbox", "vibe", "sandbox paths", "auth", "repo"]


def make_world(
    tmp_path: Path,
    *,
    missing: tuple[str, ...] = (),
    drop_dirs: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    dbus: bool = True,
    git_version: str = "git version 2.47.0",
    bwrap_ok: bool = True,
    vibe_version: str = "vibe 3.1.0",
    in_repo: bool = True,
    has_commit: bool = True,
    sandbox_cmd: str = "vibe-sandbox",
) -> dict:
    """Keyword arguments for run_checks describing a fake machine."""
    home = tmp_path / "home"
    for name in (".local/bin", ".local/share/uv", ".vibe"):
        if name not in drop_dirs:
            (home / name).mkdir(parents=True, exist_ok=True)
    tools = {
        "git",
        "bwrap",
        "vibe",
        "xdg-dbus-proxy",
        Path(shlex.split(sandbox_cmd)[0]).name,
    } - set(missing)

    def which(name: str) -> str | None:
        return f"/usr/bin/{Path(name).name}" if Path(name).name in tools else None

    def run(cmd: list[str], cwd=None, **_kw) -> subprocess.CompletedProcess:
        def done(code: int, out: str = "") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(cmd, code, out, "")

        name, args = Path(cmd[0]).name, cmd[1:]
        if name == "git" and args == ["--version"]:
            return done(0, git_version)
        if name == "bwrap":
            return done(0 if bwrap_ok else 1)
        if name == "vibe":
            return done(0, vibe_version)
        if name == "git" and args[0] == "rev-parse":
            if not in_repo or ("--verify" in args and not has_commit):
                return done(128)
            return done(0, "/work/repo")
        raise AssertionError(f"unexpected command {cmd}")

    environ = {**({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"} if dbus else {}), **(env or {})}
    return {
        "cfg": Config(sandbox_cmd=sandbox_cmd),
        "home": home,
        "which": which,
        "run": run,
        "environ": environ,
        "cwd": tmp_path,
    }


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def test_a_healthy_machine_passes_every_check(tmp_path: Path) -> None:
    checks = run_checks(**make_world(tmp_path))
    assert [c.name for c in checks] == NAMES
    assert [c.status for c in checks] == ["ok"] * len(NAMES), checks


@pytest.mark.parametrize(
    "world, name, status, fragment",
    [
        ({"missing": ("git",)}, "git", "fail", "git"),
        ({"git_version": "git version 2.20.1"}, "git", "fail", "2.22"),
        ({"missing": ("bwrap",)}, "bwrap", "fail", "bubblewrap"),
        ({"bwrap_ok": False}, "bwrap", "fail", "namespaces"),
        ({"missing": ("vibe-sandbox",)}, "sandbox", "fail", "contrib/vibe-sandbox"),
        ({"missing": ("vibe",)}, "vibe", "fail", "uv tool install mistral-vibe"),
        ({"vibe_version": "vibe 4.0.0"}, "vibe", "warn", "3.1"),
        ({"drop_dirs": (".vibe",)}, "sandbox paths", "fail", "vibe --setup"),
        ({"drop_dirs": (".local/share/uv",)}, "sandbox paths", "fail", "uv tool install"),
        ({"dbus": False}, "auth", "fail", "DBUS_SESSION_BUS_ADDRESS"),
        ({"dbus": False}, "auth", "fail", "MISTRAL_API_KEY"),
        ({"missing": ("xdg-dbus-proxy",)}, "auth", "fail", "xdg-dbus-proxy"),
        ({"in_repo": False}, "repo", "warn", "not inside a git repository"),
        ({"has_commit": False}, "repo", "warn", "no commits"),
    ],
)
def test_each_problem_is_reported_with_a_fix(
    tmp_path: Path, world: dict, name: str, status: str, fragment: str
) -> None:
    check = by_name(run_checks(**make_world(tmp_path, **world)))[name]
    assert check.status == status
    assert fragment in check.detail


def test_an_api_key_makes_the_keyring_requirements_moot(tmp_path: Path) -> None:
    world = make_world(
        tmp_path, dbus=False, missing=("xdg-dbus-proxy",), env={"MISTRAL_API_KEY": "k"}
    )
    auth = by_name(run_checks(**world))["auth"]
    assert auth.status == "ok" and "MISTRAL_API_KEY" in auth.detail
    assert "k" not in auth.detail.replace("MISTRAL_API_KEY", "")  # never echo the key


def test_a_custom_sandbox_skips_the_reference_sandbox_checks(tmp_path: Path) -> None:
    world = make_world(
        tmp_path,
        sandbox_cmd="/opt/my-sandbox --flag",
        drop_dirs=(".vibe", ".local/share/uv"),
        dbus=False,
    )
    checks = by_name(run_checks(**world))
    assert "sandbox paths" not in checks
    assert checks["sandbox"].status == "ok"
    assert checks["auth"].status == "warn"  # cannot verify a login for a custom sandbox
