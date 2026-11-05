import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from vibe_mcp import jobs
from vibe_mcp.config import Config
from vibe_mcp.jobs import Job, run_dir_for
from vibe_mcp.manager import Manager, ManagerError, find_repo

from .conftest import alive, sh, wait_for_file

pytestmark = pytest.mark.anyio


def spec(**directive: object) -> str:
    return json.dumps(directive)


@pytest.fixture
async def make_manager(cfg: Config, repo: Path):
    managers: list[Manager] = []

    def factory(**overrides: object) -> Manager:
        manager = Manager(replace(cfg, **overrides), repo)
        managers.append(manager)
        return manager

    yield factory
    for manager in managers:
        await manager.shutdown()


async def finish(mgr: Manager, *js: Job, timeout: float = 20) -> None:
    await mgr.wait([j.id for j in js], timeout=timeout, all_=True)
    assert not any(j.active for j in js), "jobs did not finish in time"


async def test_job_runs_to_completion_in_its_own_worktree(make_manager, cfg, repo) -> None:
    mgr = make_manager()
    main_head = sh("git", "rev-parse", "HEAD", cwd=repo)

    job = mgr.submit(
        spec(write={"new.py": "x = 1\n"}, message="ok", cost=0.25, tokens=500), title="add new.py"
    )
    assert job.state == "running"
    await finish(mgr, job)

    assert job.state == "finished"
    assert (job.cost_usd, job.tokens, job.final_message) == (0.25, 500, "ok")
    assert job.base_ref == main_head
    assert job.worktree == cfg.data_dir / "worktrees" / f"repo-{job.id}"
    assert (job.worktree / "new.py").exists()
    assert job.commit == sh("git", "rev-parse", job.branch, cwd=repo)
    assert sh("git", "log", "-1", "--format=%s", job.branch, cwd=repo) == (
        f"vibe {job.id}: add new.py"
    )
    assert not (repo / "new.py").exists()
    assert sh("git", "rev-parse", "HEAD", cwd=repo) == main_head
    assert job.warning is None and job.pid is None
    assert job.started_at and job.ended_at and job.ended_at >= job.started_at
    saved = jobs.load(job.run_dir)
    assert (saved.state, saved.repo, saved.commit) == ("finished", repo, job.commit)


async def test_run_with_no_changes_makes_no_commit(make_manager, repo) -> None:
    mgr = make_manager()
    job = mgr.submit(spec())
    await finish(mgr, job)
    assert job.state == "finished"
    assert job.commit is None
    assert sh("git", "rev-parse", job.branch, cwd=repo) == job.base_ref


async def test_failed_and_limit_runs_keep_their_partial_work(make_manager) -> None:
    mgr = make_manager()
    failed = mgr.submit(spec(write={"f.txt": "partial"}, exit=1))
    limit = mgr.submit(
        spec(write={"l.txt": "partial"}, exit=3, outcome="limit"),
    )
    await finish(mgr, failed, limit)
    assert (failed.state, limit.state) == ("failed", "limit")
    assert failed.commit and limit.commit


async def test_failure_without_an_export_explains_itself(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(exit=2, no_export=True))
    await finish(mgr, job)
    assert job.state == "failed"
    assert job.error and "code 2" in job.error and "vibe-sandbox: exit=2" in job.error


async def test_missing_sandbox_command_fails_the_job(make_manager) -> None:
    mgr = make_manager(sandbox_cmd="/nonexistent/vibe-sandbox")
    job = mgr.submit(spec())
    await finish(mgr, job)
    assert job.state == "failed"
    assert job.error and "nonexistent" in job.error


