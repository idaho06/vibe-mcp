from pathlib import Path

import pytest

from vibe_mcp.format import result_text, status_line
from vibe_mcp.manager import ManagerError

from .conftest import finish, sh, spec

pytestmark = pytest.mark.anyio


def team_skill(repo: Path, *, readonly: bool = False) -> Path:
    """A fan-out skill in the untracked main checkout: two angles and merge instructions."""
    folder = repo / ".vibe" / "skills" / "team"
    (folder / "angles").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: team\n---\nMerge the findings.")
    (folder / "angles" / "reuse.md").write_text("Look for reuse.")
    (folder / "angles" / "efficiency.md").write_text("Look for waste.")
    if readonly:
        (folder / "fanout.toml").write_text("readonly = true\n")
    return folder


def reviewers_of(mgr, merge):
    return [mgr.get(i) for i in merge.depends_on]


TASK = spec(
    by_angle={
        "reuse": {"message": "a.py:3 - [HIGH] duplicate helper", "cost": 0.02},
        "efficiency": {"message": "(none)", "cost": 0.03},
    },
    merge={"write": {"merged.txt": "done\n"}, "message": "merged", "cost": 0.05},
)


async def test_a_fanout_skill_runs_reviewers_then_one_merge_job(make_manager, repo: Path) -> None:
    team_skill(repo)
    mgr = make_manager()

    merge = mgr.submit(TASK, skill="team", title="cleanup")

    assert merge.state == "queued" and merge.depends_on  # Claude gets the merge job's id
    reviewers = reviewers_of(mgr, merge)
    assert sorted(r.angle for r in reviewers) == ["efficiency", "reuse"]
    assert all(r.hidden and r.readonly and r.parent == merge.id for r in reviewers)
    assert [r.state for r in reviewers] == ["running", "running"]

    await finish(mgr, merge)

    assert merge.state == "finished" and merge.commit  # the merge job edited and committed
    assert (merge.worktree / "merged.txt").exists()
    # every reviewer ran read-only, left no branch behind, and gave its worktree back
    assert all(r.state == "finished" and r.commit is None and r.worktree is None for r in reviewers)
    branches = sh("git", "for-each-ref", "--format=%(refname:short)", "refs/heads/vibe/", cwd=repo)
    assert branches == merge.branch


async def test_each_reviewer_gets_its_own_angle_and_the_merge_gets_all_the_reports(
    make_manager, repo: Path
) -> None:
    team_skill(repo)
    mgr = make_manager()
    merge = mgr.submit(TASK, skill="team")
    await finish(mgr, merge)

    for r in reviewers_of(mgr, merge):
        prompt = (r.run_dir / "task.md").read_text()
        assert prompt.startswith("## Reviewer role")
        assert f"## Your angle: {r.angle}" in prompt
        assert {"reuse": "Look for reuse.", "efficiency": "Look for waste."}[r.angle] in prompt
        assert prompt.endswith(TASK)  # the task text comes last, after "## Task"
        assert "Merge the findings." not in prompt  # reviewers never see the merge instructions

    prompt = (merge.run_dir / "task.md").read_text()
    assert prompt.startswith("## Skill: team\n\nMerge the findings.")
    assert TASK in prompt
    assert "### reuse" in prompt and "a.py:3 - [HIGH] duplicate helper" in prompt
    assert "### efficiency" in prompt and "(none)" in prompt
    assert prompt.index(TASK) < prompt.index("## Findings from independent reviewers")


async def test_a_readonly_merge_reports_without_a_branch(make_manager, repo: Path) -> None:
    team_skill(repo, readonly=True)
    mgr = make_manager()
    merge = mgr.submit(TASK, skill="team")
    await finish(mgr, merge)
    assert merge.state == "finished" and merge.commit is None and merge.worktree is None
    assert sh("git", "for-each-ref", "refs/heads/vibe/", cwd=repo) == ""
    with pytest.raises(ManagerError, match="read-only"):
        await mgr.apply(merge.id)


async def test_the_merge_job_can_be_applied_like_any_other(make_manager, repo: Path) -> None:
    team_skill(repo)
    mgr = make_manager()
    merge = mgr.submit(TASK, skill="team")
    await finish(mgr, merge)
    assert await mgr.apply(merge.id) == []
    assert (repo / "merged.txt").read_text() == "done\n"
    assert sh("git", "diff", "--cached", "--name-only", cwd=repo) == "merged.txt"


