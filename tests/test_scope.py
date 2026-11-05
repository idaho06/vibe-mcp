import pytest

from vibe_mcp.scope import normalize, overlaps, violations


def test_normalize_cleans_prefixes() -> None:
    assert normalize(["./src//parser/", "README.md"]) == ("src/parser", "README.md")


@pytest.mark.parametrize("scope", [None, [], [""], ["."], ["./"], ["src", "."]])
def test_empty_or_root_scope_means_whole_repo(scope: list[str] | None) -> None:
    assert normalize(scope) == ()


@pytest.mark.parametrize("bad", ["/etc", "../x", "src/../../x", "a/../.."])
def test_normalize_rejects_paths_outside_the_repo(bad: str) -> None:
    with pytest.raises(ValueError, match="scope"):
        normalize([bad])


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (("src",), ("src",), True),
        (("src",), ("src/parser",), True),
        (("src/parser",), ("src",), True),
        (("src",), ("srcs",), False),
        (("src/a", "docs"), ("src/b",), False),
        (("src/a", "docs"), ("lib", "docs/x"), True),
        ((), ("src",), True),
        (("src",), (), True),
        ((), (), True),
    ],
)
def test_overlaps(a: tuple[str, ...], b: tuple[str, ...], expected: bool) -> None:
    assert overlaps(a, b) is expected
    assert overlaps(b, a) is expected


def test_violations_lists_files_outside_the_scope() -> None:
    files = ["src/a.py", "src/sub/b.py", "srcs/c.py", "README.md", "docs/x.md"]
    assert violations(("src", "README.md"), files) == ["srcs/c.py", "docs/x.md"]


def test_whole_repo_scope_has_no_violations() -> None:
    assert violations((), ["anything/at/all.py"]) == []
