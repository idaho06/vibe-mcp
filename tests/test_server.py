import json
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client

from vibe_mcp.config import Config
from vibe_mcp.server import build_server

from .conftest import alive, sh, spec, wait_for_file

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(cfg: Config, repo: Path):
    async with Client(build_server(cfg, repo)) as c:
        yield c


async def call(client: Client, tool: str, **args: object) -> str:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    assert result.structured_content is None  # the text must not be sent twice
    return result.content[0].text


async def call_error(client: Client, tool: str, **args: object) -> str:
    result = await client.call_tool(tool, args)
    assert result.is_error
    return result.content[0].text


async def submit_and_finish(client: Client, directive: dict, **args: object) -> str:
    """Submit a job, wait for it, and return its id."""
    text = await call(client, "vibe_submit", spec=json.dumps(directive), **args)
    job_id = text.split()[0]
    waited = await call(client, "vibe_wait", job_ids=[job_id], timeout=20, wait_all=True)
    assert "running" not in waited and "queued" not in waited, waited
    return job_id


async def test_the_tool_surface_is_small(client: Client) -> None:
    tools = (await client.list_tools()).tools
    assert sorted(t.name for t in tools) == [
        "vibe_apply",
        "vibe_cancel",
        "vibe_result",
        "vibe_submit",
        "vibe_wait",
    ]
    assert all(t.description and t.output_schema is None for t in tools)
    # every session pays for these schemas in context tokens
    assert len(json.dumps([t.model_dump(mode="json") for t in tools])) < 6000


async def test_submit_wait_result_apply_flow(client: Client, repo: Path) -> None:
    directive = {"write": {"new.py": "x = 1\n"}, "message": "Added new.py."}
    submitted = await call(client, "vibe_submit", spec=json.dumps(directive), title="add new.py")
    job_id = submitted.split()[0]
    assert "running" in submitted

    waited = await call(client, "vibe_wait", job_ids=[job_id], timeout=20, wait_all=True)
    assert waited.startswith(f"{job_id} finished")

    result = await call(client, "vibe_result", job_id=job_id)
    assert "vibe says: Added new.py." in result
    assert "added: new.py" in result and "+x = 1" not in result
    with_diff = await call(client, "vibe_result", job_id=job_id, diff=True)
    assert "+x = 1" in with_diff

    applied = await call(client, "vibe_apply", job_id=job_id)
    assert "staged" in applied and "new.py" in applied
    assert sh("git", "diff", "--cached", "--name-only", cwd=repo) == "new.py"
    assert "already applied" in await call_error(client, "vibe_apply", job_id=job_id)


async def test_apply_reports_conflicts_without_touching_main(client: Client, repo: Path) -> None:
    job_id = await submit_and_finish(client, {"write": {"a.txt": "job\n"}})
    (repo / "a.txt").write_text("main\n")
    sh("git", "commit", "-q", "-am", "main change", cwd=repo)

    text = await call(client, "vibe_apply", job_id=job_id)

    assert "CONFLICT" in text and "a.txt" in text
    assert sh("git", "status", "--porcelain", cwd=repo) == ""


async def test_apply_with_discard_removes_the_job(client: Client, repo: Path) -> None:
    job_id = await submit_and_finish(client, {"write": {"new.py": "x\n"}})
    text = await call(client, "vibe_apply", job_id=job_id, discard=True)
    assert text.startswith(f"{job_id} discarded")
    assert sh("git", "for-each-ref", "refs/heads/vibe/", cwd=repo) == ""
    assert sh("git", "status", "--porcelain", cwd=repo) == ""


async def test_cancel(client: Client) -> None:
    text = await call(client, "vibe_submit", spec=spec(sleep=60))
    job_id = text.split()[0]
    assert (await call(client, "vibe_cancel", job_id=job_id)).startswith(f"{job_id} cancelled")


async def test_wait_with_nothing_to_wait_for(client: Client) -> None:
    assert await call(client, "vibe_wait", timeout=1) == "no active jobs"


async def test_wait_lists_every_requested_job(client: Client) -> None:
    a = (await call(client, "vibe_submit", spec=spec(sleep=60), scope=["a"])).split()[0]
    b = (await call(client, "vibe_submit", spec=spec(sleep=60), scope=["b"])).split()[0]
    lines = (await call(client, "vibe_wait", timeout=0)).splitlines()
    assert [line.split()[0] for line in lines] == [a, b]
    assert all("running" in line for line in lines)
    await call(client, "vibe_cancel", job_id=a)
    await call(client, "vibe_cancel", job_id=b)


async def test_errors_are_reported_as_tool_errors(client: Client) -> None:
    assert "unknown job" in await call_error(client, "vibe_result", job_id="ffffff")
    assert "unknown job" in await call_error(client, "vibe_cancel", job_id="ffffff")
    assert "unknown job" in await call_error(client, "vibe_apply", job_id="ffffff")
    assert "spec is empty" in await call_error(client, "vibe_submit", spec=" ")
    assert "scope" in await call_error(client, "vibe_submit", spec="x", scope=["/etc"])
    assert "unknown job" in await call_error(client, "vibe_wait", job_ids=["ffffff"])


