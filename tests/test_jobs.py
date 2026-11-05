import json
import re
from pathlib import Path

from vibe_mcp.jobs import Job, load, load_all, new_id, run_dir_for


def make(tmp_path: Path, job_id: str = "a1b2c3", **kw) -> Job:
    return Job(id=job_id, run_dir=run_dir_for(tmp_path, job_id), spec="do it", **kw)


def test_new_id_is_six_hex_chars_and_avoids_taken_ids() -> None:
    ids = {new_id() for _ in range(50)}
    assert all(re.fullmatch(r"[0-9a-f]{6}", i) for i in ids)
    assert new_id(taken=ids) not in ids


def test_run_dir_is_short_and_named_for_the_sandbox(tmp_path: Path) -> None:
    assert run_dir_for(tmp_path, "a1b2c3") == tmp_path / "runs" / "vibe-run.a1b2c3"


def test_defaults_and_derived_branch(tmp_path: Path) -> None:
    job = make(tmp_path)
    assert job.state == "queued"
    assert job.active
    assert job.branch == "vibe/a1b2c3"
    assert job.scope == ()


def test_active_follows_state(tmp_path: Path) -> None:
    assert make(tmp_path, state="running").active
    for state in ("finished", "limit", "failed", "cancelled", "lost", "applied", "discarded"):
        assert not make(tmp_path, state=state).active


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    job = make(
        tmp_path,
        title="t",
        scope=("src", "README.md"),
        base_job="ffffff",
        repo=tmp_path / "repo",
        worktree=tmp_path / "wt",
        max_price=0.5,
        cost_usd=0.013,
        final_message="Fixed.",
    )
    job.save()
    loaded = load(job.run_dir)
    assert loaded == job
    assert isinstance(loaded.scope, tuple)
    assert isinstance(loaded.worktree, Path)
    assert isinstance(loaded.repo, Path)
    assert isinstance(loaded.run_dir, Path)


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    job = make(tmp_path)
    job.save()
    job.state = "running"
    job.save()
    assert sorted(p.name for p in job.run_dir.iterdir()) == ["job.json"]
    assert load(job.run_dir).state == "running"


def test_load_tolerates_unknown_and_missing_keys(tmp_path: Path) -> None:
    job = make(tmp_path)
    job.save()
    data = json.loads((job.run_dir / "job.json").read_text())
    data["from_the_future"] = 1
    del data["title"]
    (job.run_dir / "job.json").write_text(json.dumps(data))
    assert load(job.run_dir).title == ""


def test_repo_defaults_to_none(tmp_path: Path) -> None:
    job = make(tmp_path)
    job.save()
    assert load(job.run_dir).repo is None


def test_load_all_sorts_by_creation_and_skips_junk(tmp_path: Path) -> None:
    late = make(tmp_path, "000002", created_at=200.0)
    early = make(tmp_path, "000001", created_at=100.0)
    late.save()
    early.save()
    (tmp_path / "runs" / "vibe-run.empty").mkdir()
    corrupt = tmp_path / "runs" / "vibe-run.bad"
    corrupt.mkdir()
    (corrupt / "job.json").write_text("{not json")
    (tmp_path / "runs" / "other").mkdir()
    assert [j.id for j in load_all(tmp_path)] == ["000001", "000002"]


def test_load_all_without_runs_dir(tmp_path: Path) -> None:
    assert load_all(tmp_path) == []
