"""The skills shipped in contrib/skills must load here and be valid for Vibe itself."""

import re
import shutil
from pathlib import Path

import pytest

from vibe_mcp.skills import load

SHIPPED = Path(__file__).parent.parent / "contrib" / "skills"
NAMES = sorted(p.name for p in SHIPPED.glob("*/") if (p / "SKILL.md").is_file())


def frontmatter(path: Path) -> dict[str, str]:
    head = path.read_text().split("\n---\n", 1)[0]
    assert head.startswith("---\n"), f"{path} must start with frontmatter"
    pairs = (line.split(":", 1) for line in head.splitlines()[1:] if ":" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def test_the_expected_skills_are_shipped() -> None:
    assert NAMES == ["code-review", "simplify"]


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
