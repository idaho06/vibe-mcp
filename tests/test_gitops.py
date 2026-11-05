from pathlib import Path

import pytest

from vibe_mcp import gitops
from vibe_mcp.gitops import GitError

from .conftest import sh

pytestmark = pytest.mark.anyio


async def make_job(repo: Path, tmp_path: Path, name: str = "j1", base: str = "HEAD") -> Path:
    wt = tmp_path / "wt" / name
    await gitops.add_worktree(repo, wt, f"vibe/{name}", base)
    return wt


async def test_git_raises_with_stderr(repo: Path) -> None:
    with pytest.raises(GitError, match="nonexistent"):
        await gitops.git("rev-parse", "--verify", "nonexistent", cwd=repo)


async def test_toplevel_from_subdir_and_outside_a_repo(repo: Path, tmp_path: Path) -> None:
    assert await gitops.toplevel(repo / "sub") == repo
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitError):
        await gitops.toplevel(outside)


async def test_head_and_branch(repo: Path) -> None:
    sha = sh("git", "rev-parse", "HEAD", cwd=repo)
    assert await gitops.head(repo) == sha
    assert await gitops.current_branch(repo) == "main"
    sh("git", "checkout", "-q", "--detach", cwd=repo)
    assert await gitops.current_branch(repo) == ""


async def test_is_clean(repo: Path) -> None:
    assert await gitops.is_clean(repo)
    (repo / "new.txt").write_text("x")
    assert await gitops.is_clean(repo)  # untracked files don't count by default
    assert not await gitops.is_clean(repo, untracked=True)
    (repo / "new.txt").unlink()
    (repo / "a.txt").write_text("changed\n")
    assert not await gitops.is_clean(repo)
    sh("git", "add", "a.txt", cwd=repo)
    assert not await gitops.is_clean(repo)  # staged counts too


async def test_add_worktree_from_head(repo: Path, tmp_path: Path) -> None:
    wt = await make_job(repo, tmp_path)
    assert (wt / "a.txt").read_text() == "1\n"
    assert await gitops.current_branch(wt) == "vibe/j1"
    assert await gitops.head(wt) == await gitops.head(repo)


async def test_add_worktree_from_another_branch(repo: Path, tmp_path: Path) -> None:
    first = await make_job(repo, tmp_path, "j1")
    (first / "a.txt").write_text("from j1\n")
    await gitops.commit_all(first, "j1 work")
    second = await make_job(repo, tmp_path, "j2", base="vibe/j1")
    assert (second / "a.txt").read_text() == "from j1\n"


async def test_commit_all_includes_new_modified_deleted_and_respects_gitignore(
    repo: Path, tmp_path: Path
) -> None:
    (repo / ".gitignore").write_text("*.pyc\n")
    sh("git", "add", ".gitignore", cwd=repo)
    sh("git", "commit", "-q", "-m", "ignore", cwd=repo)
    main_head = await gitops.head(repo)
    wt = await make_job(repo, tmp_path)
    (wt / "a.txt").write_text("2\n")
    (wt / "sub" / "b.txt").unlink()
    (wt / "new.py").write_text("print(1)\n")
    (wt / "junk.pyc").write_text("x")

    sha = await gitops.commit_all(wt, "vibe j1: work")

    assert sha == await gitops.head(wt)
    assert sh("git", "status", "--short", cwd=wt) == ""
    assert sh("git", "log", "-1", "--format=%an <%ae>|%s", cwd=wt) == (
        "vibe-mcp <vibe-mcp@localhost>|vibe j1: work"
    )
    assert sh("git", "ls-tree", "-r", "--name-only", "HEAD", cwd=wt).split() == [
        ".gitignore",
        "a.txt",
        "new.py",
    ]
    assert await gitops.head(repo) == main_head
    assert await gitops.current_branch(repo) == "main"


async def test_commit_all_with_no_changes_returns_none(repo: Path, tmp_path: Path) -> None:
    wt = await make_job(repo, tmp_path)
    before = await gitops.head(wt)
    assert await gitops.commit_all(wt, "nothing") is None
    assert await gitops.head(wt) == before


