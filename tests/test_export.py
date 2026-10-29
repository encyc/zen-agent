"""Session export: markdown/html rendering, escaping, and the CLI command."""


import pytest

import pyagent.agent.session as session_module
from pyagent.export import export_html, export_markdown, export_session
from pyagent.types import (
    AssistantMessage,
    ImageContent,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def transcript() -> list:
    return [
        SystemMessage(content="be brief", timestamp=1),
        UserMessage(content="what is 2+2?", timestamp=2),
        AssistantMessage(
            content=[
                ThinkingContent(thinking="simple"),
                TextContent(text="it is 4"),
                ToolCall(id="c1", name="bash", arguments={"command": "echo 4"}),
            ],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="tool_use",
            timestamp=3,
        ),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[TextContent(text="4")],
            is_error=True,
            timestamp=4,
        ),
    ]


def test_markdown_contains_roles_text_thinking_and_tool_calls():
    md = export_markdown(transcript(), title="demo")
    assert md.startswith("# demo")
    assert "## system" in md and "## user" in md and "## assistant" in md
    assert "it is 4" in md
    assert "<details><summary>thinking</summary>" in md
    assert "**tool call `bash`**" in md
    assert '"command": "echo 4"' in md
    assert "## tool result: bash ⚠️ error" in md


def test_html_escapes_untrusted_model_output():
    messages = [
        UserMessage(content="<script>alert(1)</script> & more", timestamp=1),
        AssistantMessage(
            content=[TextContent(text='<img src=x onerror="boom">')],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="stop",
            timestamp=2,
        ),
    ]
    html = export_html(messages, title="<title>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp; more" in html
    assert "&lt;img" in html
    assert "&lt;title&gt;" in html
    assert "<title>&lt;title&gt;</title>" in html


def test_html_marks_errors_and_images():
    html = export_html(transcript())
    assert 'section class="error"' in html
    assert "[image image/png]" in export_html(
        [UserMessage(content=[ImageContent(data="AAA", mime_type="image/png")], timestamp=1)]
    )


def test_empty_transcript_still_renders():
    assert export_markdown([]).strip() == "# pyagent session"
    assert "<h1>pyagent session</h1>" in export_html([])


def test_export_session_writes_beside_the_session_and_keeps_it_unchanged(tmp_path):
    path = tmp_path / "s.jsonl"
    session = session_module.Session(path)
    for message in transcript():
        session.append(message)
    before = path.read_bytes()

    written = export_session(path, "md")
    assert written == tmp_path / "s.md"
    assert "it is 4" in written.read_text(encoding="utf-8")
    assert path.read_bytes() == before  # read-only export


def test_export_session_unknown_format_is_rejected(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="unknown export format"):
        export_session(path, "pdf")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "sessions_dir", lambda: sessions)
    return sessions


def write_session(sessions, name: str):
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / name
    session = session_module.Session(path)
    for message in transcript():
        session.append(message)
    return path


def test_cli_exports_the_latest_session(capsys, isolated):
    import pyagent.cli.main as cli_main

    path = write_session(isolated, "20260101-000000.jsonl")
    assert cli_main.main(["export", "latest", "--format", "html"]) == 0
    written = path.with_suffix(".html")
    assert written.is_file()
    assert str(written) in capsys.readouterr().out


def test_cli_exports_an_explicit_session_and_output_path(capsys, tmp_path, isolated):
    import pyagent.cli.main as cli_main

    path = write_session(isolated, "20260101-000000.jsonl")
    out = tmp_path / "share.md"
    assert cli_main.main(["export", str(path), "-o", str(out)]) == 0
    assert out.is_file()
    assert "what is 2+2?" in out.read_text(encoding="utf-8")


def test_cli_rejects_a_missing_session(capsys):
    import pyagent.cli.main as cli_main

    assert cli_main.main(["export", "/nope/session.jsonl"]) == 1
    assert "no such session" in capsys.readouterr().err
