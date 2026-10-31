"""glob/grep tests against a temporary tree; the rg path is skipped without rg."""

import shutil

import pytest

from zenagent.tools import search as search_module
from zenagent.tools.search import GLOB, GREP


async def call(tool, **args):
    return await tool.execute("call_1", args, None)


@pytest.fixture(autouse=True)
def tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("import os\n\n# TODO: fix\nprint('todo')\n")
    (tmp_path / "pkg" / "b.py").write_text("x = 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("TODO: write docs\n")
    (tmp_path / "bin.dat").write_bytes(b"TODO\x00binary\n")
    return tmp_path


async def test_glob_finds_files_by_pattern():
    result = await call(GLOB, pattern="**/*.py")
    assert not result.is_error
    text = result.content[0].text
    assert "pkg/a.py" in text and "pkg/b.py" in text
    assert "readme.md" not in text
    assert result.details["matches"] == 2


async def test_glob_reports_no_matches():
    result = await call(GLOB, pattern="**/*.rs")
    assert not result.is_error
    assert "no files match" in result.content[0].text


async def test_glob_truncates_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(search_module, "MAX_GLOB_RESULTS", 2)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    result = await call(GLOB, pattern="*.txt")
    text = result.content[0].text
    assert "3 more matches not shown" in text
    assert result.details["matches"] == 5


async def test_glob_rejects_a_missing_directory():
    result = await call(GLOB, pattern="*", path="ghost")
    assert result.is_error


async def test_grep_python_fallback_finds_matches(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await call(GREP, pattern="TODO")
    assert not result.is_error
    text = result.content[0].text
    assert "pkg/a.py:3:# TODO: fix" in text
    assert "docs/readme.md:1:TODO: write docs" in text
    assert result.details["engine"] == "python"
    # binary files are skipped by the fallback scanner
    assert "bin.dat" not in text


async def test_grep_glob_filter_limits_files(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await call(GREP, pattern="TODO", glob="*.md")
    assert "readme.md" in result.content[0].text
    assert "a.py" not in result.content[0].text


async def test_grep_invalid_regex_is_an_error():
    result = await call(GREP, pattern="[unclosed")
    assert result.is_error
    assert "invalid regular expression" in result.content[0].text


async def test_grep_missing_path_is_an_error():
    result = await call(GREP, pattern="x", path="ghost")
    assert result.is_error


async def test_grep_no_matches_message(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await call(GREP, pattern="does-not-exist-anywhere")
    assert not result.is_error
    assert result.details["matches"] == 0
    assert "no matches" in result.content[0].text


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
async def test_grep_with_ripgrep_matches_the_fallback():
    with_rg = await call(GREP, pattern="TODO")
    assert with_rg.details["engine"] == "rg"
    text = with_rg.content[0].text
    assert "# TODO: fix" in text and "TODO: write docs" in text


# ---------------------------------------------------------------------------
# grep pagination: a page is 20 files, 20 hits per file, skip pages by file
# ---------------------------------------------------------------------------


def make_needle_files(root, count, *, hits=1):
    for i in range(count):
        (root / f"f{i:02d}.txt").write_text("needle\n" * hits)


def hit_files(text):
    """File paths of ``path:line:needle`` result lines (in output order)."""
    return [
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line.endswith(":needle")
    ]


async def test_grep_pages_at_20_files_and_reports_next_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    make_needle_files(tmp_path, 45)
    result = await call(GREP, pattern="needle")
    assert not result.is_error
    text = result.content[0].text
    assert result.details["files"] == 20
    assert result.details["matches"] == 20
    assert result.details["truncated"] is True
    assert result.details["next_skip"] == 20
    assert result.details["engine"] == "python"
    assert "[... showing files 1-20 of 45; call grep with skip=20" in text


async def test_grep_last_page_has_no_next_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    make_needle_files(tmp_path, 45)
    result = await call(GREP, pattern="needle", skip=40)
    assert not result.is_error
    text = result.content[0].text
    assert result.details["files"] == 5
    assert result.details["matches"] == 5
    assert result.details["truncated"] is False
    assert result.details["next_skip"] is None
    assert hit_files(text) == [f"f{i:02d}.txt" for i in range(40, 45)]
    assert "showing files" not in text


async def test_grep_skip_counts_files_not_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    make_needle_files(tmp_path, 3, hits=3)
    result = await call(GREP, pattern="needle", skip=1)
    assert not result.is_error
    text = result.content[0].text
    assert result.details["files"] == 2
    assert result.details["matches"] == 6
    assert result.details["next_skip"] is None
    assert hit_files(text) == ["f01.txt"] * 3 + ["f02.txt"] * 3


async def test_grep_caps_hits_per_file(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    (tmp_path / "hot.txt").write_text("needle\n" * 30)
    (tmp_path / "cold.txt").write_text("needle\n")
    result = await call(GREP, pattern="needle")
    assert not result.is_error
    text = result.content[0].text
    assert result.details["files"] == 2
    # 20 from the hot file (the per-file cap) + 1 from the cold file.
    assert result.details["matches"] == 21
    assert result.details["truncated"] is True  # the hot file has more
    assert result.details["next_skip"] is None  # but no page is left
    assert "hot.txt:" in text and "cold.txt:" in text
    assert text.count("hot.txt:") == 20
    assert "more matches" in text


async def test_grep_pages_are_disjoint_and_cover_every_file(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    make_needle_files(tmp_path, 25)
    first = await call(GREP, pattern="needle")
    second = await call(GREP, pattern="needle", skip=first.details["next_skip"])
    assert not second.is_error
    first_files = hit_files(first.content[0].text)
    second_files = hit_files(second.content[0].text)
    assert len(first_files) == 20 and len(second_files) == 5
    assert set(first_files).isdisjoint(second_files)
    assert set(first_files) | set(second_files) == {
        f"f{i:02d}.txt" for i in range(25)
    }


async def test_grep_skip_past_eof_is_a_no_match_page_not_an_error(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    make_needle_files(tmp_path, 2)
    result = await call(GREP, pattern="needle", skip=2)
    assert not result.is_error
    assert "past" in result.content[0].text
    assert result.details["matches"] == 0
    assert result.details["files"] == 0
    assert result.details["truncated"] is False
    assert result.details["next_skip"] is None


async def test_grep_negative_skip_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = await call(GREP, pattern="needle", skip=-1)
    assert result.is_error
    assert "skip" in result.content[0].text


async def test_grep_scan_cap_bounds_the_collected_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(search_module, "MAX_GREP_RESULTS", 25)
    make_needle_files(tmp_path, 6, hits=10)
    result = await call(GREP, pattern="needle")
    assert not result.is_error
    text = result.content[0].text
    assert result.details["matches"] == 25
    assert result.details["files"] == 3
    assert result.details["truncated"] is True
    assert "scan stopped at 25 matches" in text


async def test_grep_schema_declares_skip():
    prop = GREP.parameters["properties"]["skip"]
    assert prop["type"] == "integer"
    assert prop.get("default") == 0
    assert "skip" not in GREP.parameters["required"]


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
async def test_grep_ripgrep_and_python_page_identically(tmp_path, monkeypatch):
    import shutil as shutil_module

    make_needle_files(tmp_path, 25)
    real_which = shutil_module.which
    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    from_python = await call(GREP, pattern="needle")
    monkeypatch.setattr(shutil_module, "which", real_which)
    from_rg = await call(GREP, pattern="needle")
    assert from_rg.details["engine"] == "rg"
    for key in ("files", "matches", "truncated", "next_skip"):
        assert from_rg.details[key] == from_python.details[key]
    assert hit_files(from_rg.content[0].text) == hit_files(
        from_python.content[0].text
    )


# ---------------------------------------------------------------------------
# P2-B: glob limit + hidden-file opt-in
# ---------------------------------------------------------------------------


async def test_glob_limit_is_clamped_and_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for index in range(5):
        (tmp_path / f"file{index}.txt").write_text("x")

    result = await call(GLOB, pattern="**/*.txt", limit=2)
    assert not result.is_error
    assert result.details["matches"] == 5
    assert result.details["shown"] == 2
    assert "more matches not shown" in result.content[0].text


async def test_glob_hides_dotfiles_unless_opted_in(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "visible.txt").write_text("x")
    (tmp_path / ".hidden.txt").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")

    default = await call(GLOB, pattern="**/*")
    text = default.content[0].text
    assert "visible.txt" in text
    assert ".hidden.txt" not in text
    assert ".git" not in text

    opted_in = await call(GLOB, pattern="**/*", include_hidden=True)
    text = opted_in.content[0].text
    assert "visible.txt" in text and ".hidden.txt" in text
    assert ".git" not in text, ".git is always skipped"
