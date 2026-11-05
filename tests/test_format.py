from pathlib import Path

import pytest

from vibe_mcp.config import Config
from vibe_mcp.format import apply_text, conflict_text, duration, result_text, status_line
from vibe_mcp.jobs import Details, Job


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
    assert "vibe says: " + "x" * 10 + "… [truncated; vibe_result(job_id, full=true)" in text


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


def test_a_queued_job_shows_its_progress_text() -> None:
    waiting = job(title="cleanup", last_action="reviewers 2/4 done")
    assert status_line(waiting) == "a1b2c3 queued (reviewers 2/4 done) - cleanup"
    assert status_line(job(state="running", started_at=1, step=0, last_action="stale"), now=3) == (
        "a1b2c3 running 2s"
    )  # the progress text is only for queued jobs


def test_a_readonly_job_does_not_claim_that_nothing_was_committed() -> None:
    quiet = finished(readonly=True, final_message="(none)")
    text = result_text(quiet, Details(), Config())
    assert "no changes committed" not in text and "branch:" not in text
    assert "no changes committed" in result_text(finished(), Details(), Config())  # ordinary job


def test_the_group_cost_line_adds_reviewers_to_the_merge_job() -> None:
    merged = finished(depends_on=("a", "b", "c"), reviewers_cost=0.0912, cost_usd=0.0388)
    text = result_text(merged, Details(), Config())
    assert "reviewers: 3 jobs, $0.0912 (merge $0.0388, total $0.1300)" in text
    assert "reviewers:" not in result_text(finished(), Details(), Config())


def test_a_fanout_merge_job_shows_the_whole_group_cost_in_its_status_line() -> None:
    done = job(
        state="finished", started_at=100, ended_at=135, title="fanout review",
        cost_usd=0.0278, reviewers_cost=0.1262,
    )  # fmt: skip
    assert status_line(done) == "a1b2c3 finished 35s $0.1540 (incl. reviewers) - fanout review"


def test_no_cost_is_shown_while_a_merge_job_is_still_running() -> None:
    running = job(state="running", started_at=100, reviewers_cost=0.1262)
    assert (
        status_line(running, now=110) == "a1b2c3 running 10s"
    )  # the merge's own cost is not known yet


LINES = [f"finding {i}: " + "x" * 80 for i in range(1, 11)]  # ten lines of about 91 characters


def test_a_long_report_is_cut_at_a_line_boundary_with_a_count_of_what_was_left_out() -> None:
    message = "\n".join(LINES)
    text = result_text(finished(final_message=message), Details(), Config(max_message_chars=400))
    assert [line for line in LINES if line in text] == LINES[:4]  # whole lines, in order
    assert "[6 more lines omitted; vibe_result(job_id, full=true) returns everything]" in text


def test_one_omitted_line_is_singular() -> None:
    text = result_text(
        finished(final_message="\n".join(LINES[:3])), Details(), Config(max_message_chars=200)
    )
    assert "[1 more line omitted; vibe_result(job_id, full=true)" in text


def test_full_returns_the_whole_message_with_no_note() -> None:
    message = "\n".join(LINES)
    text = result_text(
        finished(final_message=message), Details(), Config(max_message_chars=400), full=True
    )
    assert LINES[-1] in text and "omitted" not in text and "truncated" not in text


def test_a_message_within_the_limit_is_untouched() -> None:
    text = result_text(finished(final_message="short"), Details(), Config(max_message_chars=400))
    assert "vibe says: short" in text and "omitted" not in text
