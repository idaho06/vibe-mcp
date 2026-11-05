import asyncio
import json

import pytest

from vibe_mcp import runner
from vibe_mcp.config import Config
from vibe_mcp.runner import read_export

from .conftest import alive, wait_for_file

pytestmark = pytest.mark.anyio


async def test_run_executes_in_the_worktree_and_records_everything(cfg: Config, new_job) -> None:
    spec = {
        "write": {"a.py": "print(1)\n", "pkg/b.py": "x = 2\n"},
        "message": "All done.",
        "cost": 0.5,
    }
    job = new_job(spec)
    ticks: list[int] = []

    code = await runner.run(job, cfg, lambda: ticks.append(job.step))

    assert code == 0
    assert (job.worktree / "a.py").read_text() == "print(1)\n"
    assert (job.worktree / "pkg" / "b.py").read_text() == "x = 2\n"
    assert (job.run_dir / "task.md").read_text() == json.dumps(spec)
    assert job.pid and job.step == 2
    assert job.last_action == "Editing pkg/b.py"
    assert job.final_message == "All done."
    assert ticks and ticks[-1] == 2
    argv = json.loads((job.run_dir / "argv.json").read_text())
    assert argv["cwd"] == str(job.worktree)
    assert argv["opts"]["--model"] == "mistral-large-4"
    log = (job.run_dir / "stdout.jsonl").read_text().splitlines()
    assert len(log) == 5 and json.loads(log[-1])["role"] == "assistant"
    export = read_export(job.run_dir)
    assert export and export["cost_usd"] == 0.5


async def test_run_returns_the_sandbox_exit_code(cfg: Config, new_job) -> None:
    job = new_job({"exit": 3, "outcome": "limit", "stop_reason": "max_turns"})
    assert await runner.run(job, cfg) == 3
    export = read_export(job.run_dir)
    assert export and export["stop_reason"] == "max_turns"


async def test_stderr_goes_to_a_file(cfg: Config, new_job) -> None:
    job = new_job({"exit": 2, "no_export": True})
    assert await runner.run(job, cfg) == 2
    assert "vibe-sandbox: exit=2" in (job.run_dir / "stderr.txt").read_text()
    assert read_export(job.run_dir) is None


async def test_lines_longer_than_the_default_stream_limit_are_handled(cfg: Config, new_job) -> None:
    job = new_job({"huge_line": 200_000, "message": "after the big line"})
    assert await runner.run(job, cfg) == 0
    assert job.final_message == "after the big line"
    assert job.step == 1


async def test_missing_sandbox_command_raises(new_job) -> None:
    job = new_job()
    with pytest.raises(FileNotFoundError):
        await runner.run(job, Config(sandbox_cmd="/nonexistent/vibe-sandbox"))


async def test_hard_timeout_kills_the_whole_process_group(
    cfg: Config, new_job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "HARD_TIMEOUT_GRACE", 0.5)
    job = new_job({"sleep": 60, "spawn_child": True}, time_limit=0)

    code = await runner.run(job, cfg)

    assert code != 0
    assert job.error and "time limit" in job.error
    child = int((job.run_dir / "child.pid").read_text())
    async with asyncio.timeout(3):
        while alive(child):
            await asyncio.sleep(0.05)


async def test_cancel_kills_the_process_group_and_propagates(cfg: Config, new_job) -> None:
    job = new_job({"sleep": 60, "spawn_child": True})
    task = asyncio.create_task(runner.run(job, cfg))
    await wait_for_file(job.run_dir / "child.pid")
    child = int((job.run_dir / "child.pid").read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not alive(child)
    assert not alive(job.pid)


async def test_cancel_escalates_to_sigkill_when_sigterm_is_ignored(
    cfg: Config, new_job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "KILL_AFTER", 0.3)
    job = new_job({"sleep": 60, "spawn_child": True, "ignore_term": True})
    task = asyncio.create_task(runner.run(job, cfg))
    await wait_for_file(job.run_dir / "child.pid")
    child = int((job.run_dir / "child.pid").read_text())

    task.cancel()
    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not alive(child)
    assert not alive(job.pid)
