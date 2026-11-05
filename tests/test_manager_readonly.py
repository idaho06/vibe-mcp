from pathlib import Path

import pytest

from vibe_mcp.jobs import Job, new_id, run_dir_for
from vibe_mcp.manager import Manager, ManagerError

from .conftest import finish, sh, spec

pytestmark = pytest.mark.anyio


def prepared(mgr: Manager, repo: Path, text: str, **kw) -> Job:
    """A Job built by hand, the way the fan-out code will build them."""
    job_id = new_id(mgr.jobs)
    return Job(id=job_id, run_dir=run_dir_for(mgr.cfg.data_dir, job_id), repo=repo, spec=text, **kw)


def vibe_branches(repo: Path) -> str:
    return sh("git", "for-each-ref", "--format=%(refname:short)", "refs/heads/vibe/", cwd=repo)


async def test_enqueue_starts_a_prepared_job(make_manager, repo: Path) -> None:
    mgr = make_manager()
    job = mgr.enqueue(prepared(mgr, repo, spec(write={"x.txt": "x"})))
    assert job.state == "running" and mgr.get(job.id) is job
    await finish(mgr, job)
    assert job.state == "finished" and job.commit


async def test_a_readonly_job_never_commits_and_gives_its_worktree_back(make_manager, repo) -> None:
    mgr = make_manager()
    job = mgr.enqueue(prepared(mgr, repo, spec(write={"stray.txt": "x"}), readonly=True))
    await finish(mgr, job)

    assert job.state == "finished"  # not "discarded": stats stay honest
    assert job.commit is None
    assert job.warning and "read-only" in job.warning  # it changed a file anyway
    assert job.worktree is None and not mgr._worktree_path(job).exists()
    assert vibe_branches(repo) == ""  # reviewers are detached: no branch to leave behind
    assert sh("git", "status", "--porcelain", cwd=repo) == ""


async def test_a_clean_readonly_run_has_no_warning(make_manager, repo: Path) -> None:
    mgr = make_manager()
    job = mgr.enqueue(prepared(mgr, repo, spec(message="no findings"), readonly=True))
    await finish(mgr, job)
    assert job.warning is None and job.final_message == "no findings"
    assert job.worktree is None


async def test_a_readonly_job_cannot_be_applied(make_manager, repo: Path) -> None:
    mgr = make_manager()
    job = mgr.enqueue(prepared(mgr, repo, spec(), readonly=True))
    await finish(mgr, job)
    with pytest.raises(ManagerError, match="read-only"):
        await mgr.apply(job.id)


async def test_readonly_jobs_ignore_scope_overlaps(make_manager, repo: Path) -> None:
    mgr = make_manager(max_concurrent=6)
    writer = mgr.submit(spec(sleep=0.5), scope=["src"])
    readers = [
        mgr.enqueue(prepared(mgr, repo, spec(sleep=0.3), readonly=True, scope=scope))
        for scope in ((), ("src",), ("src/sub",))
    ]
    assert [j.state for j in (writer, *readers)] == ["running"] * 4
    # the whole-repo reader holds no scope: a writer on another path is not queued behind it
    other = mgr.submit(spec(), scope=["docs"])
    assert other.state == "running"
    await finish(mgr, writer, *readers, other)


async def test_a_pinned_base_survives_commits_made_before_the_job_starts(
    make_manager, repo
) -> None:
    mgr = make_manager()
    pinned = sh("git", "rev-parse", "HEAD", cwd=repo)
    job = prepared(mgr, repo, spec(), base_ref=pinned)
    (repo / "later.txt").write_text("committed after the job was queued\n")
    sh("git", "add", "later.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "later", cwd=repo)

    mgr.enqueue(job)
    await finish(mgr, job)

    assert job.base_ref == pinned != sh("git", "rev-parse", "HEAD", cwd=repo)
    assert not (job.worktree / "later.txt").exists()
