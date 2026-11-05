from pathlib import Path

import pytest

from vibe_mcp.skills import SkillError, available, block, load


def write(base: Path, name: str, text: str) -> None:
    folder = base / ".vibe" / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text)


@pytest.fixture
def places(tmp_path: Path) -> tuple[Path, Path]:
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    return repo, home


def test_a_project_skill_is_loaded_without_its_frontmatter(places) -> None:
    repo, home = places
    write(repo, "simplify", "---\nname: simplify\ndescription: d\n---\n\n# Simplify\n\nDo it.\n")
    assert load("simplify", repo, home) == "# Simplify\n\nDo it."


def test_a_global_skill_is_loaded_and_the_project_one_wins(places) -> None:
    repo, home = places
    write(home, "review", "global review")
    write(home, "simplify", "global simplify")
    write(repo, "simplify", "project simplify")
    assert load("review", repo, home) == "global review"
    assert load("simplify", repo, home) == "project simplify"


def test_a_skill_without_frontmatter_is_used_whole(places) -> None:
    repo, home = places
    write(repo, "plain", "just text\n---\nmore text\n")
    assert load("plain", repo, home) == "just text\n---\nmore text"


def test_an_empty_skill_is_an_error(places) -> None:
    repo, home = places
    write(repo, "hollow", "---\nname: hollow\n---\n")
    with pytest.raises(SkillError, match="empty"):
        load("hollow", repo, home)


def test_unknown_skill_lists_what_is_available(places) -> None:
    repo, home = places
    write(repo, "simplify", "x")
    write(home, "review", "x")
    (repo / ".vibe" / "skills" / "no-skill-file").mkdir()  # a folder without SKILL.md
    assert available(repo, home) == ["review", "simplify"]
    with pytest.raises(SkillError, match=r"unknown skill 'nope'; available: review, simplify"):
        load("nope", repo, home)


def test_unknown_skill_when_there_are_no_skills(places) -> None:
    repo, home = places
    with pytest.raises(SkillError, match=r"no skills found in .*repo/\.vibe/skills"):
        load("simplify", repo, home)


@pytest.mark.parametrize("name", ["", "../x", "a/b", ".hidden", "x y", "-x"])
def test_names_that_could_escape_the_skill_folders_are_rejected(places, name: str) -> None:
    repo, home = places
    with pytest.raises(SkillError, match="invalid skill name"):
        load(name, repo, home)


def test_block_frames_the_skill_for_the_prompt(places) -> None:
    repo, home = places
    write(repo, "simplify", "---\nname: simplify\n---\nSteps.")
    assert block("simplify", repo, home) == "## Skill: simplify\n\nSteps.\n\n---\n\n"
