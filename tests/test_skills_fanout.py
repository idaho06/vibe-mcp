from pathlib import Path

import pytest

from vibe_mcp.skills import Fanout, SkillError, fanout


def make_skill(
    base: Path, name: str = "team", angles: dict[str, str] | None = None, **files: str
) -> Path:
    folder = base / ".vibe" / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\nMerge the findings.")
    if angles is not None:
        (folder / "angles").mkdir()
        for angle, text in angles.items():
            (folder / "angles" / f"{angle}.md").write_text(text)
    for filename, text in files.items():
        (folder / filename.replace("_", ".")).write_text(text)
    return folder


@pytest.fixture
def places(tmp_path: Path) -> tuple[Path, Path]:
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    return repo, home


def test_an_angles_folder_makes_a_skill_a_fanout_skill(places) -> None:
    repo, home = places
    make_skill(repo, angles={"reuse": "Look for reuse.", "efficiency": "Look for waste.\n"})
    result = fanout("team", repo, home)
    assert result == Fanout(
        angles={"efficiency": "Look for waste.", "reuse": "Look for reuse."}, readonly=False
    )
    assert list(result.angles) == ["efficiency", "reuse"]  # sorted by file name: a stable order


def test_a_skill_without_angles_is_not_a_fanout_skill(places) -> None:
    repo, home = places
    make_skill(repo, "plain")
    make_skill(repo, "hollow", angles={})  # an empty angles folder
    assert fanout("plain", repo, home) is None
    assert fanout("hollow", repo, home) is None


def test_angle_files_may_carry_frontmatter_and_other_files_are_ignored(places) -> None:
    repo, home = places
    folder = make_skill(repo, angles={"reuse": "---\nname: reuse\n---\nLook for reuse."})
    (folder / "angles" / "notes.txt").write_text("not an angle")
    assert fanout("team", repo, home).angles == {"reuse": "Look for reuse."}


def test_fanout_toml_can_declare_a_readonly_merge(places) -> None:
    repo, home = places
    make_skill(repo, angles={"a": "x"}, fanout_toml="readonly = true\n")
    assert fanout("team", repo, home).readonly is True


@pytest.mark.parametrize(
    "toml, fragment",
    [("readonly = 3\n", "readonly"), ("bogus = 1\n", "bogus"), ("readonly =\n", "fanout.toml")],
)
def test_a_bad_fanout_toml_is_rejected(places, toml: str, fragment: str) -> None:
    repo, home = places
    make_skill(repo, angles={"a": "x"}, fanout_toml=toml)
    with pytest.raises(SkillError, match=fragment):
        fanout("team", repo, home)


def test_bad_angle_files_are_rejected(places) -> None:
    repo, home = places
    make_skill(repo, "empty", angles={"reuse": "---\nname: reuse\n---\n"})
    make_skill(repo, "oddname", angles={"Not Valid": "x"})
    with pytest.raises(SkillError, match="empty"):
        fanout("empty", repo, home)
    with pytest.raises(SkillError, match="invalid angle name"):
        fanout("oddname", repo, home)


def test_the_folder_that_supplies_the_skill_supplies_its_angles(places) -> None:
    repo, home = places
    make_skill(repo, "team")  # the project's skill has no angles ...
    make_skill(home, "team", angles={"reuse": "global angle"})  # ... the global one does
    assert fanout("team", repo, home) is None  # the project skill wins entirely


def test_unknown_and_invalid_names_raise_like_load(places) -> None:
    repo, home = places
    with pytest.raises(SkillError, match="unknown skill"):
        fanout("nope", repo, home)
    with pytest.raises(SkillError, match="invalid skill name"):
        fanout("../x", repo, home)
