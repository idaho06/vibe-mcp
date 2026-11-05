"""Configuration: a frozen dataclass loaded from a TOML file, validated strictly."""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_PATH = Path("~/.config/vibe-mcp/config.toml")
ISOLATION_MODES = ("worktree", "serial")
# vibe-sandbox creates RUN/bus, a Unix socket; sun_path holds at most 107 characters.
_SOCKET_LIMIT = 107
_LONGEST_SOCKET_SUFFIX = "runs/vibe-run.abcdef/bus"


class ConfigError(Exception):
    """The configuration file is unreadable or invalid."""


def _default_data_dir() -> Path:
    return Path.home() / ".local/share/vibe-mcp"


@dataclass(frozen=True)
class Config:
    isolation: str = "worktree"
    max_concurrent: int = 3
    model: str = "mistral-large-4"
    sandbox_cmd: str = "vibe-sandbox"
    data_dir: Path = field(default_factory=_default_data_dir)
    max_turns: int = 200
    time_limit: int = 1200
    max_price: float = 0
    max_message_chars: int = 2000
    max_diff_bytes: int = 20000

    @property
    def concurrency(self) -> int:
        """Serial isolation always runs one job at a time."""
        return 1 if self.isolation == "serial" else self.max_concurrent


_POSITIVE_INTS = (
    "max_concurrent",
    "max_turns",
    "time_limit",
    "max_message_chars",
    "max_diff_bytes",
)
_STRINGS = ("model", "sandbox_cmd")


def _check(key: str, value: object) -> object:
    if key in _POSITIVE_INTS:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"{key} must be an integer >= 1, got {value!r}")
    elif key == "max_price":
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ConfigError(f"{key} must be a number >= 0, got {value!r}")
    elif key in _STRINGS:
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{key} must be a non-empty string, got {value!r}")
    elif key == "isolation":
        if value not in ISOLATION_MODES:
            raise ConfigError(f"{key} must be one of {ISOLATION_MODES}, got {value!r}")
    elif key == "data_dir":
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{key} must be a non-empty string, got {value!r}")
        path = Path(value).expanduser()
        if len(str(path / _LONGEST_SOCKET_SUFFIX)) > _SOCKET_LIMIT:
            raise ConfigError(
                f"data_dir {str(path)!r} is too long: the sandbox socket path would exceed "
                f"{_SOCKET_LIMIT} characters"
            )
        return path
    return value


def load(path: Path | None = None) -> Config:
    """Load the config from `path`, else $VIBE_MCP_CONFIG, else the default. No file = defaults."""
    if path is None:
        path = Path(os.environ.get("VIBE_MCP_CONFIG") or DEFAULT_PATH)
    path = path.expanduser()
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    known = {f.name for f in fields(Config)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown option(s) in {path}: {', '.join(unknown)}")
    return Config(**{key: _check(key, value) for key, value in raw.items()})
