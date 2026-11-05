import json
from pathlib import Path

import pytest

from vibe_mcp.config import Config
from vibe_mcp.jobs import Job, run_dir_for
from vibe_mcp.runner import (
    Event,
    apply_export,
    build_command,
    final_state,
    parse_stream_line,
    read_export,
)


def make_job(tmp_path: Path, **kw) -> Job:
    return Job(id="a1b2c3", run_dir=run_dir_for(tmp_path, "a1b2c3"), spec="do it", **kw)


def test_build_command_defaults(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    cmd = build_command(job, Config(sandbox_cmd="vibe-sandbox"))
    run = str(job.run_dir)
    assert cmd == [
        "vibe-sandbox",
        run,
        "--prompt-file",
        f"{run}/task.md",
        "--model",
        "mistral-large-4",
        "--auto-approve",
        "--trust",
        "--max-turns",
        "200",
        "--time-limit",
        "1200",
        "--output",
        "streaming",
    ]


def test_build_command_uses_job_budgets_and_optional_price(tmp_path: Path) -> None:
    job = make_job(tmp_path, max_turns=7, time_limit=60, max_price=0.5)
    cmd = build_command(job, Config())
    assert cmd[cmd.index("--max-turns") + 1] == "7"
    assert cmd[cmd.index("--time-limit") + 1] == "60"
    assert cmd[cmd.index("--max-price") + 1] == "0.5"
    assert "--max-price" not in build_command(make_job(tmp_path), Config())


def test_build_command_splits_a_sandbox_command_with_arguments(tmp_path: Path) -> None:
    cmd = build_command(make_job(tmp_path), Config(sandbox_cmd="python '/my dir/fake.py'"))
    assert cmd[:2] == ["python", "/my dir/fake.py"]


EFFECT = {
    "type": "effect",
    "title": "file_system.edit",
    "detail": {"toolName": "file_system.edit", "display": {"summary": "Editing m.py"}},
    "state": {"status": "completed"},
}


def line(obj: object) -> str:
    return json.dumps(obj) + "\n"


def test_effect_becomes_a_progress_event() -> None:
    assert parse_stream_line(line(EFFECT)) == Event("effect", "Editing m.py")


def test_effect_without_display_falls_back_to_the_tool_title() -> None:
    assert parse_stream_line(line({"type": "effect", "title": "file_system.bash"})) == Event(
        "effect", "file_system.bash"
    )


def test_long_effect_summaries_are_truncated() -> None:
    effect = {"type": "effect", "detail": {"display": {"summary": "bash: " + "x" * 500}}}
    event = parse_stream_line(line(effect))
    assert event is not None
    assert len(event.text) == 100
    assert event.text.endswith("…")


def test_assistant_message_is_the_final_message() -> None:
    msg = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Fixed."}, {"type": "text", "text": "Verified."}],
    }
    assert parse_stream_line(line(msg)) == Event("final", "Fixed.\nVerified.")


@pytest.mark.parametrize(
    "raw",
    [
        line({"type": "reasoning", "text": "thinking"}),
        line({"type": "message", "role": "user", "content": [{"type": "text", "text": "hi"}]}),
        line({"type": "message", "role": "assistant", "content": "not a list"}),
        line([1, 2]),
        line({"no": "type"}),
        "not json\n",
        "\n",
        "",
    ],
)
def test_other_lines_are_ignored(raw: str) -> None:
    assert parse_stream_line(raw) is None


EXPORT = {
    "outcome": "finished",
    "stop_reason": None,
    "error": None,
    "session_id": "7090",
    "usage": {"input_tokens": 32733, "output_tokens": 313, "total_tokens": 33046},
    "cost_usd": 0.01303162,
    "steps": 4,
    "exit_code": 0,
    "config": {"huge": "x" * 1000},
}


def test_read_export(tmp_path: Path) -> None:
    assert read_export(tmp_path) is None
    (tmp_path / "export").mkdir()
    (tmp_path / "export" / "export.json").write_text("{broken")
    assert read_export(tmp_path) is None
    (tmp_path / "export" / "export.json").write_text(json.dumps(EXPORT))
    export = read_export(tmp_path)
    assert export is not None
    assert export["cost_usd"] == 0.01303162
    assert "config" not in export


def test_apply_export_fills_the_outcome_fields(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    apply_export(job, {**EXPORT, "stop_reason": "max_turns", "error": {"message": "boom"}})
    assert job.cost_usd == 0.01303162
    assert job.tokens == 33046
    assert job.exit_code == 0
    assert job.stop_reason == "max_turns"
    assert job.error is not None and "boom" in job.error


def test_apply_export_tolerates_missing_fields_and_no_export(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    apply_export(job, None)
    apply_export(job, {})
    assert (job.cost_usd, job.tokens, job.exit_code, job.error) == (0.0, 0, None, None)


def test_apply_export_keeps_an_error_recorded_earlier(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    job.error = "killed: still running past the time limit"
    apply_export(job, EXPORT)  # export.json reports no error of its own
    assert job.error == "killed: still running past the time limit"


@pytest.mark.parametrize(
    "exit_code, expected",
    [(0, "finished"), (3, "limit"), (1, "failed"), (2, "failed"), (-15, "failed")],
)
def test_final_state_follows_the_exit_code(exit_code: int, expected: str) -> None:
    assert final_state(exit_code) == expected
