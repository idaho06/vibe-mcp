"""Job scopes: repo-relative path prefixes. An empty scope means the whole repository."""

import posixpath
from collections.abc import Iterable

Scope = tuple[str, ...]


def normalize(scope: Iterable[str] | None) -> Scope:
    """Clean prefixes; reject paths outside the repo. Any root entry widens it to the whole repo."""
    prefixes: list[str] = []
    for raw in scope or ():
        if raw.startswith("/"):
            raise ValueError(f"scope path must be relative to the repo: {raw!r}")
        path = posixpath.normpath(raw) if raw else "."
        if path == ".." or path.startswith("../"):
            raise ValueError(f"scope path escapes the repo: {raw!r}")
        if path == ".":
            return ()
        prefixes.append(path)
    return tuple(prefixes)


def _covers(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def overlaps(a: Scope, b: Scope) -> bool:
    """True if some path could belong to both scopes (an empty scope overlaps everything)."""
    if not a or not b:
        return True
    return any(_covers(p, q) or _covers(q, p) for p in a for q in b)


def violations(scope: Scope, files: Iterable[str]) -> list[str]:
    """Files not covered by the scope, in the given order."""
    if not scope:
        return []
    return [f for f in files if not any(_covers(p, f) for p in scope)]
