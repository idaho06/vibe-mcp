from pathlib import Path

import pytest

from vibe_mcp.jobs import Job, new_id, run_dir_for
from vibe_mcp.manager import Manager, ManagerError

from .conftest import alive, finish, spec, wait_for_file

pytestmark = pytest.mark.anyio


def prepared(mgr: Manager, repo: Path, text: str, **kw) -> Job:
    job_id = new_id(mgr.jobs)
    return Job(id=job_id, run_dir=run_dir_for(mgr.cfg.data_dir, job_id), repo=repo, spec=text, **kw)


def group(
    mgr: Manager, repo: Path, *finder_specs: str, merge_spec: str = "{}"
) -> tuple[list[Job], Job]:
    """Hand-built fan-out: hidden read-only finders, and a merge job that depends on them."""
    merge_id = new_id(mgr.jobs)
    finders = [
        mgr.enqueue(
            prepared(mgr, repo, text, readonly=True, hidden=True, parent=merge_id, angle=f"a{i}")
        )
        for i, text in enumerate(finder_specs)
    ]
    merge = prepared(mgr, repo, merge_spec, depends_on=tuple(f.id for f in finders))
    merge.id, merge.run_dir = merge_id, run_dir_for(mgr.cfg.data_dir, merge_id)
    return finders, mgr.enqueue(merge)


async def test_a_job_waits_for_everything_it_depends_on(make_manager, repo: Path) -> None:
    mgr = make_manager()
    finders, merge = group(mgr, repo, spec(sleep=0.4), spec(sleep=0.1))
    assert merge.state == "queued"
    await finish(mgr, *finders, merge)
    assert merge.state == "finished"
    assert merge.started_at >= max(f.ended_at for f in finders)


async def test_a_dependent_still_runs_when_some_dependencies_failed(
    make_manager, repo: Path
) -> None:
    mgr = make_manager()
    finders, merge = group(mgr, repo, spec(exit=1), spec(message="found it"))
    await finish(mgr, *finders, merge)
    assert [f.state for f in finders] == ["failed", "finished"]
    assert merge.state == "finished"


async def test_a_dependent_fails_clearly_when_none_of_its_dependencies_finished(
    make_manager, repo: Path
) -> None:
    mgr = make_manager()
    finders, merge = group(mgr, repo, spec(exit=1), spec(exit=1))
    await finish(mgr, *finders, merge)
    assert merge.state == "failed"
    assert merge.error and "none of the jobs it depends on finished" in merge.error
    assert all(f.id in merge.error for f in finders)


async def test_an_unknown_dependency_is_rejected_before_anything_is_queued(
    make_manager, repo
) -> None:
    mgr = make_manager()
    with pytest.raises(ManagerError, match="unknown job ffffff"):
        mgr.enqueue(prepared(mgr, repo, spec(), depends_on=("ffffff",)))
    assert mgr.jobs == {}


async def test_hidden_jobs_are_left_out_of_the_default_wait_but_can_be_asked_for(
    make_manager, repo: Path
) -> None:
    mgr = make_manager()
    hidden = mgr.enqueue(prepared(mgr, repo, spec(sleep=60), readonly=True, hidden=True))
    assert await mgr.wait(timeout=0) == []  # nothing visible is active
    visible = mgr.submit(spec(sleep=60), scope=["x"])
    assert await mgr.wait(timeout=0) == [visible]
    assert await mgr.wait([hidden.id], timeout=0) == [hidden]
    await mgr.cancel(hidden.id)
    await mgr.cancel(visible.id)


async def test_a_job_cannot_be_discarded_while_another_waits_on_it(
    make_manager, repo: Path
) -> None:
    mgr = make_manager()
    finders, merge = group(mgr, repo, spec(), spec(sleep=60))
    await mgr.wait([finders[0].id], timeout=20)
    assert finders[0].state == "finished" and merge.state == "queued"
    with pytest.raises(ManagerError, match=rf"{merge.id}.*job {finders[0].id}"):
        await mgr.discard(finders[0].id)
    await mgr.cancel(merge.id)


async def test_cancelling_the_merge_cancels_its_reviewers_first(make_manager, repo: Path) -> None:
    mgr = make_manager()
    finders, merge = group(mgr, repo, spec(sleep=60, spawn_child=True), spec(sleep=60))
    await wait_for_file(finders[0].run_dir / "child.pid")
    child = int((finders[0].run_dir / "child.pid").read_text())

    await mgr.cancel(merge.id)

    assert merge.state == "cancelled"  # not "failed": the reviewers were cancelled after it
    assert [f.state for f in finders] == ["cancelled", "cancelled"]
    assert not alive(child)


async def test_cancelling_one_reviewer_does_not_stop_the_merge(make_manager, repo: Path) -> None:
    mgr = make_manager()
    finders, merge = group(mgr, repo, spec(sleep=60), spec(sleep=0.2))
    await mgr.cancel(finders[0].id)
    await finish(mgr, merge)
    assert (finders[0].state, finders[1].state, merge.state) == (
        "cancelled",
        "finished",
        "finished",
    )
