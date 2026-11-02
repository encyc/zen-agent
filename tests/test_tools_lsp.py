"""lsp tool: server mapping, missing-server errors and the five actions."""

import shutil
import sys
from pathlib import Path

import pytest

import zenagent.tools.lsp as lsp_module
from zenagent.tools.lsp import LSP, ServerSpec, server_for

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_lsp_server.py"


@pytest.fixture
async def fake_server(monkeypatch):
    spec = ServerSpec(
        name="fake",
        command=(sys.executable, str(FIXTURE)),
        extensions=(".py",),
    )
    monkeypatch.setattr(
        lsp_module, "server_for", lambda path: spec if path.suffix == ".py" else None
    )
    yield spec
    await lsp_module.shutdown_clients()


def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "mod.py"
    path.write_text("def foo():\n    return 1\n\nprint(foo())\n")
    return path


async def call(**args):
    return await LSP.execute("c1", args, None)


def test_server_for_maps_extensions():
    assert server_for(Path("a.py")).name == "pyright"
    assert server_for(Path("a.ts")).name == "typescript"
    assert server_for(Path("a.tsx")).name == "typescript"
    assert server_for(Path("a.md")) is None


async def test_symbols_action(fake_server, tmp_path):
    result = await call(action="symbols", path=str(source_file(tmp_path)))
    assert result.is_error is False
    assert "foo  function  line 1" in result.content[0].text


async def test_diagnostics_action(fake_server, tmp_path):
    result = await call(action="diagnostics", path=str(source_file(tmp_path)))
    assert result.is_error is False
    text = result.content[0].text
    assert "mod.py:1:3: error: fake error (fake)" in text


async def test_references_and_definition(fake_server, tmp_path):
    path = source_file(tmp_path)
    references = await call(action="references", path=str(path), line=1, character=0)
    assert references.is_error is False
    assert "mod.py:3:2" in references.content[0].text

    definition = await call(action="definition", path=str(path), line=3, character=6)
    assert definition.is_error is False
    assert "mod.py:2:1" in definition.content[0].text


async def test_rename_returns_the_workspace_edit(fake_server, tmp_path):
    result = await call(
        action="rename", path=str(source_file(tmp_path)), line=1, character=4, new_name="bar"
    )
    assert result.is_error is False
    assert "1 edit(s)" in result.content[0].text
    assert "bar" in result.content[0].text


def test_format_workspace_edit_text_document_edit():
    """pyright returns documentChanges with kind-less TextDocumentEdit items."""
    edit = {
        "documentChanges": [
            {
                "textDocument": {"uri": "file:///w/pkg/mod.py", "version": None},
                "edits": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 9},
                        },
                        "newText": "welcome",
                    }
                ],
            },
            {
                "textDocument": {"uri": "file:///w/pkg/app.py", "version": None},
                "edits": [
                    {
                        "range": {
                            "start": {"line": 4, "character": 19},
                            "end": {"line": 4, "character": 24},
                        },
                        "newText": "welcome",
                    }
                ],
            },
        ]
    }
    lines = lsp_module._format_workspace_edit(edit)
    assert "/w/pkg/mod.py: 1 edit(s)" in lines
    assert "/w/pkg/app.py: 1 edit(s)" in lines
    assert "  line 1: → 'welcome'" in lines
    assert "  line 5: → 'welcome'" in lines


async def test_unknown_action_and_extension_errors(fake_server, tmp_path):
    unknown = await call(action="hover", path=str(source_file(tmp_path)))
    assert unknown.is_error is True
    assert "unknown action" in unknown.content[0].text

    other = tmp_path / "notes.md"
    other.write_text("x\n")
    result = await call(action="symbols", path=str(other))
    assert result.is_error is True
    assert "no language server configured" in result.content[0].text


async def test_missing_server_binary_is_reported(fake_server, tmp_path, monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(
        lsp_module.shutil,
        "which",
        lambda name: None if name == sys.executable else real_which(name),
    )
    result = await call(action="symbols", path=str(source_file(tmp_path)))
    assert result.is_error is True
    assert "not installed" in result.content[0].text


async def test_missing_file_is_an_error(fake_server, tmp_path):
    result = await call(action="symbols", path=str(tmp_path / "nope.py"))
    assert result.is_error is True
    assert "file not found" in result.content[0].text


@pytest.mark.skipif(
    shutil.which("pyright-langserver") is None,
    reason="pyright-langserver is not installed",
)
async def test_real_pyright_reports_a_type_error(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("value: int = 'not an int'\n")
    try:
        result = await call(action="diagnostics", path=str(path))
        assert result.is_error is False
        assert "m.py" in result.content[0].text
    finally:
        await lsp_module.shutdown_clients()


async def test_rename_waits_for_analysis_before_returning_the_edit(
    fake_server, tmp_path
):
    """Regression: a rename issued right after didOpen must not return a
    partial (open-file-only) workspace edit; the tool waits for the server's
    analysis-complete signal (publishDiagnostics) first."""
    result = await call(
        action="rename", path=str(source_file(tmp_path)), line=1, character=4, new_name="bar"
    )
    assert result.is_error is False
    text = result.content[0].text
    # pyright's real shape: TextDocumentEdit entries in documentChanges;
    # both the open file and the referencing file must be listed.
    assert "/other/mod.py: 1 edit(s)" in text  # the referencing file
    assert "mod.py: 1 edit(s)" in text  # the open file itself
