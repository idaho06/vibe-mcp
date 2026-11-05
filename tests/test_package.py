import vibe_mcp


def test_version_is_exposed() -> None:
    assert vibe_mcp.__version__ == "0.2.0"