async def test_running_jobs_are_killed_when_the_server_stops(cfg: Config, repo: Path) -> None:
    async with Client(build_server(cfg, repo)) as c:
        text = await call(c, "vibe_submit", spec=spec(sleep=60, spawn_child=True))
        run_dir = next((cfg.data_dir / "runs").glob("vibe-run.*"))
        await wait_for_file(run_dir / "child.pid")
        child = int((run_dir / "child.pid").read_text())
        assert text.split()[0] in run_dir.name
    assert not alive(child)


async def test_the_server_refuses_to_start_outside_a_git_repo(cfg: Config, tmp_path: Path) -> None:
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    with pytest.raises(BaseException, match="not inside a git repository"):  # noqa: PT011
        async with Client(build_server(cfg, elsewhere)):
            pass


async def test_submit_with_a_skill_and_the_error_for_a_wrong_name(
    client: Client, repo: Path, cfg: Config
) -> None:
    skill = repo / ".vibe" / "skills" / "simplify"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: simplify\n---\nSimplify carefully.")

    job_id = await submit_and_finish(client, {"write": {"s.txt": "x"}}, skill="simplify")
    prompt = (cfg.data_dir / "runs" / f"vibe-run.{job_id}" / "task.md").read_text()
    assert "Simplify carefully." in prompt

    error = await call_error(client, "vibe_submit", spec="x", skill="simplyfy")
    assert "unknown skill 'simplyfy'" in error and "available: simplify" in error


async def test_a_fanout_skill_through_the_mcp_looks_like_one_job(
    client: Client, repo: Path
) -> None:
    folder = repo / ".vibe" / "skills" / "team"
    (folder / "angles").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: team\n---\nMerge the findings.")
    for angle in ("reuse", "efficiency"):
        (folder / "angles" / f"{angle}.md").write_text(f"Look at {angle}.")
    task = spec(
        by_angle={
            "reuse": {"message": "a.py:1 - [HIGH] dup", "cost": 0.02},
            "efficiency": {"cost": 0.03},
        },
        merge={"message": "merged", "cost": 0.05},
    )

    submitted = await call(client, "vibe_submit", spec=task, skill="team", title="cleanup")
    job_id = submitted.split()[0]
    assert "queued" in submitted  # the merge job: it waits for its reviewers
    assert (
        len((await call(client, "vibe_wait", timeout=0)).splitlines()) == 1
    )  # reviewers are hidden

    waited = await call(client, "vibe_wait", job_ids=[job_id], timeout=30, wait_all=True)
    assert waited.startswith(f"{job_id} finished")
    result = await call(client, "vibe_result", job_id=job_id)
    assert "vibe says: merged" in result
    assert "reviewers: 2 jobs, $0.0500 (merge $0.0500, total $0.1000)" in result


@pytest.mark.parametrize(
    "tool, args, wrong, valid",
    [
        ("vibe_wait", {"jobs": ["x"], "timeout": 0}, "jobs", "job_ids"),  # seen in a live session
        ("vibe_submit", {"spec": "x", "skill_name": "simplify"}, "skill_name", "skill"),
        ("vibe_apply", {"job_id": "x", "discarded": True}, "discarded", "discard"),
        ("vibe_result", {"job_id": "x", "show_diff": True}, "show_diff", "diff"),
        ("vibe_cancel", {"job_id": "x", "force": True}, "force", "job_id"),
    ],
)
async def test_unknown_arguments_are_rejected_and_the_valid_names_are_listed(
    client: Client, tool: str, args: dict, wrong: str, valid: str
) -> None:
    text = await call_error(client, tool, **args)
    assert f"unknown argument(s) {wrong}" in text
    assert valid in text.split("valid:")[1]  # the right name is offered


async def test_a_misnamed_skill_argument_does_not_queue_a_job_without_the_skill(
    client: Client,
) -> None:
    await call_error(client, "vibe_submit", spec="x", skill_name="simplify")
    assert await call(client, "vibe_wait", timeout=0) == "no active jobs"  # nothing was queued


async def test_a_misnamed_discard_does_not_apply_the_job(client: Client, repo: Path) -> None:
    job_id = await submit_and_finish(client, {"write": {"new.py": "x\n"}})
    await call_error(client, "vibe_apply", job_id=job_id, discarded=True)
    assert sh("git", "diff", "--cached", "--name-only", cwd=repo) == ""  # not applied
    assert "finished" in await call(client, "vibe_result", job_id=job_id)  # and still there
    assert (await call(client, "vibe_apply", job_id=job_id, discard=True)).startswith(
        f"{job_id} discarded"
    )


async def test_valid_calls_and_unknown_tools_behave_as_before(client: Client) -> None:
    assert (
        await call(client, "vibe_wait", job_ids=None, timeout=0, wait_all=False) == "no active jobs"
    )
    result = await client.call_tool("no_such_tool", {})
    assert result.is_error  # still an error, not our argument check


async def test_a_long_report_is_cut_by_line_and_full_returns_all_of_it(
    cfg: Config, repo: Path
) -> None:
    report = "\n".join(f"finding {i}: " + "x" * 60 for i in range(1, 13))
    async with Client(build_server(replace(cfg, max_message_chars=300), repo)) as client:
        job_id = await submit_and_finish(client, {"message": report})

        cut = await call(client, "vibe_result", job_id=job_id)
        assert "more lines omitted" in cut and "full=true" in cut and "finding 12" not in cut

        everything = await call(client, "vibe_result", job_id=job_id, full=True)
        assert "finding 12" in everything and "omitted" not in everything
