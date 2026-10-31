"""File tool tests: read/write/edit/list against a temporary tree.

Tools are driven directly (``execute(tool_call_id, args, abort)``) rather than
through the loop; the loop integration is covered by ``test_loop.py``.
"""

import pytest

from zenagent.tools.fs import (
    DEFAULT_READ_LIMIT,
    EDIT_FILE,
    LIST_DIR,
    MAX_READ_LIMIT,
    READ_FILE,
    READ_TRUNCATION_MARKER,
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

    total = DEFAULT_READ_LIMIT + 50
    body = "\n".join(f"line {i}" for i in range(total))
    Path("long.txt").write_text(body)
    result = await call(READ_FILE, path="long.txt")
    assert not result.is_error
    text = result.content[0].text
    assert READ_TRUNCATION_MARKER in text
    assert f"of {total} lines" in text
    assert f"offset={DEFAULT_READ_LIMIT}" in text
    assert text.count("\n") >= DEFAULT_READ_LIMIT - 1
    assert result.details["total_lines"] == total
    assert result.details["truncated"] is True


async def test_read_pages_with_offset_and_limit():
    from pathlib import Path

    Path("page.txt").write_text("\n".join(f"line {i}" for i in range(100)))
    result = await call(READ_FILE, path="page.txt", offset=40, limit=10)
    assert not result.is_error
    text = result.content[0].text
    assert text.splitlines()[:10] == [f"line {i}" for i in range(40, 50)]
    assert READ_TRUNCATION_MARKER in text  # next page is reachable
    assert result.details["offset"] == 40
    assert result.details["limit"] == 10
    assert result.details["total_lines"] == 100


async def test_read_last_page_has_no_next_offset_hint():
    from pathlib import Path

    Path("tail.txt").write_text("\n".join(f"line {i}" for i in range(20)))
    result = await call(READ_FILE, path="tail.txt", offset=15, limit=10)
    assert not result.is_error
    assert result.content[0].text.splitlines() == [f"line {i}" for i in range(15, 20)]
    assert READ_TRUNCATION_MARKER not in result.content[0].text
    assert result.details["truncated"] is False


async def test_read_limit_is_clamped_to_the_maximum():
    from pathlib import Path

    Path("big.txt").write_text("\n".join(f"line {i}" for i in range(MAX_READ_LIMIT + 10)))
    result = await call(READ_FILE, path="big.txt", limit=999_999)
    assert not result.is_error
    assert result.details["limit"] == MAX_READ_LIMIT
    assert len(result.content[0].text.splitlines()) >= MAX_READ_LIMIT


async def test_read_rejects_a_negative_offset():
    from pathlib import Path

    Path("x.txt").write_text("a\nb\n")
    result = await call(READ_FILE, path="x.txt", offset=-1)
    assert result.is_error
    assert "offset" in result.content[0].text


async def test_read_offset_past_eof_is_an_error_result():
    from pathlib import Path

    Path("x.txt").write_text("a\nb\n")
    result = await call(READ_FILE, path="x.txt", offset=99)
    assert result.is_error
    assert "past" in result.content[0].text or "EOF" in result.content[0].text


async def test_write_refuses_to_overwrite_with_a_truncated_read_projection():
    """The model read a truncated page and is about to clobber the file."""
    from pathlib import Path

    total = DEFAULT_READ_LIMIT + 20
    Path("data.txt").write_text("\n".join(f"line {i}" for i in range(total)))
    read = await call(READ_FILE, path="data.txt")
    assert READ_TRUNCATION_MARKER in read.content[0].text

    result = await call(WRITE_FILE, path="data.txt", content=read.content[0].text)
    assert result.is_error
    assert "truncated" in result.content[0].text.lower()
    assert "read_file" in result.content[0].text
    # The original file is untouched.
    assert len(Path("data.txt").read_text().splitlines()) == total


async def test_write_allows_a_shorter_normal_overwrite():
    from pathlib import Path

    Path("data.txt").write_text("long content " * 100)
    result = await call(WRITE_FILE, path="data.txt", content="short")
    assert not result.is_error
    assert Path("data.txt").read_text() == "short"


async def test_write_allows_content_with_the_marker_for_a_new_file():
    result = await call(WRITE_FILE, path="new.txt", content="[truncated: showing lines 1-2 of 9]")
    assert not result.is_error


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
    from zenagent.agent.validate import validate_arguments

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
    from zenagent.tools.fs import MAX_DIFF_LINES, unified_diff

    diff = unified_diff("big.txt", "", "\n".join(f"line{i}" for i in range(500)))
    assert "more diff lines" in diff
    assert len(diff.splitlines()) <= MAX_DIFF_LINES + 1


async def test_write_refuses_a_short_401_line_page_even_though_it_is_longer_with_the_footer():
    """The canonical near-boundary case: footer makes the page *longer* on disk."""
    from pathlib import Path


    Path("near.txt").write_text("\n".join("x" for _ in range(401)))
    read = await call(READ_FILE, path="near.txt")
    assert READ_TRUNCATION_MARKER in read.content[0].text

    result = await call(WRITE_FILE, path="near.txt", content=read.content[0].text)
    assert result.is_error
    assert len(Path("near.txt").read_text().splitlines()) == 401


async def test_write_refuses_a_budget_truncated_whole_file_projection():
    """A whole-file read capped by the kernel budget has no page footer."""
    from pathlib import Path

    from zenagent.agent.result_budget import apply_result_budget

    Path("huge.txt").write_text("H" * 150_000)
    read = await call(READ_FILE, path="huge.txt")
    # The loop applies the kernel budget before the model sees the result.
    projected = apply_result_budget(
        type(read)(content=read.content, details=read.details)
    )
    assert projected.details["budget"]["truncated"] is True

    result = await call(
        WRITE_FILE, path="huge.txt", content=projected.content[0].text
    )
    assert result.is_error
    assert Path("huge.txt").stat().st_size == 150_000


async def test_write_refuses_a_crlf_truncated_page():
    from pathlib import Path

    body = "\r\n".join(f"line {i}" for i in range(DEFAULT_READ_LIMIT + 5))
    Path("crlf.txt").write_bytes(body.encode())
    read = await call(READ_FILE, path="crlf.txt")
    assert READ_TRUNCATION_MARKER in read.content[0].text

    result = await call(WRITE_FILE, path="crlf.txt", content=read.content[0].text)
    assert result.is_error
    assert Path("crlf.txt").read_bytes().decode().splitlines() == [
        f"line {i}" for i in range(DEFAULT_READ_LIMIT + 5)
    ]


async def test_read_limit_zero_is_rejected_not_silently_defaulted():
    from pathlib import Path

    Path("x.txt").write_text("a\nb\n")
    result = await call(READ_FILE, path="x.txt", limit=0)
    assert result.is_error
    assert "limit" in result.content[0].text


async def test_read_empty_file_with_offset_past_eof_is_an_error():
    from pathlib import Path

    Path("empty.txt").write_text("")
    result = await call(READ_FILE, path="empty.txt", offset=3)
    assert result.is_error
    assert "past" in result.content[0].text or "EOF" in result.content[0].text


# ---------------------------------------------------------------------------
# write_file / edit_file: keep the existing file's CRLF and BOM conventions
# ---------------------------------------------------------------------------


async def test_write_keeps_crlf_when_given_lf_content():
    from pathlib import Path

    Path("win.txt").write_bytes(b"one\r\ntwo\r\n")
    result = await call(WRITE_FILE, path="win.txt", content="one\ntwo\nthree\n")
    assert not result.is_error
    assert Path("win.txt").read_bytes() == b"one\r\ntwo\r\nthree\r\n"


async def test_write_keeps_bom_and_crlf_of_the_existing_file():
    from pathlib import Path

    Path("bom.txt").write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    result = await call(WRITE_FILE, path="bom.txt", content="one\ntwo\nthree\n")
    assert not result.is_error
    assert Path("bom.txt").read_bytes() == b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n"


async def test_write_does_not_double_convert_content_already_using_crlf():
    from pathlib import Path

    Path("win.txt").write_bytes(b"a\r\nb\r\n")
    result = await call(WRITE_FILE, path="win.txt", content="a\r\nb\r\nc\r\n")
    assert not result.is_error
    assert Path("win.txt").read_bytes() == b"a\r\nb\r\nc\r\n"


async def test_write_new_file_stays_lf_without_a_bom():
    from pathlib import Path

    result = await call(WRITE_FILE, path="new.txt", content="a\nb\n")
    assert not result.is_error
    assert Path("new.txt").read_bytes() == b"a\nb\n"


async def test_edit_keeps_crlf_and_accepts_an_lf_old_string():
    from pathlib import Path

    Path("win.py").write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
    result = await call(
        EDIT_FILE,
        path="win.py",
        old_string="beta\ngamma",
        new_string="beta\nDELTA",
    )
    assert not result.is_error
    assert Path("win.py").read_bytes() == b"alpha\r\nbeta\r\nDELTA\r\n"


async def test_edit_keeps_bom_and_crlf_after_the_edit():
    from pathlib import Path

    Path("bom.py").write_bytes(b"\xef\xbb\xbfhello\r\nworld\r\n")
    result = await call(
        EDIT_FILE, path="bom.py", old_string="world", new_string="there"
    )
    assert not result.is_error
    assert Path("bom.py").read_bytes() == b"\xef\xbb\xbfhello\r\nthere\r\n"


# ---------------------------------------------------------------------------
# edit_file: replace_all
# ---------------------------------------------------------------------------


async def test_edit_replace_all_replaces_every_occurrence():
    from pathlib import Path

    Path("vars.py").write_text("a = 1\nb = 1\nc = 1\n")
    result = await call(
        EDIT_FILE,
        path="vars.py",
        old_string="= 1",
        new_string="= 2",
        replace_all=True,
    )
    assert not result.is_error
    assert Path("vars.py").read_text() == "a = 2\nb = 2\nc = 2\n"
    assert "3 occurrences" in result.content[0].text


async def test_edit_replace_all_with_zero_matches_is_an_error():
    from pathlib import Path

    Path("one.py").write_text("a\n")
    result = await call(
        EDIT_FILE,
        path="one.py",
        old_string="zzz",
        new_string="b",
        replace_all=True,
    )
    assert result.is_error
    assert "not found" in result.content[0].text
    assert Path("one.py").read_text() == "a\n"


async def test_edit_replace_all_false_still_requires_a_unique_match():
    from pathlib import Path

    original = "x = 1\ny = 1\n"
    Path("two.py").write_text(original)
    result = await call(
        EDIT_FILE,
        path="two.py",
        old_string="= 1",
        new_string="= 2",
        replace_all=False,
    )
    assert result.is_error
    assert "2 times" in result.content[0].text
    assert Path("two.py").read_text() == original


async def test_edit_schema_declares_replace_all():
    prop = EDIT_FILE.parameters["properties"]["replace_all"]
    assert prop["type"] == "boolean"
    assert prop.get("default") is False
    assert "replace_all" not in EDIT_FILE.parameters["required"]


async def test_edit_keeps_a_bom_even_when_old_string_mentions_it():
    from pathlib import Path

    Path("bom.py").write_bytes("\ufeffimport os\n".encode())
    result = await call(EDIT_FILE, path="bom.py", old_string="import os", new_string="import sys")
    assert not result.is_error
    assert Path("bom.py").read_bytes().startswith(b"\xef\xbb\xbf")
    assert "import sys" in Path("bom.py").read_text(encoding="utf-8")


async def test_writing_empty_content_to_a_bom_only_file_truncates_to_zero():
    from pathlib import Path

    Path("bom.txt").write_bytes(b"\xef\xbb\xbf")
    result = await call(WRITE_FILE, path="bom.txt", content="")
    assert not result.is_error
    assert Path("bom.txt").read_bytes() == b""
