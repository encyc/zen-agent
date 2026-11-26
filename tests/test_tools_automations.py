"""The automations tools: the agent manages this project's schedules."""

from __future__ import annotations

import json

import pytest

from zenagent.app.automations import (
    AutomationStore,
    Job,
    Schedule,
    StateStore,
    daemon_lock,
    resolve_session,
)
from zenagent.tools.automations import build_automation_tools


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    read_tool, edit_tool = build_automation_tools(root)
    return root, read_tool, edit_tool


async def _call(tool, **args):
    result = await tool.execute("call-1", args, None, None)
    return result.content[0].text, result.is_error


def test_the_two_tools_declare_their_approval_tiers(tools):
    """The tier is what keeps an unattended write out of a read-only session."""
    _root, read_tool, edit_tool = tools
    assert read_tool.approval == "read"
    assert edit_tool.approval == "write"


def test_neither_tool_takes_a_filesystem_path(tools):
    """`~/.zenagent` is a protected path: a `path` argument would be refused."""
    _root, read_tool, edit_tool = tools
    for tool in (read_tool, edit_tool):
        assert "path" not in tool.parameters["properties"]


async def test_list_is_empty_before_anything_exists(tools):
    _root, read_tool, _edit = tools
    text, is_error = await _call(read_tool, action="list")
    assert is_error is False
    assert "No automations" in text
    # It must also say that nothing will run yet.
    assert "zen daemon" in text


async def test_create_then_list_then_show(tools):
    root, read_tool, edit_tool = tools
    text, is_error = await _call(
        edit_tool,
        action="create",
        name="daily-report",
        prompt="read metrics.yaml and report anomalies",
        schedule="daily 09:00",
    )
    assert is_error is False
    assert "Created daily-report" in text
    # The boundary must be stated, never paraphrased as "full permission".
    assert "auto mode" in text
    assert "refused" in text
    assert "zen daemon" in text          # no scheduler is running yet

    # Only the name was minted: no transcript on disk.
    store = AutomationStore(root)
    job = store.get("daily-report")
    assert job is not None
    assert job.session.endswith(".jsonl")
    assert not resolve_session(root, job.session).exists()

    text, _ = await _call(read_tool, action="list")
    assert "daily-report" in text and "daily 09:00" in text

    text, _ = await _call(read_tool, action="show", name="daily-report")
    assert "read metrics.yaml" in text


async def test_create_rejects_a_bad_schedule_with_the_accepted_forms(tools):
    _root, _read, edit_tool = tools
    text, _ = await _call(
        edit_tool, action="create", name="x", prompt="p", schedule="hourly"
    )
    assert "daily HH:MM" in text
    assert AutomationStore(_root).load() == []


async def test_update_pauses_and_resumes(tools):
    root, _read, edit_tool = tools
    await _call(
        edit_tool, action="create", name="r", prompt="p", schedule="every 10m"
    )
    text, _ = await _call(edit_tool, action="update", name="r", enabled=False)
    assert "paused" in text
    assert AutomationStore(root).get("r").enabled is False
    text, _ = await _call(edit_tool, action="update", name="r", enabled=True)
    assert "enabled" in text


async def test_delete_keeps_the_transcript(tools):
    root, _read, edit_tool = tools
    await _call(edit_tool, action="create", name="r", prompt="p", schedule="daily 09:00")
    session = AutomationStore(root).get("r").session
    text, _ = await _call(edit_tool, action="delete", name="r")
    assert "Deleted r" in text
    assert session in text                 # tells the reader the history survives
    assert AutomationStore(root).load() == []


async def test_run_without_a_daemon_runs_nothing_and_says_so(tools):
    """Never claim a run that will not happen: state has one writer."""
    root, _read, edit_tool = tools
    await _call(edit_tool, action="create", name="r", prompt="p", schedule="daily 09:00")
    text, is_error = await _call(edit_tool, action="run", name="r")
    assert is_error is False               # not a failure, just not run
    assert "Nothing was run" in text
    assert "zen daemon" in text
    # And it really did not queue anything.
    assert not (daemon_lock(root).parent / "automations.request.json").exists()


async def test_run_with_a_daemon_writes_the_mailbox(tools, monkeypatch):
    root, _read, edit_tool = tools
    await _call(edit_tool, action="create", name="r", prompt="p", schedule="daily 09:00")

    from zenagent.tools import automations as module

    monkeypatch.setattr(module, "daemon_running", lambda _root: True)
    text, _ = await _call(edit_tool, action="run", name="r")
    assert "Asked the scheduler" in text
    request = daemon_lock(root).parent / "automations.request.json"
    assert json.loads(request.read_text())["jobs"][0]["name"] == "r"


async def test_an_unknown_action_is_an_error_result(tools):
    _root, read_tool, edit_tool = tools
    _text, is_error = await _call(read_tool, action="create")
    assert is_error is True
    _text, is_error = await _call(edit_tool, action="list")
    assert is_error is True


async def test_a_missing_name_is_reported_not_crashed(tools):
    _root, read_tool, edit_tool = tools
    text, is_error = await _call(read_tool, action="show")
    assert is_error is True
    text, _ = await _call(edit_tool, action="run", name="nope")
    assert "No automation named" in text
    text, _ = await _call(edit_tool, action="delete", name="nope")
    assert "No automation named" in text


async def test_list_reports_the_last_result_and_cost(tools):
    root, read_tool, _edit = tools
    store = AutomationStore(root)
    store.load()
    job = store.add(
        Job(
            id="",
            name="r",
            prompt="p",
            schedule=Schedule.parse("every 1h"),
            session="s.jsonl",
        )
    )
    from zenagent.app.automations import JobState

    StateStore(root).save({job.id: JobState(run_count=2, last_status="done")})
    from zenagent.app.runs import RunLedger
    from zenagent.project import project_dir

    ledger = RunLedger(project_dir(root=root) / "runs.jsonl")
    old = ledger.start(kind="automation", label="r")
    ledger.finish(old, status="done", termination="stop", cost=0.01)
    new = ledger.start(kind="automation", label="r")
    ledger.finish(new, status="done", termination="stop", cost=1.25)

    text, _ = await _call(read_tool, action="list")
    assert "done" in text
    assert "1.2500" in text           # newest, not oldest
