"""Usage aggregation: per-model totals, tolerance, and the CLI command."""

import json

import pytest

import pyagent.agent.session as session_module
from pyagent.types import AssistantMessage, Cost, TextContent, Usage, UserMessage
from pyagent.usage import collect_usage, render_report, session_paths


def assistant(model: str, *, input_tokens=10, output=5, cache_read=0, cost=0.01):
    return AssistantMessage(
        content=[TextContent(text="ok")],
        provider="p",
        model=model,
        usage=Usage(
            input=input_tokens,
            output=output,
            cache_read=cache_read,
            cost=Cost(total=cost),
        ),
        stop_reason="stop",
        timestamp=1,
    )


def write_session(sessions, name: str, messages: list) -> None:
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / name
    session = session_module.Session(path)
    for message in messages:
        session.append(message)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "sessions_dir", lambda: sessions)
    return sessions


def test_aggregates_per_model_across_sessions(isolated):
    write_session(
        isolated,
        "a.jsonl",
        [UserMessage(content="hi", timestamp=1), assistant("m1"), assistant("m1", output=7)],
    )
    write_session(isolated, "b.jsonl", [assistant("m2", cache_read=100)])

    report = collect_usage(session_paths())
    assert report.sessions == 2
    assert report.per_model["m1"].calls == 2
    assert report.per_model["m1"].output == 12
    assert report.per_model["m2"].cache_read == 100
    assert report.total_cost == pytest.approx(0.03)


def test_tolerates_torn_and_unknown_lines(isolated):
    isolated.mkdir(parents=True)
    path = isolated / "broken.jsonl"
    path.write_text(
        '{"role": "assistant", "content": [], "provider": "p", "model": "m1",\n'
        'not json at all\n'
        + json.dumps(assistant("m1").to_json())
        + "\n"
    )
    report = collect_usage([path])
    assert report.per_model["m1"].calls == 1


def test_empty_directory_and_no_sessions(isolated):
    assert session_paths() == []
    assert collect_usage([]).per_model == {}
    assert "no usage" in render_report(collect_usage([]))


def test_render_report_lists_models_and_total(isolated):
    write_session(isolated, "a.jsonl", [assistant("m1")])
    text = render_report(collect_usage(session_paths()))
    assert "model" in text and "m1" in text
    assert "1 session(s)" in text


def test_since_filters_old_sessions(isolated, monkeypatch):
    import os
    import time

    write_session(isolated, "old.jsonl", [assistant("m1")])
    old = isolated / "old.jsonl"
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    write_session(isolated, "new.jsonl", [assistant("m2")])

    recent = session_paths("2000-01-01")
    assert len(recent) == 2
    filtered = session_paths("2999-01-01")
    assert filtered == []


def test_bad_since_raises():
    with pytest.raises(ValueError):
        session_paths("not-a-date")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_usage_table_and_json(isolated, capsys):
    import pyagent.cli.main as cli_main

    write_session(isolated, "a.jsonl", [assistant("m1")])
    assert cli_main.main(["usage"]) == 0
    out = capsys.readouterr().out
    assert "m1" in out and "1 session(s)" in out

    assert cli_main.main(["usage", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sessions"] == 1
    assert payload["models"]["m1"]["calls"] == 1


def test_cli_usage_rejects_bad_since(isolated, capsys):
    import pyagent.cli.main as cli_main

    assert cli_main.main(["usage", "--since", "yesterday"]) == 1
    assert "bad --since" in capsys.readouterr().err
