"""The skills shipped in contrib/skills must load here and be valid for Vibe itself."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

from vibe_mcp.skills import fanout, load

SHIPPED = Path(__file__).parent.parent / "contrib" / "skills"
NAMES = sorted(p.name for p in SHIPPED.glob("*/") if (p / "SKILL.md").is_file())


def frontmatter(path: Path) -> dict:
    """The frontmatter parsed as YAML, the way Vibe parses it (an invalid file raises)."""
    head = path.read_text().split("\n---\n", 1)[0]
    assert head.startswith("---\n"), f"{path} must start with frontmatter"
    meta = yaml.safe_load(head.removeprefix("---\n"))
    assert isinstance(meta, dict), f"{path} frontmatter must be a mapping"
    return meta


def test_the_expected_skills_are_shipped() -> None:
    assert NAMES == ["code-review", "review-fanout", "simplify", "simplify-fanout"]


@pytest.mark.parametrize("name", NAMES)
def test_frontmatter_follows_vibes_rules(name: str) -> None:
    meta = frontmatter(SHIPPED / name / "SKILL.md")
    assert meta["name"] == name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
    assert 1 <= len(meta["description"]) <= 1024
    assert meta.get("license") == "MIT"


@pytest.mark.parametrize("name", NAMES)
def test_each_skill_loads_through_the_mcp(name: str, tmp_path: Path) -> None:
    shutil.copytree(SHIPPED / name, tmp_path / ".vibe" / "skills" / name)
    body = load(name, tmp_path, home=tmp_path / "nohome")
    assert len(body) > 200 and not body.startswith("---")


def test_a_description_with_an_unquoted_colon_is_caught(tmp_path: Path) -> None:
    """The mistake that made Vibe silently skip a skill: `levels: low` is not valid plain YAML."""
    broken = tmp_path / "SKILL.md"
    broken.write_text("---\nname: demo\ndescription: Effort levels: low and high\n---\nBody.\n")
    with pytest.raises(yaml.YAMLError):
        frontmatter(broken)


APACHE = Path(__file__).parent.parent / "contrib" / "skills-apache"


def test_the_apache_adaptation_keeps_its_license_and_credit() -> None:
    """Apache-2.0 section 4: ship the license, say what changed, keep the credit."""
    license_text = (APACHE / "LICENSE").read_text()
    assert "Apache License" in license_text and "Version 2.0, January 2004" in license_text
    skill = APACHE / "code-simplifier" / "SKILL.md"
    head = skill.read_text().split("\n---\n", 1)[0]
    # the notice sits in YAML comments: visible in the file, never sent to Vibe
    for needed in (
        "claude-plugins-official",
        "Apache License, Version 2.0",
        "Changes:",
        "Not endorsed",
    ):
        assert needed in head, f"missing from the notice: {needed}"
    meta = frontmatter(skill)
    assert meta["name"] == "code-simplifier" and meta["license"] == "Apache-2.0"
    assert 1 <= len(meta["description"]) <= 1024


def test_the_adaptation_loads_through_the_mcp_without_its_notice(tmp_path: Path) -> None:
    shutil.copytree(APACHE / "code-simplifier", tmp_path / ".vibe" / "skills" / "code-simplifier")
    body = load("code-simplifier", tmp_path, home=tmp_path / "nohome")
    assert len(body) > 500 and not body.startswith("---")
    assert "claude-plugins-official" not in body  # the notice is not paid for on every job
    assert "Never ask questions" in body  # the unattended rules were added
    assert (
        "ES modules" not in body and "React" not in body
    )  # the JavaScript-specific rules are gone


@pytest.mark.parametrize(
    "name, angles, readonly",
    [
        ("simplify-fanout", ["dead-weight", "duplication", "structure", "wasted-work"], False),
        ("review-fanout", ["callers", "edges", "logic", "security", "state-and-timing"], True),
    ],
)
def test_the_fanout_packs_declare_their_reviewers(
    name: str, angles: list[str], readonly: bool, tmp_path: Path
) -> None:
    shutil.copytree(SHIPPED / name, tmp_path / ".vibe" / "skills" / name)
    found = fanout(name, tmp_path, home=tmp_path / "nohome")
    assert found is not None
    assert sorted(found.angles) == angles and found.readonly is readonly
    assert all(len(text) > 300 for text in found.angles.values())
    merge = load(name, tmp_path, home=tmp_path / "nohome")
    assert "Findings from independent reviewers" in merge  # the merge job's input section


@pytest.mark.parametrize("name", ["simplify", "code-review"])
def test_the_single_agent_skills_are_not_fanout_skills(name: str, tmp_path: Path) -> None:
    shutil.copytree(SHIPPED / name, tmp_path / ".vibe" / "skills" / name)
    assert fanout(name, tmp_path, home=tmp_path / "nohome") is None


@pytest.mark.parametrize(
    "relative",
    ["simplify/SKILL.md", "simplify-fanout/SKILL.md", "simplify-fanout/angles/dead-weight.md"],
)
def test_cleanup_skills_never_delete_a_comment_that_contradicts_the_code(relative: str) -> None:
    """A real run deleted a docstring that disagreed with the code, hiding the bug it pointed at."""
    text = " ".join((SHIPPED / relative).read_text().split())
    assert "disagrees with the code" in text
