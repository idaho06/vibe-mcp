from pathlib import Path

import pytest

from .conftest import finish, spec

pytestmark = pytest.mark.anyio

CONTEXT = "## Project context: AGENTS.md\n\nStandards.\n\n---\n\n"


async def test_untracked_project_docs_reach_the_job(make_manager, repo: Path) -> None:
    (repo / "AGENTS.md").write_text("Standards.\n")  # untracked, so not in the worktree
    mgr = make_manager(context_files=("AGENTS.md",))
    task = spec(write={"out.txt": "x"})

    job = mgr.submit(task)
    await finish(mgr, job)

    assert job.preamble == CONTEXT + "## Task\n\n"
    assert (job.run_dir / "task.md").read_text() == job.preamble + task
    assert not (job.worktree / "AGENTS.md").exists()
    assert job.state == "finished" and (job.worktree / "out.txt").exists()


async def test_context_comes_before_the_skill_before_the_task(make_manager, repo: Path) -> None:
    (repo / "AGENTS.md").write_text("Standards.\n")
    folder = repo / ".vibe" / "skills" / "simplify"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: simplify\n---\nSteps.")
    mgr = make_manager(context_files=("AGENTS.md",))

    job = mgr.submit(spec(), skill="simplify")

    assert job.preamble == CONTEXT + "## Skill: simplify\n\nSteps.\n\n---\n\n## Task\n\n"
    assert job.preamble.count("## Task") == 1


async def test_without_any_context_the_prompt_is_just_the_spec(make_manager) -> None:
    mgr = make_manager(context_files=("NOT-THERE.md",))
    job = mgr.submit(spec())
    assert job.preamble == "" and job.prompt == job.spec
