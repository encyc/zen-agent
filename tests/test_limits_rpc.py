"""The ``limits`` RPC kind: get / set / reset against the real config file.

These tests pin the contract the desktop page consumes: a flat knob list with
effective values, defaults, provenance ("did the file set it?"), and the session
override chip. They also pin the write-through: a ``set`` must land in
``config.toml`` without disturbing the rest of the file.
"""

import json

import pytest

from zenagent.config import load_user_config
from zenagent.limits import Limits, read_managed, write_managed


def build_server(runtime, writes: list[str]):
    from zenagent.cli.rpc import RpcServer

    return RpcServer(runtime=runtime, write_line=writes.append)


async def call(server, writes: list[str], payload: dict) -> dict:
    """Send one request; a ValueError means the server raised instead of
    reporting a failure frame, which is itself a contract breach."""
    payload.setdefault("id", "l1")
    await server._handle_line(json.dumps(payload))
    return json.loads(writes[-1])


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point the RPC at a scratch config.toml (never the developer's own)."""
    path = tmp_path / "config.toml"
    path.write_text(
        '# hand-written\n\ndefault_model = "deepseek-flash"\n\n[tools]\nallow = ["read_*"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("zenagent.cli.rpc.ops_config.global_config_path", lambda: path)
    monkeypatch.setattr("zenagent.config.config_path", lambda: path)
    return path


def knobs_by_key(result: dict) -> dict[str, dict]:
    return {row["key"]: row for row in result["knobs"]}


async def test_get_lists_every_knob_with_defaults(runtime_factory, config_file):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "limits", "op": "get"})

    assert result["ok"] is True
    assert result["path"] == str(config_file)
    assert result["groups"] == ["skills", "context", "agent", "subagents", "tools"]
    knobs = knobs_by_key(result)
    assert len(knobs) == 14
    index = knobs["limits.skills.max_index_bytes"]
    assert index["value"] == 12288
    assert index["default"] == 12288
    assert index["explicit"] is False
    assert index["apply"] == "immediate"
    assert index["unit"] == "bytes"
    # The already-shipped subagent budgets are part of the same page.
    assert knobs["task.max_turns"]["section"] == "subagents"
    assert knobs["task.max_cost"]["value"] is None
    assert knobs["task.max_cost"]["nullable"] is True


async def test_set_writes_the_file_and_adopts_the_value(runtime_factory, config_file):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {
            "type": "limits",
            "op": "set",
            "key": "limits.skills.max_index_bytes",
            "value": 16384,
        },
    )

    assert result["ok"] is True
    knobs = knobs_by_key(result)
    assert knobs["limits.skills.max_index_bytes"]["value"] == 16384
    assert knobs["limits.skills.max_index_bytes"]["explicit"] is True
    assert runtime.limits.max_index_bytes == 16384
    text = config_file.read_text(encoding="utf-8")
    assert "# hand-written" in text  # the surgical writer kept the comments
    assert 'allow = ["read_*"]' in text
    assert "max_index_bytes = 16384" in text
    assert load_user_config(config_file).limits.max_index_bytes == 16384


async def test_reset_deletes_the_key(runtime_factory, config_file):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)
    await call(
        server,
        writes,
        {"type": "limits", "op": "set", "key": "limits.agent.max_turns", "value": 40},
    )

    result = await call(
        server,
        writes,
        {"type": "limits", "op": "reset", "key": "limits.agent.max_turns"},
    )

    assert result["ok"] is True
    assert knobs_by_key(result)["limits.agent.max_turns"]["explicit"] is False
    assert "max_turns = 40" not in config_file.read_text(encoding="utf-8")


async def test_set_rejects_out_of_range_and_leaves_the_file(
    tmp_path, runtime_factory, config_file
):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)
    before = config_file.read_text(encoding="utf-8")

    result = await call(
        server,
        writes,
        {
            "type": "limits",
            "op": "set",
            "key": "limits.skills.max_index_bytes",
            "value": 10,
        },
    )

    assert result["type"] == "error"
    assert "must be between" in result["message"]
    assert config_file.read_text(encoding="utf-8") == before


async def test_unknown_key_and_op_are_errors(runtime_factory, config_file):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    bad_key = await call(
        server,
        writes,
        {"type": "limits", "op": "set", "key": "limits.nope.x", "value": 1},
    )
    assert bad_key["type"] == "error"
    assert "unknown limit" in bad_key["message"]

    bad_op = await call(server, writes, {"type": "limits", "op": "nope"})
    assert bad_op["type"] == "error"


async def test_subagent_knob_writes_into_task_table(runtime_factory, config_file):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {"type": "limits", "op": "set", "key": "task.timeout_s", "value": 60},
    )

    assert result["ok"] is True
    assert "[task]" in config_file.read_text(encoding="utf-8")
    assert load_user_config(config_file).task_timeout_s == 60
    assert runtime.user_config.task_timeout_s == 60


async def test_clearing_the_unbounded_cost_knob(runtime_factory, config_file):
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)
    await call(
        server,
        writes,
        {"type": "limits", "op": "set", "key": "task.max_cost", "value": 2.5},
    )
    assert load_user_config(config_file).task_max_cost == 2.5

    result = await call(
        server, writes, {"type": "limits", "op": "reset", "key": "task.max_cost"}
    )

    assert result["ok"] is True
    assert load_user_config(config_file).task_max_cost is None


async def test_session_overrides_are_reported(runtime_factory, config_file):
    runtime = await runtime_factory(max_turns=20)
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "limits", "op": "get"})

    assert (
        knobs_by_key(result)["limits.agent.max_turns"]["override"] == "--max-turns 20"
    )


async def test_write_refuses_unknown_managed_keys(runtime_factory, config_file):
    """A key from a future build must not be silently deleted by a rewrite."""
    config_file.write_text(
        config_file.read_text(encoding="utf-8") + "\n[task]\nfuture_budget = 1\n",
        encoding="utf-8",
    )
    runtime = await runtime_factory()
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(
        server,
        writes,
        {"type": "limits", "op": "set", "key": "limits.agent.max_turns", "value": 30},
    )

    assert result["type"] == "error"
    assert "unknown keys" in result["message"]
    assert "future_budget = 1" in config_file.read_text(encoding="utf-8")


async def test_reading_task_values_from_the_file(runtime_factory, config_file):
    """A value the runtime did not adopt at boot is still reported from disk.

    ``app_env`` stubs ``runtime.load_user_config`` to a fixed default config,
    so the runtime's ``[task]`` stays at its defaults; the page must show the
    file's value as the source of truth for the next session.
    """
    tables = read_managed(config_file)
    tables["task"] = {"max_turns": 123, "keep_transcripts": 7}
    write_managed(config_file, tables)
    runtime = await runtime_factory()
    # Pretend the session booted before that edit: the runtime copy is stale.
    runtime.user_config.task_max_turns = 300
    writes: list[str] = []
    server = build_server(runtime, writes)

    result = await call(server, writes, {"type": "limits", "op": "get"})

    knobs = knobs_by_key(result)
    assert knobs["task.max_turns"]["value"] == 123
    assert knobs["task.max_turns"]["explicit"] is True
    assert knobs["task.keep_transcripts"]["value"] == 7
    assert Limits().max_turns == 100
