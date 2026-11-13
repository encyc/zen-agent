"""RunLedger: durable, replayable, best-effort run records."""

from __future__ import annotations

import json
from pathlib import Path

from zenagent.app.runs import RunLedger, status_for_termination


def _ledger(tmp_path: Path) -> RunLedger:
    return RunLedger(tmp_path / "runs.jsonl")


def test_missing_file_reads_empty(tmp_path):
    assert _ledger(tmp_path).load() == []


def test_start_and_finish_round_trip(tmp_path):
    ledger = _ledger(tmp_path)
    run = ledger.start(
        kind="coding",
        agent_id="main",
        session="/p/sessions/a.jsonl",
        label="a",
    )
    assert run.status == "running"
    assert run.ended_at is None

    finished = ledger.finish(
        run,
        status="done",
        termination="stop",
        turns=3,
        cost=0.0123,
        duration_ms=42,
    )
    assert finished.ended_at is not None
    assert finished.cost == 0.0123

    [loaded] = ledger.load()
    assert loaded.id == run.id
    assert loaded.status == "done"
    assert loaded.termination == "stop"
    assert loaded.turns == 3
    assert loaded.cost == 0.0123
    assert loaded.duration_ms == 42
    assert loaded.session == "/p/sessions/a.jsonl"
    assert loaded.label == "a"


def test_last_record_per_id_wins(tmp_path):
    ledger = _ledger(tmp_path)
    run = ledger.start(kind="swarm", agent_id="research", work_item_id="w1")
    ledger.finish(run, status="failed", termination="error", error="boom")
    ledger.finish(run, status="done", termination="stop")

    [loaded] = ledger.load()
    assert loaded.status == "done"
    assert loaded.error is None


def test_bad_lines_are_skipped_with_warnings(tmp_path):
    path = tmp_path / "runs.jsonl"
    ledger = _ledger(tmp_path)
    run = ledger.start(kind="coding")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"type": "other"}) + "\n")

    rows = ledger.load()
    assert [row.id for row in rows] == [run.id]
    assert any("not JSON" in warning for warning in ledger.warnings)
    assert any("not a run" in warning for warning in ledger.warnings)


def test_list_filters_and_caps_newest_first(tmp_path):
    ledger = _ledger(tmp_path)
    first = ledger.start(kind="coding", agent_id="main")
    ledger.finish(first, status="done", termination="stop")
    second = ledger.start(kind="zen_worker", agent_id="worker-a")
    ledger.finish(second, status="done", termination="stop")
    third = ledger.start(kind="swarm", agent_id="research", work_item_id="w1")
    ledger.finish(third, status="failed", termination="max_turns")

    assert [row.id for row in ledger.list(kind="swarm")] == [third.id]
    assert [row.id for row in ledger.list(agent="worker-a")] == [second.id]
    assert [row.id for row in ledger.list(work_item="w1")] == [third.id]
    assert [row.id for row in ledger.list(status="failed")] == [third.id]
    assert len(ledger.list(since=0)) == 3
    assert ledger.list(since=third.started_at + 1) == []
    assert len(ledger.list(limit=2)) == 2
    assert ledger.list(limit=0) == []


def test_get_by_id(tmp_path):
    ledger = _ledger(tmp_path)
    run = ledger.start(kind="coding")
    assert ledger.get(run.id) == run
    assert ledger.get("missing") is None


def test_record_shape_is_camel_case(tmp_path):
    ledger = _ledger(tmp_path)
    run = ledger.start(
        kind="zen_worker",
        agent_id="worker",
        session="/p/s.jsonl",
        parent_run_id="r_parent",
        label="focus",
    )
    raw = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert raw["type"] == "run"
    assert raw["id"] == run.id
    assert raw["agentId"] == "worker"
    assert raw["parentRunId"] == "r_parent"
    assert raw["startedAt"] == run.started_at


def test_status_mapping():
    assert status_for_termination("stop") == "done"
    assert status_for_termination("tool_terminate") == "done"
    assert status_for_termination("aborted") == "aborted"
    assert status_for_termination("max_cost") == "failed"
    assert status_for_termination("error") == "failed"
    assert status_for_termination(None) == "done"


async def test_prepare_records_a_coding_run(app_env, monkeypatch):
    from conftest import text_stream

    from zenagent.app.agent_profile import AgentProfile
    from zenagent.app.bootstrap import prepare
    from zenagent.app.runtime import RuntimeOptions
    from zenagent.config import UserConfig
    from zenagent.project import project_dir

    monkeypatch.setattr(
        "zenagent.app.bootstrap.load_user_config",
        lambda *args, **kwargs: UserConfig(default_model="deepseek-flash"),
    )
    prepared = await prepare(
        RuntimeOptions(
            root=app_env,
            profile=AgentProfile(mcp=False, skills=False, templates=False),
        ),
        stream_fn=text_stream("ok"),
        views=False,
    )
    try:
        await prepared.runtime.agent.prompt("hello")
        rows = RunLedger(project_dir(root=app_env) / "runs.jsonl").list(kind="coding")
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "done"
        assert row.termination == "stop"
        assert row.agent_id == "main"
        assert row.session.endswith(".jsonl")
        assert row.turns == 1
        assert row.ended_at is not None
        assert row.duration_ms is not None
    finally:
        await prepared.runtime.close()
