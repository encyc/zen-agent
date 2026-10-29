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