async def test_submit_validation_and_budget_defaults(make_manager, cfg) -> None:
    mgr = make_manager()
    with pytest.raises(ManagerError, match="spec"):
        mgr.submit("   ")
    with pytest.raises(ManagerError, match="scope"):
        mgr.submit(spec(), scope=["../x"])
    with pytest.raises(ManagerError, match="max_turns"):
        mgr.submit(spec(), max_turns=0)
    with pytest.raises(ManagerError, match="time_limit"):
        mgr.submit(spec(), time_limit=0)
    with pytest.raises(ManagerError, match="max_price"):
        mgr.submit(spec(), max_price=-1)
    with pytest.raises(ManagerError, match="unknown job"):
        mgr.submit(spec(), base_job="ffffff")
    assert mgr.jobs == {}

    default = mgr.submit(spec())
    assert (default.max_turns, default.time_limit, default.max_price) == (200, 1200, 0)
    custom = mgr.submit(spec(), max_turns=5, time_limit=30, max_price=0.2, scope=["src/"])
    assert (custom.max_turns, custom.time_limit, custom.max_price) == (5, 30, 0.2)
    assert custom.scope == ("src",)


async def test_concurrency_limit(make_manager) -> None:
    mgr = make_manager(max_concurrent=2)
    a, b, c = (mgr.submit(spec(sleep=0.4), scope=[s]) for s in ("a", "b", "c"))
    assert [a.state, b.state, c.state] == ["running", "running", "queued"]
    await finish(mgr, a, b, c)
    assert c.state == "finished"
    assert c.started_at >= min(a.ended_at, b.ended_at)


async def test_overlapping_scopes_run_one_after_the_other(make_manager) -> None:
    mgr = make_manager()
    a = mgr.submit(spec(sleep=0.4), scope=["src"])
    b = mgr.submit(spec(), scope=["src/sub"])
    c = mgr.submit(spec(), scope=["docs"])
    assert [a.state, b.state, c.state] == ["running", "queued", "running"]
    await finish(mgr, a, b, c)
    assert b.started_at >= a.ended_at


async def test_a_waiting_job_reserves_its_scope(make_manager) -> None:
    mgr = make_manager()
    running = mgr.submit(spec(sleep=0.4), scope=["src/a"])
    whole_repo = mgr.submit(spec(sleep=0.2))
    docs = mgr.submit(spec(), scope=["docs"])  # disjoint from `running`, but behind `whole_repo`
    assert [running.state, whole_repo.state, docs.state] == ["running", "queued", "queued"]
    await finish(mgr, running, whole_repo, docs)
    assert whole_repo.started_at >= running.ended_at
    assert docs.started_at >= whole_repo.ended_at


async def test_base_job_chaining(make_manager, repo) -> None:
    mgr = make_manager()
    first = mgr.submit(spec(write={"chain.txt": "from first\n"}, sleep=0.3), scope=["chain.txt"])
    second = mgr.submit(spec(write={"two.txt": "2\n"}), base_job=first.id, scope=["two.txt"])
    assert second.state == "queued"  # disjoint scopes, but it must wait for its base
    await finish(mgr, first, second)
    assert second.state == "finished"
    assert second.base_ref == first.commit
    assert (second.worktree / "chain.txt").read_text() == "from first\n"
    assert not (repo / "chain.txt").exists()


async def test_dependent_fails_when_its_base_is_cancelled(make_manager) -> None:
    mgr = make_manager()
    base = mgr.submit(spec(sleep=60))
    dependent = mgr.submit(spec(), base_job=base.id)
    assert dependent.state == "queued"
    await mgr.cancel(base.id)
    assert dependent.state == "failed"
    assert dependent.error and base.id in dependent.error and "cancelled" in dependent.error


@pytest.mark.parametrize("state", ["applied", "discarded", "failed", "lost"])
async def test_dependent_fails_clearly_when_its_base_is_unusable(make_manager, state) -> None:
    mgr = make_manager()
    base = mgr.submit(spec())
    await finish(mgr, base)
    base.state = state
    dependent = mgr.submit(spec(), base_job=base.id)
    assert dependent.state == "failed"
    assert dependent.error and state in dependent.error and "HEAD" in dependent.error


async def test_cancel_queued_job_never_runs_it(make_manager) -> None:
    mgr = make_manager()
    running = mgr.submit(spec(sleep=60))
    queued = mgr.submit(spec())
    assert queued.state == "queued"
    await mgr.cancel(queued.id)
    assert queued.state == "cancelled" and queued.ended_at and queued.worktree is None
    await mgr.cancel(running.id)
    assert queued.state == "cancelled"


