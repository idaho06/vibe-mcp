from pathlib import Path

import pytest

from .conftest import finish, sh, spec

pytestmark = pytest.mark.anyio


async def test_serial_mode_runs_one_job_at_a_time(make_manager) -> None:
    mgr = make_manager(isolation="serial", max_concurrent=5)
    jobs = [mgr.submit(spec(sleep=0.2), scope=[name]) for name in ("a", "b", "c")]
    assert [j.state for j in jobs] == ["running", "queued", "queued"]
    await finish(mgr, *jobs)
    assert jobs[1].started_at >= jobs[0].ended_at
    assert jobs[2].started_at >= jobs[1].ended_at


async def test_serial_jobs_accumulate_on_each_other(make_manager, repo: Path) -> None:
    mgr = make_manager(isolation="serial")
    main_head = sh("git", "rev-parse", "HEAD", cwd=repo)
    first = mgr.submit(spec(write={"x.txt": "x\n"}, sleep=0.2))
    second = mgr.submit(spec(write={"y.txt": "y\n"}))  # submitted while `first` still runs
    third = mgr.submit(spec())
    await finish(mgr, first, second, third)

    assert first.base_ref == main_head
    assert second.base_ref == first.commit
    assert third.base_ref == second.commit
    assert (third.worktree / "x.txt").exists() and (third.worktree / "y.txt").exists()
    assert not (repo / "x.txt").exists()


async def test_serial_mode_skips_unusable_jobs_when_choosing_the_base(make_manager) -> None:
    mgr = make_manager(isolation="serial")
    good = mgr.submit(spec(write={"a.txt": "a\n"}))
    broken = mgr.submit(spec(write={"b.txt": "b\n"}, exit=1))
    after = mgr.submit(spec())
    await finish(mgr, good, broken, after)
    assert broken.state == "failed"
    assert after.base_ref == good.commit


async def test_explicit_base_job_overrides_the_serial_default(make_manager) -> None:
    mgr = make_manager(isolation="serial")
    first = mgr.submit(spec(write={"a.txt": "a\n"}))
    second = mgr.submit(spec(write={"b.txt": "b\n"}))
    third = mgr.submit(spec(), base_job=first.id)
    await finish(mgr, first, second, third)
    assert third.base_ref == first.commit
    assert not (third.worktree / "b.txt").exists()


async def test_worktree_mode_does_not_chain_by_default(make_manager, repo: Path) -> None:
    mgr = make_manager(isolation="worktree")
    first = mgr.submit(spec(write={"only-in-first.txt": "a\n"}))
    await finish(mgr, first)
    second = mgr.submit(spec())
    await finish(mgr, second)
    assert second.base_ref == sh("git", "rev-parse", "HEAD", cwd=repo)
    assert not (second.worktree / "only-in-first.txt").exists()
