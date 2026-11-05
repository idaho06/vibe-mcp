"""Skills: instructions for Vibe kept as SKILL.md files and placed in front of a job's task.

They are looked up in Vibe's own skill folders, in the *main checkout*, so project skills work
even when `.vibe/` is gitignored and therefore missing from the job's worktree.
"""

import re
import tomllib
from pathlib import Path
from typing import NamedTuple

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


def _skill_dir(name: str, repo: Path, home: Path) -> Path:
    """The folder of the highest-priority skill called `name`."""
    if not _NAME.fullmatch(name):
        raise SkillError(f"invalid skill name {name!r}")
    for folder in _folders(repo, home):
        if (folder / name / "SKILL.md").is_file():
            return folder / name
    names = available(repo, home)
    if names:
        raise SkillError(f"unknown skill {name!r}; available: {', '.join(names)}")
    where = " or ".join(str(folder) for folder in _folders(repo, home))
    raise SkillError(f"unknown skill {name!r}; no skills found in {where}")


def _body(path: Path) -> str:
    return _FRONTMATTER.sub("", path.read_text(), count=1).strip()


def load(name: str, repo: Path, home: Path | None = None) -> str:
    """The skill's instructions without its frontmatter."""
    path = _skill_dir(name, repo, home or Path.home()) / "SKILL.md"
    body = _body(path)
    if not body:
        raise SkillError(f"skill {name!r} is empty ({path})")
    return body


class Fanout(NamedTuple):
    angles: dict[str, str]  # angle name -> what that reviewer looks for
    readonly: bool  # True: the merge job only reports; False: it may edit files


def fanout(name: str, repo: Path, home: Path | None = None) -> Fanout | None:
    """The reviewers of a fan-out skill, or None for an ordinary skill.

    A skill is a fan-out skill when its folder has an `angles/` subfolder with `<angle>.md` files.
    Its SKILL.md then holds the instructions for the merge job. An optional `fanout.toml` can say
    `readonly = true` for skills whose merge job only reports (a review).
    """
    folder = _skill_dir(name, repo, home or Path.home())
    files = sorted((folder / "angles").glob("*.md")) if (folder / "angles").is_dir() else []
    if not files:
        return None
    angles: dict[str, str] = {}
    for path in files:
        if not _NAME.fullmatch(path.stem):
            raise SkillError(f"invalid angle name {path.name!r} in skill {name!r}")
        angles[path.stem] = _body(path)
        if not angles[path.stem]:
            raise SkillError(f"angle {path.stem!r} of skill {name!r} is empty")
    options: dict = {}
    if (folder / "fanout.toml").is_file():
        try:
            options = tomllib.loads((folder / "fanout.toml").read_text())
        except tomllib.TOMLDecodeError as exc:
            raise SkillError(f"cannot read {folder / 'fanout.toml'}: {exc}") from exc
    if unknown := set(options) - {"readonly"}:
        raise SkillError(
            f"unknown option(s) in fanout.toml of {name!r}: {', '.join(sorted(unknown))}"
        )
    readonly = options.get("readonly", False)
    if not isinstance(readonly, bool):
        raise SkillError(
            f"readonly in fanout.toml of {name!r} must be true or false, got {readonly!r}"
        )
    return Fanout(angles, readonly)


def block(name: str, repo: Path, home: Path | None = None) -> str:
    """The skill as a block of the job's prompt."""
    return f"## Skill: {name}\n\n{load(name, repo, home)}\n\n---\n\n"