async def test_cancel_running_job_kills_it_but_keeps_the_worktree(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"w.txt": "wip"}, sleep=60, spawn_child=True))
    await wait_for_file(job.run_dir / "child.pid")
    child = int((job.run_dir / "child.pid").read_text())

    await mgr.cancel(job.id)

    assert job.state == "cancelled" and job.ended_at
    assert not alive(child)
    assert job.worktree.exists() and (job.worktree / "w.txt").exists()
    assert job.commit is None
    assert jobs.load(job.run_dir).state == "cancelled"


async def test_cancel_rejects_finished_and_unknown_jobs(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec())
    await finish(mgr, job)
    with pytest.raises(ManagerError, match="already finished"):
        await mgr.cancel(job.id)
    with pytest.raises(ManagerError, match="unknown job"):
        await mgr.cancel("ffffff")


async def test_wait_returns_when_any_job_ends(make_manager) -> None:
    mgr = make_manager()
    slow = mgr.submit(spec(sleep=60), scope=["a"])
    quick = mgr.submit(spec(), scope=["b"])
    started = time.monotonic()
    result = await mgr.wait([slow.id, quick.id], timeout=20)
    assert result == [slow, quick]
    assert quick.state == "finished" and slow.active
    assert time.monotonic() - started < 10


async def test_wait_all_times_out_and_zero_timeout_is_a_status_check(make_manager) -> None:
    mgr = make_manager()
    slow = mgr.submit(spec(sleep=60))
    started = time.monotonic()
    assert await mgr.wait([slow.id], timeout=0.3, all_=True) == [slow]
    assert 0.2 < time.monotonic() - started < 5 and slow.active
    started = time.monotonic()
    await mgr.wait([slow.id], timeout=0)
    assert time.monotonic() - started < 0.1


async def test_wait_defaults_to_active_jobs_and_handles_nothing_to_wait_for(make_manager) -> None:
    mgr = make_manager()
    assert await mgr.wait(timeout=5) == []
    job = mgr.submit(spec())
    assert await mgr.wait(timeout=20) == [job]
    assert job.state == "finished"
    assert await mgr.wait([job.id], timeout=5) == [job]  # already ended: returns at once
    with pytest.raises(ManagerError, match="unknown job"):
        await mgr.wait(["ffffff"])


async def test_jobs_left_active_by_a_dead_server_become_lost(cfg, repo, tmp_path) -> None:
    def saved(job_id: str, state: str, repo_path: Path, created: float) -> Job:
        job = Job(
            id=job_id,
            run_dir=run_dir_for(cfg.data_dir, job_id),
            spec="x",
            repo=repo_path,
            state=state,
            created_at=created,
        )
        job.save()
        return job

    saved("000001", "running", repo, 1)
    saved("000002", "queued", repo, 2)
    saved("000003", "finished", repo, 3)
    other = saved("000004", "running", tmp_path / "other", 4)

    mgr = Manager(cfg, repo)

    assert list(mgr.jobs) == ["000001", "000002", "000003"]
    assert [j.state for j in mgr.jobs.values()] == ["lost", "lost", "finished"]
    assert "server stopped" in (mgr.jobs["000001"].error or "")
    assert jobs.load(mgr.jobs["000001"].run_dir).state == "lost"
    assert jobs.load(other.run_dir).state == "running"


async def test_main_checkout_guard_warns_when_head_moves(make_manager, repo) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(sleep=0.5))
    await asyncio.sleep(0.15)
    sh("git", "commit", "--allow-empty", "-q", "-m", "committed meanwhile", cwd=repo)
    await finish(mgr, job)
    assert job.warning and "main checkout" in job.warning


async def test_find_repo(repo, tmp_path) -> None:
    assert await find_repo(repo / "sub") == repo
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    with pytest.raises(ManagerError, match="not inside a git repository"):
        await find_repo(elsewhere)


async def test_shutdown_kills_running_jobs(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(sleep=60, spawn_child=True))
    await wait_for_file(job.run_dir / "child.pid")
    child = int((job.run_dir / "child.pid").read_text())

    await mgr.shutdown()

    assert job.state == "cancelled" and "shut down" in (job.error or "")
    assert not alive(child)
    await mgr.shutdown()  # idempotent
