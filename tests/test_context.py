from pathlib import Path

from vibe_mcp.context import MAX_CHARS, blocks


def test_blocks_come_from_the_listed_files_in_order(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Standards.\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "STYLE.md").write_text("\n  Style.  \n")
    text = blocks(tmp_path, ("docs/STYLE.md", "AGENTS.md"))
    assert text == (
        "## Project context: docs/STYLE.md\n\nStyle.\n\n---\n\n"
        "## Project context: AGENTS.md\n\nStandards.\n\n---\n\n"
    )


def test_missing_empty_and_non_file_entries_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "EMPTY.md").write_text("  \n\n")
    (tmp_path / "adir").mkdir()
    assert blocks(tmp_path, ("MISSING.md", "EMPTY.md", "adir")) == ""
    assert blocks(tmp_path, ()) == ""


def test_a_symlinked_file_is_followed(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Standards.")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    assert "Standards." in blocks(tmp_path, ("CLAUDE.md",))


def test_a_huge_file_is_cut(tmp_path: Path) -> None:
    (tmp_path / "BIG.md").write_text("x" * (MAX_CHARS + 500))
    text = blocks(tmp_path, ("BIG.md",))
    assert "x" * MAX_CHARS in text and "x" * (MAX_CHARS + 1) not in text
    assert "[truncated]" in text