async def test_diff_helpers(repo: Path, tmp_path: Path) -> None:
    base = await gitops.head(repo)
    wt = await make_job(repo, tmp_path)
    (wt / "a.txt").write_text("2\n")
    (wt / "new.py").write_text("print(1)\n")
    (wt / "sub" / "b.txt").unlink()
    (wt / "old.txt").write_text("renamed\n")  # a plain addition, not a rename
    tip = await gitops.commit_all(wt, "work")
    assert tip

    assert sorted(await gitops.changed_files(repo, base, tip)) == [
        "a.txt",
        "new.py",
        "old.txt",
        "sub/b.txt",
    ]
    assert sorted(await gitops.added_files(repo, base, tip)) == ["new.py", "old.txt"]
    stat = await gitops.diff_stat(repo, base, tip)
    assert "a.txt" in stat and "4 files changed" in stat
    assert "+2" in await gitops.diff(repo, base, tip)


async def test_renames_list_both_paths(repo: Path, tmp_path: Path) -> None:
    base = await gitops.head(repo)
    wt = await make_job(repo, tmp_path)
    (wt / "sub" / "b.txt").rename(wt / "c.txt")
    tip = await gitops.commit_all(wt, "rename")
    assert tip
    assert sorted(await gitops.changed_files(repo, base, tip)) == ["c.txt", "sub/b.txt"]


async def test_merge_squash_stages_without_committing(repo: Path, tmp_path: Path) -> None:
    wt = await make_job(repo, tmp_path)
    (wt / "a.txt").write_text("2\n")
    (wt / "new.py").write_text("print(1)\n")
    await gitops.commit_all(wt, "work")
    main_head = await gitops.head(repo)

    assert await gitops.merge_squash(repo, "vibe/j1") == []

    assert await gitops.head(repo) == main_head
    assert (repo / "a.txt").read_text() == "2\n"
    assert sorted(sh("git", "diff", "--cached", "--name-only", cwd=repo).split()) == [
        "a.txt",
        "new.py",
    ]


async def test_merge_squash_conflict_is_reported_and_reset(repo: Path, tmp_path: Path) -> None:
    wt = await make_job(repo, tmp_path)
    (wt / "a.txt").write_text("job version\n")
    await gitops.commit_all(wt, "work")
    (repo / "a.txt").write_text("main version\n")
    sh("git", "commit", "-q", "-am", "main change", cwd=repo)
    main_head = await gitops.head(repo)

    assert await gitops.merge_squash(repo, "vibe/j1") == ["a.txt"]

    assert await gitops.head(repo) == main_head
    assert await gitops.is_clean(repo, untracked=True)
    assert (repo / "a.txt").read_text() == "main version\n"


async def test_merge_squash_of_unknown_branch_raises(repo: Path) -> None:
    with pytest.raises(GitError):
        await gitops.merge_squash(repo, "vibe/nope")


async def test_remove_worktree_deletes_directory_and_branch(repo: Path, tmp_path: Path) -> None:
    wt = await make_job(repo, tmp_path)
    (wt / "dirty.txt").write_text("uncommitted")
    await gitops.remove_worktree(repo, wt, "vibe/j1")
    assert not wt.exists()
    assert sh("git", "branch", "--list", "vibe/j1", cwd=repo) == ""
    assert sh("git", "worktree", "list", "--porcelain", cwd=repo).count("worktree ") == 1
    await gitops.remove_worktree(repo, wt, "vibe/j1")  # idempotent


async def test_a_detached_worktree_has_no_branch(repo: Path, tmp_path: Path) -> None:
    sha = await gitops.head(repo)
    wt = tmp_path / "wt" / "detached"
    await gitops.add_worktree(repo, wt, None, sha)
    assert (wt / "a.txt").read_text() == "1\n"
    assert await gitops.current_branch(wt) == ""  # detached HEAD
    assert await gitops.head(wt) == sha
    assert sh("git", "for-each-ref", "refs/heads/", cwd=repo).count("\n") == 0  # only main exists


async def test_removing_a_detached_worktree_needs_no_branch(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt" / "detached"
    await gitops.add_worktree(repo, wt, None, "HEAD")
    (wt / "dirty.txt").write_text("x")
    await gitops.remove_worktree(repo, wt, None)
    assert not wt.exists()
    assert sh("git", "worktree", "list", "--porcelain", cwd=repo).count("worktree ") == 1
