"""Skills: instructions for Vibe kept as SKILL.md files and placed in front of a job's task.

They are looked up in Vibe's own skill folders, in the *main checkout*, so project skills work
even when `.vibe/` is gitignored and therefore missing from the job's worktree.
"""

import re
from pathlib import Path

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")  # no separators or dots: cannot leave the folders
_FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)


class SkillError(Exception):
    """The requested skill does not exist or cannot be used."""


def _folders(repo: Path, home: Path) -> list[Path]:
    """Where skills are looked up, highest priority first."""
    return [repo / ".vibe" / "skills", home / ".vibe" / "skills"]


def available(repo: Path, home: Path | None = None) -> list[str]:
    home = home or Path.home()
    names = {p.parent.name for folder in _folders(repo, home) for p in folder.glob("*/SKILL.md")}
    return sorted(names)


def load(name: str, repo: Path, home: Path | None = None) -> str:
    """The skill's instructions without its frontmatter."""
    home = home or Path.home()
    if not _NAME.fullmatch(name):
        raise SkillError(f"invalid skill name {name!r}")
    for folder in _folders(repo, home):
        path = folder / name / "SKILL.md"
        if path.is_file():
            body = _FRONTMATTER.sub("", path.read_text(), count=1).strip()
            if not body:
                raise SkillError(f"skill {name!r} is empty ({path})")
            return body
    names = available(repo, home)
    if names:
        raise SkillError(f"unknown skill {name!r}; available: {', '.join(names)}")
    where = " or ".join(str(folder) for folder in _folders(repo, home))
    raise SkillError(f"unknown skill {name!r}; no skills found in {where}")


def block(name: str, repo: Path, home: Path | None = None) -> str:
    """The skill as a block of the job's prompt."""
    return f"## Skill: {name}\n\n{load(name, repo, home)}\n\n---\n\n"
