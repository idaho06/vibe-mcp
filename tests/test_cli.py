import json
from pathlib import Path

import pytest

from vibe_mcp import cli
from vibe_mcp.jobs import Job, run_dir_for


def saved(data_dir: Path, job_id: str, **kw) -> Job:
    job = Job(id=job_id, run_dir=run_dir_for(data_dir, job_id), spec="s", **kw)
    job.save()
    return job


def sample_jobs(tmp_path: Path) -> list[Job]:
    return [
        Job(id="000001", run_dir=tmp_path, spec="s", state="finished",
            started_at=0, ended_at=60, cost_usd=0.10, tokens=1000),
        Job(id="000002", run_dir=tmp_path, spec="s", state="applied",
            started_at=0, ended_at=180, cost_usd=0.30, tokens=3000),
        Job(id="000003", run_dir=tmp_path, spec="s", state="cancelled"),  # never started
    ]  # fmt: skip


def test_stats_data(tmp_path: Path) -> None:
    data = cli.stats_data(sample_jobs(tmp_path))
    assert data["jobs"] == 3
    assert data["states"] == {"finished": 1, "applied": 1, "cancelled": 1}
    assert data["cost_usd"] == pytest.approx(0.40)
    assert data["avg_cost_usd"] == pytest.approx(0.20)  # over the jobs that actually ran
    assert data["tokens"] == 4000
    assert (data["seconds"], data["avg_seconds"]) == (240, 120)


def test_stats_data_of_nothing() -> None:
    data = cli.stats_data([])
    assert data["jobs"] == 0 and data["avg_cost_usd"] == 0 and data["avg_seconds"] == 0


def test_stats_text(tmp_path: Path) -> None:
    text = cli.stats_text(cli.stats_data(sample_jobs(tmp_path)))
    assert "jobs: 3" in text and "finished 1" in text and "applied 1" in text
    assert "$0.4000 total" in text and "$0.2000 avg" in text
    assert "4,000" in text
    assert "4m00s total" in text and "2m00s avg" in text
    assert cli.stats_text(cli.stats_data([])) == "no jobs yet"


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    path = tmp_path / "config.toml"
    path.write_text(f'data_dir = "{data_dir}"\n')
    monkeypatch.setenv("VIBE_MCP_CONFIG", str(path))
    return data_dir


def test_stats_command_reads_every_repos_jobs(config_file: Path, capsys) -> None:
    saved(config_file, "000001", state="finished", started_at=0, ended_at=30, cost_usd=0.5)
    saved(config_file, "000002", state="failed", repo=Path("/some/other/repo"))
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "jobs: 2" in out and "$0.5000 total" in out


def test_stats_command_json(config_file: Path, capsys) -> None:
    saved(config_file, "000001", state="finished", started_at=0, ended_at=30, cost_usd=0.5)
    assert cli.main(["stats", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cost_usd"] == 0.5


def test_stats_command_with_no_jobs(config_file: Path, capsys) -> None:
    assert cli.main(["stats"]) == 0
    assert capsys.readouterr().out.strip() == "no jobs yet"


class FakeServer:
    def __init__(self) -> None:
        self.ran = False

    def run(self) -> None:
        self.ran = True


@pytest.mark.parametrize("argv", [[], ["serve"]])
def test_serve_is_the_default_command(
    argv: list[str], repo: Path, config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    server = FakeServer()
    monkeypatch.setattr(cli, "build_server", lambda *args, **kw: server)
    monkeypatch.chdir(repo)
    assert cli.main(argv) == 0
    assert server.ran
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout belongs to the MCP protocol
    # say which repo is served: a wrapper that changes directory would silently pick another one
    assert f"serving git repo {repo}" in captured.err


def test_serve_outside_a_repo_exits_with_a_clear_message(
    tmp_path: Path, config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(["serve"]) == 2
    captured = capsys.readouterr()
    assert "not inside a git repository" in captured.err and captured.out == ""


def test_a_bad_config_exits_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = 1\n")
    monkeypatch.setenv("VIBE_MCP_CONFIG", str(bad))
    for argv in (["stats"], ["serve"]):
        assert cli.main(argv) == 2
        assert "bogus" in capsys.readouterr().err


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["bogus"])
    assert exc.value.code == 2


def test_doctor_reports_and_exits_nonzero_only_on_failures(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from vibe_mcp.doctor import Check

    checks = [Check("git", "ok", "git version 2.47.0"), Check("repo", "warn", "no commits")]
    monkeypatch.setattr(cli, "run_checks", lambda cfg: checks)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "[OK  ] git: git version 2.47.0" in out and "[WARN] repo: no commits" in out

    checks.append(Check("bwrap", "fail", "bubblewrap is not installed"))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] bwrap: bubblewrap is not installed" in out and "1 problem" in out
