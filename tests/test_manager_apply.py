import asyncio
from pathlib import Path

import pytest

from vibe_mcp import jobs
from vibe_mcp.manager import ManagerError

from .conftest import finish, sh, spec

pytestmark = pytest.mark.anyio


def branches(repo: Path) -> str:
    return sh("git", "for-each-ref", "--format=%(refname:short)", "refs/heads/vibe/", cwd=repo)


async def test_apply_stages_the_changes_without_committing(make_manager, repo: Path) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"new.py": "x = 1\n", "a.txt": "changed\n"}))
    await finish(mgr, job)
    main_head = sh("git", "rev-parse", "HEAD", cwd=repo)

    assert await mgr.apply(job.id) == []

    assert sh("git", "rev-parse", "HEAD", cwd=repo) == main_head
    assert sh("git", "diff", "--cached", "--name-only", cwd=repo).split() == ["a.txt", "new.py"]
    assert (repo / "new.py").read_text() == "x = 1\n"
    assert job.state == "applied"
    assert not job.worktree.exists()
    assert branches(repo) == ""
    assert jobs.load(job.run_dir).state == "applied"


async def test_apply_of_a_job_with_no_changes_just_closes_it(make_manager, repo: Path) -> None:
    mgr = make_manager()
    job = mgr.submit(spec())
    await finish(mgr, job)
    assert await mgr.apply(job.id) == []
    assert job.state == "applied" and branches(repo) == ""
    assert sh("git", "diff", "--cached", "--name-only", cwd=repo) == ""


async def test_apply_refuses_a_main_checkout_with_tracked_changes(make_manager, repo) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"new.py": "x\n"}))
    await finish(mgr, job)

    (repo / "a.txt").write_text("edited\n")
    with pytest.raises(ManagerError, match="uncommitted"):
        await mgr.apply(job.id)
    sh("git", "add", "a.txt", cwd=repo)
    with pytest.raises(ManagerError, match="uncommitted"):
        await mgr.apply(job.id)
    assert job.state == "finished" and job.worktree.exists()

    sh("git", "reset", "-q", "--hard", cwd=repo)
    (repo / "untracked.txt").write_text("fine\n")  # untracked files do not block
    assert await mgr.apply(job.id) == []


async def test_apply_conflict_is_reported_and_leaves_everything_in_place(
    make_manager, repo: Path
) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"a.txt": "job version\n"}))
    await finish(mgr, job)
    (repo / "a.txt").write_text("main version\n")
    sh("git", "commit", "-q", "-am", "main change", cwd=repo)
    main_head = sh("git", "rev-parse", "HEAD", cwd=repo)

    assert await mgr.apply(job.id) == ["a.txt"]

    assert job.state == "finished" and job.worktree.exists()
    assert branches(repo) == job.branch
    assert sh("git", "rev-parse", "HEAD", cwd=repo) == main_head
    assert sh("git", "status", "--porcelain", cwd=repo) == ""
    assert (repo / "a.txt").read_text() == "main version\n"


async def test_only_finished_or_limit_jobs_can_be_applied(make_manager) -> None:
    mgr = make_manager()
    failed = mgr.submit(spec(write={"f.txt": "x"}, exit=1), scope=["f"])
    running = mgr.submit(spec(sleep=60), scope=["r"])
    await mgr.wait([failed.id])
    with pytest.raises(ManagerError, match="failed"):
        await mgr.apply(failed.id)
    with pytest.raises(ManagerError, match="running"):
        await mgr.apply(running.id)
    with pytest.raises(ManagerError, match="unknown job"):
        await mgr.apply("ffffff")
    await mgr.cancel(running.id)


async def test_a_limit_job_can_be_applied_and_not_twice(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"p.txt": "partial\n"}, exit=3, outcome="limit"))
    await finish(mgr, job)
    assert await mgr.apply(job.id) == []
    with pytest.raises(ManagerError, match="already applied"):
        await mgr.apply(job.id)
    with pytest.raises(ManagerError, match="already applied"):
        await mgr.discard(job.id)


async def test_discard_removes_worktree_and_branch(make_manager, repo: Path) -> None:
    mgr = make_manager()
    failed = mgr.submit(spec(write={"f.txt": "x"}, exit=1), scope=["f"])
    finished = mgr.submit(spec(write={"g.txt": "x"}), scope=["g"])
    cancelled = mgr.submit(spec(write={"c.txt": "wip"}, sleep=60), scope=["c"])
    await mgr.wait([failed.id, finished.id], all_=True)
    await mgr.cancel(cancelled.id)  # leaves an uncommitted worktree behind

    for job in (failed, finished, cancelled):
        await mgr.discard(job.id)
        assert job.state == "discarded"
        assert not job.worktree.exists()
        assert jobs.load(job.run_dir).state == "discarded"
    assert branches(repo) == ""
    assert sh("git", "status", "--porcelain", cwd=repo) == ""
    with pytest.raises(ManagerError, match="already discarded"):
        await mgr.discard(finished.id)


async def test_discard_of_a_queued_then_cancelled_job_is_fine(make_manager) -> None:
    mgr = make_manager()
    running = mgr.submit(spec(sleep=60))
    queued = mgr.submit(spec())
    await mgr.cancel(queued.id)
    await mgr.discard(queued.id)
    assert queued.state == "discarded"
    with pytest.raises(ManagerError, match="cancel"):
        await mgr.discard(running.id)
    await mgr.cancel(running.id)


async def test_apply_and_discard_wait_for_dependent_jobs(make_manager) -> None:
    mgr = make_manager(max_concurrent=1)
    base = mgr.submit(spec(write={"b.txt": "b\n"}))
    await finish(mgr, base)
    filler = mgr.submit(spec(sleep=60))
    dependent = mgr.submit(spec(), base_job=base.id)
    assert dependent.state == "queued"

    for action in (mgr.apply, mgr.discard):
        with pytest.raises(ManagerError, match=f"{dependent.id}.*job {base.id}"):
            await action(base.id)

    await mgr.cancel(dependent.id)
    await mgr.discard(base.id)
    assert base.state == "discarded"
    await mgr.cancel(filler.id)


@pytest.mark.parametrize("second", ["apply", "discard"])
async def test_racing_calls_on_one_job_cannot_both_win(make_manager, repo: Path, second) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"new.py": "x\n"}))
    await finish(mgr, job)

    first_result, second_result = await asyncio.gather(
        mgr.apply(job.id), getattr(mgr, second)(job.id), return_exceptions=True
    )

    assert first_result == []
    assert isinstance(second_result, ManagerError)
    assert "already applied" in str(second_result)  # not a leaked git error or "uncommitted"
    assert job.state == "applied"
