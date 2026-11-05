from pathlib import Path

import pytest

from vibe_mcp.config import Config
from vibe_mcp.format import apply_text, conflict_text, duration, result_text, status_line
from vibe_mcp.jobs import Job
from vibe_mcp.manager import Details


def job(**kw) -> Job:
    return Job(id="a1b2c3", run_dir=Path("/r/vibe-run.a1b2c3"), spec="s", **kw)


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "0s"), (0.4, "0s"), (45, "45s"), (60, "1m00s"), (192, "3m12s"), (3725, "1h02m")],
)
def test_duration(seconds: float, expected: str) -> None:
    assert duration(seconds) == expected


def test_status_line_for_each_phase() -> None:
    assert status_line(job()) == "a1b2c3 queued"
    assert status_line(job(title="fix parser")) == "a1b2c3 queued - fix parser"
    running = job(state="running", started_at=100, step=7, last_action="Editing src/x.py")
    assert status_line(running, now=292) == "a1b2c3 running 3m12s step 7: Editing src/x.py"
    assert status_line(job(state="running", started_at=100), now=105) == "a1b2c3 running 5s"
    done = job(state="finished", started_at=100, ended_at=165, cost_usd=0.013)
    assert status_line(done) == "a1b2c3 finished 1m05s $0.0130"
    failed = job(state="failed", started_at=1, ended_at=3, error="exited with code 2\nmore")
    assert status_line(failed) == "a1b2c3 failed 2s (exited with code 2)"
    cancelled = job(state="cancelled", ended_at=3)
    assert status_line(cancelled) == "a1b2c3 cancelled"


def finished(**kw) -> Job:
    base = {
        "state": "finished",
        "started_at": 100,
        "ended_at": 165,
        "cost_usd": 0.013,
        "tokens": 33046,
        "final_message": "Fixed.",
        "title": "add parser",
    }
    return job(**{**base, **kw})


def test_result_text_reports_changes_and_bookkeeping() -> None:
    j = finished(
        scope=("src",),
        worktree=Path("/wt/repo-a1b2c3"),
        warning="main checkout's branch or HEAD changed",
        commit="abc123",
    )
    details = Details(
        stat=" a.py | 2 +-\n 1 file changed",
        files=["a.py", "src/new.py"],
        added=["src/new.py"],
        violations=["a.py"],
    )
    text = result_text(j, details, Config())
    lines = text.splitlines()
    assert lines[0] == status_line(j)
    for expected in (
        "vibe says: Fixed.",
        " a.py | 2 +-",
        "added: src/new.py",
        "SCOPE VIOLATIONS (outside src): a.py",
        "WARNING: main checkout's branch or HEAD changed",
        "tokens: 33046",
        "branch: vibe/a1b2c3",
        "worktree: /wt/repo-a1b2c3",
        "run: /r/vibe-run.a1b2c3",
    ):
        assert expected in text
    assert "no changes" not in text


def test_result_text_without_changes_or_message() -> None:
    text = result_text(finished(final_message=""), Details(), Config())
    assert "no changes" in text
    assert "vibe says" not in text


def test_result_text_truncates_a_long_message() -> None:
    text = result_text(finished(final_message="x" * 50), Details(), Config(max_message_chars=10))
    assert "vibe says: " + "x" * 10 + "… [truncated]" in text


def test_result_text_includes_the_error_of_a_failed_job() -> None:
    text = result_text(finished(state="failed", error="boom"), Details(), Config())
    assert "error: boom" in text


def test_result_text_diff_is_capped() -> None:
    details = Details(diff="d" * 100)
    text = result_text(finished(commit="c"), details, Config(max_diff_bytes=30))
    assert "d" * 30 in text and "d" * 31 not in text
    assert "[diff truncated: 30 of 100 bytes]" in text
    assert "d" * 100 in result_text(finished(commit="c"), details, Config(max_diff_bytes=1000))


def test_closed_jobs_do_not_advertise_a_branch() -> None:
    text = result_text(finished(state="applied"), Details(), Config())
    assert "branch:" not in text and "worktree:" not in text


def test_apply_and_conflict_texts() -> None:
    applied = apply_text(job(), " a.py | 2 +-")
    assert "a1b2c3" in applied and "staged" in applied and "not committed" in applied
    assert " a.py | 2 +-" in applied
    assert "no changes" in apply_text(job(), "")
    conflict = conflict_text(job(worktree=Path("/wt/x")), ["a.txt", "b.txt"])
    assert "CONFLICT" in conflict and "a.txt, b.txt" in conflict
    assert "vibe/a1b2c3" in conflict and "/wt/x" in conflict and "unchanged" in conflict
