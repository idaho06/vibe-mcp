from pathlib import Path

import pytest

from vibe_mcp import fanout
from vibe_mcp.config import Config
from vibe_mcp.jobs import Job, run_dir_for

from .conftest import sh


def reviewer(tmp_path: Path, angle: str, state: str = "finished", message: str = "", **kw) -> Job:
    return Job(
        id=f"{angle[:6]:0<6}", run_dir=run_dir_for(tmp_path, angle), spec="s", angle=angle,
        state=state, final_message=message, **kw,
    )  # fmt: skip


# ---- pin


def test_pin_resolves_a_ref_to_the_commit_it_names_now(repo: Path) -> None:
    head = sh("git", "rev-parse", "HEAD", cwd=repo)
    assert fanout.pin(repo, "HEAD") == head
    sh("git", "branch", "topic", cwd=repo)
    assert fanout.pin(repo, "topic") == head
    (repo / "x.txt").write_text("x")
    sh("git", "add", "x.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "x", cwd=repo)
    assert fanout.pin(repo, "HEAD") != head  # HEAD moved, the pinned value did not


def test_pin_rejects_an_unknown_ref(repo: Path) -> None:
    with pytest.raises(ValueError, match="nope"):
        fanout.pin(repo, "nope")


# ---- the reviewer's prompt


def test_the_reviewer_prompt_leads_with_its_role_and_forbids_doing_the_task() -> None:
    text = fanout.reviewer_preamble(
        "reuse", "Look for reuse.", 4, context="## Project context: X\n\nrules\n\n---\n\n"
    )
    assert text.startswith("## Reviewer role")
    assert "one of 4 independent reviewers" in text
    assert "read-only" in text and "never commit" in text
    assert "do not carry it out" in text  # the spec says "Simplify X": the reviewer must not
    assert "Never ask questions" in text
    assert text.endswith("## Task\n\n")


def test_the_prompt_order_is_role_then_angle_then_context_then_task() -> None:
    text = fanout.reviewer_preamble("reuse", "Look for reuse.", 4, context="CONTEXT-BLOCK\n\n")
    order = [
        text.index(part)
        for part in (
            "## Reviewer role",
            "## Your angle: reuse",
            "Look for reuse.",
            "CONTEXT-BLOCK",
            "## Task",
        )
    ]
    assert order == sorted(order)


# ---- the merge job's appendix


def test_the_appendix_lists_every_reviewer_with_its_report(tmp_path: Path) -> None:
    jobs = [
        reviewer(tmp_path, "reuse", message="a.py:3 - [HIGH] duplicate helper"),
        reviewer(tmp_path, "efficiency", message="(none)"),
    ]
    text = fanout.appendix(jobs)
    assert text.startswith("\n\n## Findings from independent reviewers")
    assert "### reuse (job reuse0, finished)\n\na.py:3 - [HIGH] duplicate helper" in text
    assert "### efficiency (job effici, finished)\n\n(none)" in text
    assert text.index("### reuse") < text.index("### efficiency")


def test_the_appendix_says_which_reports_are_missing_or_partial(tmp_path: Path) -> None:
    jobs = [
        reviewer(tmp_path, "reuse", state="failed", error="exited with code 1"),
        reviewer(tmp_path, "altitude", state="cancelled"),
        reviewer(tmp_path, "efficiency", state="limit", message="b.py:9 - [LOW] slow loop"),
        reviewer(tmp_path, "simplification", message="   "),
    ]
    text = fanout.appendix(jobs)
    assert "(no report: exited with code 1)" in text
    assert "(no report: cancelled)" in text
    assert "(stopped at its limit; the report may be incomplete)" in text and "slow loop" in text
    assert "(the reviewer gave no report)" in text


def test_a_huge_report_is_cut_but_the_limit_is_not_the_result_cap(tmp_path: Path) -> None:
    long = "x" * (fanout.MAX_REPORT_CHARS + 500)
    text = fanout.appendix([reviewer(tmp_path, "reuse", message=long)])
    assert "x" * fanout.MAX_REPORT_CHARS in text and "x" * (fanout.MAX_REPORT_CHARS + 1) not in text
    assert "[report truncated]" in text
    assert Config().max_message_chars < fanout.MAX_REPORT_CHARS  # a separate, larger limit


def test_progress_counts_the_reviewers_that_have_ended(tmp_path: Path) -> None:
    jobs = [
        reviewer(tmp_path, "a", state="finished"),
        reviewer(tmp_path, "b", state="running"),
        reviewer(tmp_path, "c", state="failed"),
    ]
    assert fanout.progress(jobs) == "reviewers 2/3 done"


# ---- building the group


def build(tmp_path: Path, **overrides) -> list[Job]:
    args = dict(
        cfg=Config(data_dir=tmp_path), repo=Path("/r"), taken={"aaaaaa"}, skill="team",
        angles={"reuse": "Look for reuse.", "efficiency": "Look for waste."}, context="",
        merge_preamble="## Skill: team\n\nMerge.\n\n---\n\n## Task\n\n",
        spec="Simplify HEAD~1..HEAD",
        title="cleanup", scope=("src",), max_turns=40, time_limit=300, max_price=0.2,
        base_ref="1" * 40, readonly=False,
    )  # fmt: skip
    return fanout.build(**{**args, **overrides})


def test_build_returns_the_reviewers_first_and_the_merge_job_last(tmp_path: Path) -> None:
    *reviewers, merge = build(tmp_path)
    assert [r.angle for r in reviewers] == ["reuse", "efficiency"]
    ids = [j.id for j in (*reviewers, merge)]
    assert len(set(ids)) == 3 and "aaaaaa" not in ids

    for r in reviewers:
        assert (r.readonly, r.hidden, r.parent, r.scope) == (True, True, merge.id, ())
        assert r.base_ref == "1" * 40 and r.spec == "Simplify HEAD~1..HEAD" and r.skill == "team"
        assert (r.max_turns, r.time_limit, r.max_price) == (40, 300, 0.2)
        assert r.preamble.startswith("## Reviewer role") and r.preamble.endswith("## Task\n\n")
        assert f"## Your angle: {r.angle}" in r.preamble
        assert r.repo == Path("/r") and r.run_dir == run_dir_for(tmp_path, r.id)


def test_build_makes_the_merge_job_depend_on_all_reviewers(tmp_path: Path) -> None:
    *reviewers, merge = build(tmp_path, readonly=True)
    assert merge.depends_on == tuple(r.id for r in reviewers)
    assert (merge.readonly, merge.hidden, merge.parent) == (True, False, None)
    assert merge.base_ref == "1" * 40 and merge.scope == ("src",) and merge.title == "cleanup"
    assert merge.preamble.startswith("## Skill: team") and merge.spec == "Simplify HEAD~1..HEAD"
    assert merge.last_action == "waiting for 2 reviewers"
    assert merge.state == "queued"


def test_build_puts_the_project_context_into_every_reviewer(tmp_path: Path) -> None:
    *reviewers, _ = build(tmp_path, context="## Project context: AGENTS.md\n\nrules\n\n---\n\n")
    assert all("## Project context: AGENTS.md" in r.preamble for r in reviewers)
