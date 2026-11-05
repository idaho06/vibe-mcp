import pytest

from vibe_mcp.manager import Details

from .conftest import finish, spec

pytestmark = pytest.mark.anyio


async def test_details_describe_what_the_job_changed(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(
        spec(write={"src/ok.py": "x = 1\n", "other.py": "y = 2\n", "a.txt": "changed\n"}),
        scope=["src"],
    )
    await finish(mgr, job)

    details = await mgr.details(job.id)

    assert sorted(details.files) == ["a.txt", "other.py", "src/ok.py"]
    assert sorted(details.added) == ["other.py", "src/ok.py"]
    assert sorted(details.violations) == ["a.txt", "other.py"]
    assert "3 files changed" in details.stat
    assert details.diff is None

    with_diff = await mgr.details(job.id, with_diff=True)
    assert with_diff.diff and "+x = 1" in with_diff.diff


async def test_details_of_a_job_without_a_commit_are_empty(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec())
    await finish(mgr, job)
    assert await mgr.details(job.id, with_diff=True) == Details()


async def test_details_still_work_after_apply(make_manager) -> None:
    mgr = make_manager()
    job = mgr.submit(spec(write={"new.py": "x\n"}))
    await finish(mgr, job)
    await mgr.apply(job.id)
    assert (await mgr.details(job.id)).added == ["new.py"]
