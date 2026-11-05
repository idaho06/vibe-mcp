from pathlib import Path

import pytest

from vibe_mcp.manager import ManagerError

from .conftest import finish, spec

pytestmark = pytest.mark.anyio


def add_skill(base: Path, name: str, body: str) -> None:
    folder = base / ".vibe" / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")


async def test_a_skill_in_the_untracked_main_checkout_reaches_the_job(make_manager, repo) -> None:
    add_skill(repo, "simplify", "Simplify carefully.")  # untracked: not in the job's worktree
    mgr = make_manager()
    task = spec(write={"out.txt": "x"})

    job = mgr.submit(task, skill="simplify")
    await finish(mgr, job)

    assert job.skill == "simplify" and "Simplify carefully." in job.preamble
    prompt = (job.run_dir / "task.md").read_text()
    assert prompt.startswith("## Skill: simplify\n\nSimplify carefully.") and prompt.endswith(task)
    assert not (job.worktree / ".vibe").exists()
    assert job.state == "finished" and (job.worktree / "out.txt").exists()


async def test_a_global_skill_is_found_through_home(
    make_manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "fakehome"
    add_skill(home, "review", "Review carefully.")
    monkeypatch.setenv("HOME", str(home))
    job = make_manager().submit(spec(), skill="review")
    assert "Review carefully." in job.preamble


async def test_an_unknown_skill_is_rejected_before_anything_is_queued(make_manager, repo) -> None:
    add_skill(repo, "simplify", "x")
    mgr = make_manager()
    with pytest.raises(ManagerError, match=r"unknown skill 'simplyfy'; available: simplify"):
        mgr.submit(spec(), skill="simplyfy")
    assert mgr.jobs == {}


async def test_without_a_skill_the_prompt_is_just_the_spec(make_manager) -> None:
    mgr = make_manager()
    task = spec()
    job = mgr.submit(task)
    await finish(mgr, job)
    assert job.skill is None and job.preamble == ""
    assert (job.run_dir / "task.md").read_text() == task
