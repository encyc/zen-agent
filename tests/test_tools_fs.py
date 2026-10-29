"""File tool tests: read/write/edit/list against a temporary tree.

Tools are driven directly (``execute(tool_call_id, args, abort)``) rather than
through the loop; the loop integration is covered by ``test_loop.py``.
"""

import pytest

from pyagent.tools.fs import (
    EDIT_FILE,
    LIST_DIR,
    MAX_READ_LINES,
    READ_FILE,
    WRITE_FILE,
)


async def call(tool, **args):
    return await tool.execute("call_1", args, None)


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# write + read round trip
# ---------------------------------------------------------------------------


async def test_write_then_read_round_trip():
    written = await call(WRITE_FILE, path="notes/a.txt", content="hello\nworld\n")
    assert not written.is_error
    assert "wrote 12 characters" in written.content[0].text

    read = await call(READ_FILE, path="notes/a.txt")
    assert not read.is_error
    assert read.content[0].text == "hello\nworld\n"


async def test_write_creates_parent_directories():
    result = await call(WRITE_FILE, path="deep/nested/dir/file.txt", content="x")
    assert not result.is_error
    from pathlib import Path

    assert Path("deep/nested/dir/file.txt").read_text() == "x"


async def test_read_missing_file_is_an_error_result():
    result = await call(READ_FILE, path="nope.txt")
    assert result.is_error
    assert "file not found" in result.content[0].text


async def test_read_directory_is_an_error_result():
    from pathlib import Path

    Path("adir").mkdir()
    result = await call(READ_FILE, path="adir")
    assert result.is_error
    assert "directory" in result.content[0].text


async def test_read_binary_file_is_refused():
    from pathlib import Path

    Path("bin.dat").write_bytes(b"abc\x00def")
    result = await call(READ_FILE, path="bin.dat")
    assert result.is_error
    assert "binary" in result.content[0].text


async def test_read_non_utf8_is_refused():
    from pathlib import Path

    Path("latin1.txt").write_bytes("café".encode("latin-1"))
    result = await call(READ_FILE, path="latin1.txt")
    assert result.is_error
    assert "UTF-8" in result.content[0].text


async def test_read_truncates_long_files_with_a_hint():
    from pathlib import Path

    body = "\n".join(f"line {i}" for i in range(MAX_READ_LINES + 50))
    Path("long.txt").write_text(body)
    result = await call(READ_FILE, path="long.txt")
    assert not result.is_error
    text = result.content[0].text
    assert "truncated" in text
    assert f"of {MAX_READ_LINES + 50} lines" in text
    assert text.count("\n") >= MAX_READ_LINES - 1
    assert result.details["total_lines"] == MAX_READ_LINES + 50


# ---------------------------------------------------------------------------
# edit_file: unique match is the whole point
# ---------------------------------------------------------------------------


async def test_edit_replaces_a_unique_match():
    from pathlib import Path

    Path("code.py").write_text("def f():\n    return 1\n")
    result = await call(
        EDIT_FILE, path="code.py", old_string="return 1", new_string="return 2"
    )
    assert not result.is_error
    assert Path("code.py").read_text() == "def f():\n    return 2\n"


async def test_edit_with_two_matches_refuses_and_leaves_file_untouched():
    from pathlib import Path

    original = "x = 1\ny = 1\n"
    Path("two.py").write_text(original)
    result = await call(EDIT_FILE, path="two.py", old_string="= 1", new_string="= 2")
    assert result.is_error
    assert "2 times" in result.content[0].text
    assert "nothing changed" in result.content[0].text
    assert Path("two.py").read_text() == original  # Review Focus #4


async def test_edit_with_zero_matches_refuses():
    from pathlib import Path

    Path("one.py").write_text("a\n")
    result = await call(EDIT_FILE, path="one.py", old_string="zzz", new_string="b")
    assert result.is_error
    assert "not found" in result.content[0].text
    assert Path("one.py").read_text() == "a\n"


async def test_edit_with_empty_old_string_refuses():
    from pathlib import Path

    Path("f.txt").write_text("abc")
    result = await call(EDIT_FILE, path="f.txt", old_string="", new_string="x")
    assert result.is_error
    assert Path("f.txt").read_text() == "abc"


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


async def test_list_dir_marks_directories_and_sizes():
    from pathlib import Path

    Path("tree/sub").mkdir(parents=True)
    Path("tree/file.txt").write_text("12345")
    result = await call(LIST_DIR, path="tree")
    assert not result.is_error
    text = result.content[0].text
    assert "2 entries" in text
    assert "d  sub/" in text
    assert "f  file.txt" in text
    assert "5 B" in text
    # directories sort before files
    assert text.index("d  sub/") < text.index("f  file.txt")


async def test_list_dir_missing_is_an_error_result():
    result = await call(LIST_DIR, path="ghost")
    assert result.is_error
    assert "not found" in result.content[0].text


# ---------------------------------------------------------------------------
# schemas must be truthful: the loop validates against them
# ---------------------------------------------------------------------------


async def test_schemas_declare_required_arguments():
    assert READ_FILE.parameters["required"] == ["path"]
    assert WRITE_FILE.parameters["required"] == ["path", "content"]
    assert EDIT_FILE.parameters["required"] == ["path", "old_string", "new_string"]
    assert LIST_DIR.parameters["required"] == ["path"]
    for tool in (READ_FILE, WRITE_FILE, EDIT_FILE, LIST_DIR):
        assert tool.parameters["type"] == "object"
        for name in tool.parameters["required"]:
            assert name in tool.parameters["properties"]


async def test_tools_are_validated_by_the_loop_before_execution():
    """A missing required argument never reaches the tool body."""
    from pyagent.agent.validate import validate_arguments

    assert validate_arguments(WRITE_FILE.parameters, {"path": "a"}) is not None
    assert validate_arguments(WRITE_FILE.parameters, {"path": "a", "content": "b"}) is None


# ---------------------------------------------------------------------------
# S1-T5: unified diff in tool details
# ---------------------------------------------------------------------------


async def test_edit_result_carries_a_unified_diff():
    from pathlib import Path

    Path("d.py").write_text("a = 1\nb = 2\n")
    result = await call(EDIT_FILE, path="d.py", old_string="b = 2", new_string="b = 99")
    diff = result.details["diff"]
    assert "-b = 2" in diff and "+b = 99" in diff
    assert diff.startswith("--- a/d.py") and "+++ b/d.py" in diff


async def test_write_result_diff_for_new_file_is_all_additions():
    result = await call(WRITE_FILE, path="new.txt", content="line1\nline2\n")
    diff = result.details["diff"]
    assert "+line1" in diff and "+line2" in diff
    assert "-line1" not in diff


async def test_write_result_diff_for_overwrite_shows_removals():
    from pathlib import Path

    Path("f.txt").write_text("old\n")
    result = await call(WRITE_FILE, path="f.txt", content="brand new\n")
    diff = result.details["diff"]
    assert "-old" in diff and "+brand new" in diff


async def test_huge_diff_is_truncated():
    from pyagent.tools.fs import MAX_DIFF_LINES, unified_diff

    diff = unified_diff("big.txt", "", "\n".join(f"line{i}" for i in range(500)))
    assert "more diff lines" in diff
    assert len(diff.splitlines()) <= MAX_DIFF_LINES + 1
