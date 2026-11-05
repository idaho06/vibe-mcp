from pathlib import Path

import pytest

from vibe_mcp.config import Config, ConfigError, load


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    cfg = load(tmp_path / "nope.toml")
    assert cfg == Config(data_dir=Path.home() / ".local/share/vibe-mcp")
    assert cfg.isolation == "worktree"
    assert (cfg.max_concurrent, cfg.max_turns, cfg.time_limit, cfg.max_price) == (3, 200, 1200, 0)
    assert cfg.model == "mistral-large-4"
    assert cfg.sandbox_cmd == "vibe-sandbox"


def test_overrides_and_tilde_expansion(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        'isolation = "serial"\nmax_turns = 50\nmax_price = 0.5\ndata_dir = "~/vm"\n',
    )
    cfg = load(path)
    assert cfg.isolation == "serial"
    assert cfg.max_turns == 50
    assert cfg.max_price == 0.5
    assert cfg.data_dir == Path.home() / "vm"


def test_serial_forces_one_concurrent_job(tmp_path: Path) -> None:
    cfg = load(write(tmp_path, 'isolation = "serial"\nmax_concurrent = 5\n'))
    assert cfg.concurrency == 1
    assert load(write(tmp_path, "max_concurrent = 5\n")).concurrency == 5


def test_env_var_selects_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, "max_turns = 7\n")
    monkeypatch.setenv("VIBE_MCP_CONFIG", str(path))
    assert load().max_turns == 7


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("bogus = 1\n", "bogus"),
        ('max_turns = "ten"\n', "max_turns"),
        ("max_turns = true\n", "max_turns"),
        ("max_turns = 0\n", "max_turns"),
        ("max_concurrent = 0\n", "max_concurrent"),
        ("max_price = -1\n", "max_price"),
        ('isolation = "chaos"\n', "isolation"),
        ("model = 3\n", "model"),
        ("this is not toml\n", "config.toml"),
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, text: str, fragment: str) -> None:
    with pytest.raises(ConfigError, match=fragment):
        load(write(tmp_path, text))


def test_data_dir_too_long_for_the_sandbox_socket(tmp_path: Path) -> None:
    long_dir = tmp_path / ("x" * 100)
    with pytest.raises(ConfigError, match="data_dir"):
        load(write(tmp_path, f'data_dir = "{long_dir}"\n'))