async def test_every_job_of_the_group_reviews_the_commit_that_was_head_at_submit(
    make_manager, repo: Path
) -> None:
    team_skill(repo)
    mgr = make_manager(max_concurrent=1)  # reviewers run one after another
    submitted_at = sh("git", "rev-parse", "HEAD", cwd=repo)
    merge = mgr.submit(spec(by_angle={"reuse": {"sleep": 0.4}}), skill="team")

    (repo / "later.txt").write_text("committed while the first reviewer runs\n")
    sh("git", "add", "later.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "later", cwd=repo)
    await finish(mgr, merge)

    assert {j.base_ref for j in (*reviewers_of(mgr, merge), merge)} == {submitted_at}
    assert not (merge.worktree / "later.txt").exists()


async def test_the_default_wait_shows_only_the_merge_job(make_manager, repo: Path) -> None:
    team_skill(repo)
    mgr = make_manager()
    merge = mgr.submit(spec(by_angle={"reuse": {"sleep": 0.3}}), skill="team")
    assert await mgr.wait(timeout=0) == [merge]
    await finish(mgr, merge)


async def test_the_waiting_merge_job_reports_how_many_reviewers_are_done(
    make_manager, repo
) -> None:
    team_skill(repo)
    mgr = make_manager(max_concurrent=1)
    merge = mgr.submit(
        spec(by_angle={"efficiency": {"sleep": 0.3}, "reuse": {"sleep": 0.3}}), skill="team"
    )
    assert status_line(merge) == f"{merge.id} queued (waiting for 2 reviewers)"
    first = reviewers_of(mgr, merge)[0]
    await mgr.wait([first.id], timeout=20)
    assert (
        merge.state == "queued" and status_line(merge) == f"{merge.id} queued (reviewers 1/2 done)"
    )
    await finish(mgr, merge)


async def test_a_failed_reviewer_is_named_in_the_merge_prompt_and_does_not_stop_it(
    make_manager, repo: Path
) -> None:
    team_skill(repo)
    mgr = make_manager()
    merge = mgr.submit(
        spec(by_angle={"reuse": {"exit": 1}, "efficiency": {"message": "(none)"}}), skill="team"
    )
    await finish(mgr, merge)
    assert merge.state == "finished"
    prompt = (merge.run_dir / "task.md").read_text()
    assert "### reuse" in prompt and "failed)" in prompt and "(no report:" in prompt


async def test_the_group_cost_is_recorded_and_shown(make_manager, repo: Path) -> None:
    team_skill(repo)
    mgr = make_manager()
    merge = mgr.submit(TASK, skill="team")
    await finish(mgr, merge)
    assert merge.reviewers_cost == pytest.approx(0.05)  # 0.02 + 0.03
    assert merge.cost_usd == pytest.approx(0.05)
    text = result_text(merge, await mgr.details(merge.id), mgr.cfg)
    assert "reviewers: 2 jobs, $0.0500 (merge $0.0500, total $0.1000)" in text


async def test_project_context_reaches_every_reviewer(make_manager, repo: Path) -> None:
    team_skill(repo)
    (repo / "AGENTS.md").write_text("House rules.\n")
    mgr = make_manager(context_files=("AGENTS.md",))
    merge = mgr.submit(TASK, skill="team")
    await finish(mgr, merge)
    for job in (*reviewers_of(mgr, merge), merge):
        assert "## Project context: AGENTS.md" in (job.run_dir / "task.md").read_text()


async def test_serial_mode_runs_the_group_one_job_at_a_time(make_manager, repo: Path) -> None:
    team_skill(repo)
    mgr = make_manager(isolation="serial")
    merge = mgr.submit(
        spec(by_angle={"reuse": {"sleep": 0.2}, "efficiency": {"sleep": 0.2}}), skill="team"
    )
    assert sorted(j.state for j in mgr.jobs.values()) == ["queued", "queued", "running"]
    await finish(mgr, merge)
    jobs = sorted(mgr.jobs.values(), key=lambda j: j.started_at)
    assert all(a.ended_at <= b.started_at for a, b in zip(jobs, jobs[1:], strict=False))


async def test_base_job_cannot_be_combined_with_a_fanout_skill(make_manager, repo: Path) -> None:
    team_skill(repo)
    mgr = make_manager()
    first = mgr.submit(spec())
    await finish(mgr, first)
    with pytest.raises(ManagerError, match="base_job"):
        mgr.submit(TASK, skill="team", base_job=first.id)
    assert len(mgr.jobs) == 1  # nothing from the rejected submit was queued


async def test_a_repo_without_commits_gives_a_clear_error(make_manager, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=empty)
    team_skill(empty)
    with pytest.raises(ManagerError, match="cannot resolve"):
        make_manager_for(make_manager, empty).submit(TASK, skill="team")


def make_manager_for(make_manager, repo: Path):
    from vibe_mcp.manager import Manager

    mgr = make_manager()
    return Manager(mgr.cfg, repo)
