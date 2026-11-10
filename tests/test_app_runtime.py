"""Runtime: frontend-agnostic assembly of agent, session, tools and MCP."""

import pytest

from zenagent.agent import Agent
from zenagent.app.runtime import Runtime, RuntimeOptions


def _options() -> RuntimeOptions:
    return RuntimeOptions(mcp=False, skills=False, templates=False)


async def test_bootstrap_wires_agent_and_session(runtime_factory, app_env):
    runtime = await runtime_factory()

    assert isinstance(runtime.agent, Agent)
    assert runtime.agent.tools, "built-in tools must be wired"
    assert runtime.session.path.parent == app_env / "sessions"
    assert runtime.agent.model.provider == "deepseek"


async def test_close_is_idempotent(runtime_factory):
    runtime = await runtime_factory()
    await runtime.close()
    await runtime.close()


async def test_context_files_are_injected(runtime_factory, app_env, monkeypatch):
    monkeypatch.chdir(app_env)
    (app_env / "AGENTS.md").write_text("PROJECT RULE: use tabs.")
    runtime = await runtime_factory()
    assert "PROJECT RULE: use tabs." in runtime.agent.system_prompt


async def test_bootstrap_still_requires_a_key_by_default(app_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="missing API key"):
        await Runtime.bootstrap(_options())


async def test_bootstrap_can_open_a_keyless_workbench(app_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    runtime = await Runtime.bootstrap(_options(), allow_missing_key=True)
    try:
        assert runtime.missing_api_key == "DEEPSEEK_API_KEY"
        assert runtime.agent.api_key == ""
        assert runtime.model is not None and runtime.model.id.endswith("flash")
    finally:
        await runtime.close()


async def test_reload_api_key_adopts_a_key_saved_in_the_app(app_env, monkeypatch):
    from zenagent import settings

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    runtime = await Runtime.bootstrap(_options(), allow_missing_key=True)
    try:
        assert runtime.reload_api_key() is False, "nothing to adopt yet"

        settings.set_api_key("DEEPSEEK_API_KEY", "sk-saved")

        assert runtime.reload_api_key() is True
        assert runtime.agent.api_key == "sk-saved"
        assert runtime.missing_api_key is None
    finally:
        await runtime.close()


def test_public_embedding_api_is_importable():
    from zenagent.app import (  # noqa: F401
        ChatController,
        CommandRegistry,
        ModelManager,
        PermissionBroker,
        Runtime,
        SessionManager,
    )


async def test_switch_session_can_carry_todo_state(runtime_factory, tmp_path):
    from zenagent.agent.session import unique_session_path
    from zenagent.tools.todo import load_todo_phases, save_todo_phases

    runtime = await runtime_factory()
    old_path = runtime.session.path
    phases = [
        {"name": "work", "tasks": [{"content": "one", "status": "completed"}]}
    ]
    save_todo_phases(old_path, phases)

    new_path = unique_session_path()
    await runtime.switch_session(new_path, carry_todos=True)
    assert load_todo_phases(new_path) == phases
    assert runtime.todo_tool is not None


async def test_switch_session_without_carry_starts_empty(runtime_factory, tmp_path):
    from zenagent.tools.todo import load_todo_phases, save_todo_phases

    runtime = await runtime_factory()
    save_todo_phases(
        runtime.session.path,
        [{"name": "work", "tasks": [{"content": "one", "status": "pending"}]}],
    )
    new_path = tmp_path / "other-session.jsonl"
    await runtime.switch_session(new_path)
    assert load_todo_phases(new_path) == []


async def test_switch_session_restores_the_last_request_usage(runtime_factory):
    """Compaction and context math read the guard, so a resume must reseed it
    from the loaded transcript instead of leaving the last request unknown."""
    from zenagent.agent.session import Session, unique_session_path
    from zenagent.app.chat import ChatController
    from zenagent.types import AssistantMessage, TextContent, Usage

    runtime = await runtime_factory()
    path = unique_session_path()
    await Session(path).append(
        AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="m",
            usage=Usage(input=900, output=30, cache_read=800),
            stop_reason="stop",
            timestamp=1,
        )
    )

    await runtime.switch_session(path)

    assert runtime.guard.last_usage is not None
    assert runtime.guard.last_usage.input == 900
    assert ChatController(runtime).status.context_used == 930


async def test_bootstrap_resume_seeds_the_last_request_usage(runtime_factory):
    """``--resume`` loads the transcript before any live request; the guard
    must start from that transcript too."""
    from zenagent.agent.session import Session, unique_session_path
    from zenagent.app.chat import ChatController
    from zenagent.types import AssistantMessage, TextContent, Usage

    path = unique_session_path()
    await Session(path).append(
        AssistantMessage(
            content=[TextContent(text="hi")],
            provider="p",
            model="m",
            usage=Usage(input=900, output=30, cache_read=800),
            stop_reason="stop",
            timestamp=1,
        )
    )

    runtime = await runtime_factory(resume=path)

    assert runtime.guard.last_usage is not None
    assert runtime.guard.last_usage.input == 900
    assert ChatController(runtime).status.context_used == 930


async def test_two_runtimes_share_the_job_manager_until_the_last_closes(runtime_factory):
    from zenagent.tools.jobs import get_job_manager

    first = await runtime_factory()
    second = await runtime_factory()
    manager = get_job_manager()
    job = await manager.start("sleep 2", timeout_s=10)

    await first.close()
    assert get_job_manager() is manager, "one Runtime close must not kill shared jobs"
    assert manager.snapshot(job.id) is not None

    await second.close()
    assert get_job_manager() is not manager, "last close disposes the manager"


# ---------------------------------------------------------------------------
# v0.4.1: oversized tool results spill to the *current* session's artifacts
# ---------------------------------------------------------------------------


async def test_bootstrap_wires_an_artifact_sink(runtime_factory):
    runtime = await runtime_factory()

    assert callable(runtime.agent.artifact_sink)
    path = runtime.agent.artifact_sink("bash", "payload")
    assert path is not None
    assert (
        path.parent
        == runtime.session.path.parent / "artifacts" / runtime.session.path.stem
    )
    assert path.read_text(encoding="utf-8") == "payload"


async def test_artifact_sink_follows_the_session_switch(runtime_factory):
    from zenagent.agent.session import unique_session_path

    runtime = await runtime_factory()
    first = runtime.agent.artifact_sink("bash", "from the first session")
    assert first is not None
    assert first.read_text(encoding="utf-8") == "from the first session"

    new_path = unique_session_path()
    await runtime.switch_session(new_path)
    second = runtime.agent.artifact_sink("bash", "from the new session")

    assert second is not None
    assert second.parent == new_path.parent / "artifacts" / new_path.stem
    assert second.read_text(encoding="utf-8") == "from the new session"


async def test_assigning_the_session_directly_retargets_the_sink(runtime_factory):
    """The TUI retry/compaction paths assign ``runtime.session`` directly."""
    from zenagent.agent.session import Session, unique_session_path

    runtime = await runtime_factory()
    new_path = unique_session_path()
    runtime.session = Session(new_path)

    path = runtime.agent.artifact_sink("bash", "payload")
    assert path is not None
    assert path.parent == new_path.parent / "artifacts" / new_path.stem


async def test_runtime_exposes_a_read_only_task_tool(runtime_factory):
    runtime = await runtime_factory()
    tools = {tool.name: tool for tool in runtime._base_tools}
    assert "task" in tools and "todo" in tools
    task = tools["task"]
    assert task.approval == "read"
    assert task.execution_mode == "sequential"
    # Outside zen the child tool set is the parent's read tools minus the
    # denylist; time travel and session state never reach the child.
    from zenagent.agent.subagents_runtime import child_tool_names

    child = child_tool_names(list(runtime._base_tools))
    assert child <= {name for name, tool in tools.items() if tool.approval == "read"}
    assert not child & {"task", "ask", "wait", "todo", "rewind"}
    assert "write_file" not in child and "bash" not in child
    assert "memory" not in child and "checkpoint" not in child
    # In zen the child mirrors the parent's active set (minus the denylist).
    zen_child = child_tool_names(list(runtime._base_tools), zen=True)
    assert "write_file" in zen_child and "bash" in zen_child
    assert not zen_child & {"task", "ask", "wait", "todo", "rewind"}
    assert "memory" not in zen_child and "checkpoint" not in zen_child


async def test_switching_sessions_rebinds_the_task_tool(runtime_factory):
    runtime = await runtime_factory()
    task_tool = runtime.task_tool
    assert task_tool is not None
    bound: list = []
    original = task_tool.bind_session
    task_tool.bind_session = lambda path: (bound.append(path), original(path))[1]

    new_path = runtime.session.path.parent / "rebound.jsonl"
    await runtime.switch_session(new_path)

    assert bound and bound[-1] == new_path


async def test_bootstrap_creates_a_write_journal(runtime_factory):
    runtime = await runtime_factory()
    assert runtime.journal is not None
    assert runtime.agent.after_tool_call is not None


async def test_bootstrap_respects_disabled_backups(monkeypatch, app_env):
    from zenagent.config import UserConfig

    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(default_model="deepseek-flash", backup_enabled=False),
    )
    runtime = await Runtime.bootstrap(_options(), stream_fn=None)
    try:
        assert runtime.journal is None
    finally:
        await runtime.close()


async def test_bootstrap_carries_guard_policy_options(runtime_factory, tmp_path):
    runtime = await runtime_factory(
        allow_outside=[str(tmp_path / "outside")],
        allow_destructive=True,
        allow_protected=True,
    )
    assert runtime.gate is not None
    allow_outside = [str(path) for path in runtime.gate.policy.allow_outside]
    assert str(tmp_path / "outside") in allow_outside
    assert runtime.gate.policy.allow_destructive
    assert runtime.gate.policy.allow_protected


def _echo_stream():
    import time

    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[TextContent(text="ok")],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_runtime_writes_trace_and_metrics(runtime_factory, tmp_path):
    trace = tmp_path / "trace.jsonl"
    metrics = tmp_path / "metrics.jsonl"
    runtime = await runtime_factory(
        stream_fn=_echo_stream(), trace=trace, metrics=metrics
    )

    await runtime.agent.prompt("hi")

    assert trace.read_text(encoding="utf-8").strip()
    assert metrics.read_text(encoding="utf-8").strip()
    assert runtime.trace_path == str(trace)


async def test_runtime_auto_compact_is_configurable(runtime_factory):
    runtime = await runtime_factory(auto_compact=False)
    assert runtime.auto_compact is False


async def test_bootstrap_accepts_an_api_key_override(app_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    options = RuntimeOptions(
        mcp=False, skills=False, templates=False, api_key="override-key"
    )

    runtime = await Runtime.bootstrap(options)

    try:
        assert runtime.agent.api_key == "override-key"
    finally:
        await runtime.close()


async def test_custom_tools_override_the_composed_tool_set(runtime_factory):
    from zenagent.tools.fs import READ_FILE

    runtime = await runtime_factory(tools=[READ_FILE])

    assert [tool.name for tool in runtime.agent.tools] == ["read_file"]


async def test_mcp_manager_receives_the_retry_interval(runtime_factory, monkeypatch):
    captured: dict = {}

    class FakeManager:
        def __init__(self, configs, retry_interval_s=10.0):
            captured["interval"] = retry_interval_s

        async def start(self):
            return []

        def tool_defs(self):
            return []

        async def close(self):
            return None

    monkeypatch.setattr("zenagent.app.plugin_wiring.McpManager", FakeManager)
    monkeypatch.setattr(
        "zenagent.app.plugin_wiring.load_mcp_config", lambda *a, **k: {"srv": object()}
    )

    await runtime_factory(mcp=True, mcp_retry_interval_s=0.25)

    assert captured["interval"] == 0.25


async def test_bootstrap_wires_the_task_budget_from_config(
    runtime_factory, app_env, monkeypatch
):
    """Runtime-based frontends (TUI, REPL, RPC) all get ``[task]`` for free."""
    from zenagent.agent.subagents import SubagentLimits
    from zenagent.app.runtime import build_task_tool as real_build_task_tool
    from zenagent.config import UserConfig

    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return real_build_task_tool(**kwargs)

    monkeypatch.setattr(
        "zenagent.app.runtime.load_user_config",
        lambda *args, **kwargs: UserConfig(
            default_model="deepseek-flash",
            task_max_turns=5,
            task_timeout_s=120,
            task_max_cost=1.5,
            task_max_transcript_bytes=4096,
            task_max_transcript_file_bytes=2048,
        ),
    )
    monkeypatch.setattr("zenagent.app.runtime.build_task_tool", capture)

    await runtime_factory()

    assert captured["max_turns"] == 5
    assert captured["timeout_s"] == 120
    assert captured["max_cost"] == 1.5  # [task].max_cost wins over the session cap
    assert callable(captured["live_tools"]) and callable(captured["mode"])
    assert callable(captured["resolve_model"])
    assert captured["limits"] == SubagentLimits(
        keep_transcripts=64, max_total_bytes=4096, max_file_bytes=2048
    )


async def test_bootstrap_hands_the_permission_gate_to_the_task_tool(
    runtime_factory, app_env, monkeypatch
):
    """A child agent consults the same gate as its parent (no policy bypass)."""
    from zenagent.app.permissions import PermissionBroker
    from zenagent.app.runtime import Runtime, RuntimeOptions
    from zenagent.app.runtime import build_task_tool as real_build_task_tool

    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return real_build_task_tool(**kwargs)

    monkeypatch.setattr("zenagent.app.runtime.build_task_tool", capture)

    runtime = await runtime_factory()
    assert captured["before_tool_call"] == runtime.gate.before_tool_call

    broker = PermissionBroker.wrap(runtime.gate)
    brokered = await Runtime.bootstrap(
        RuntimeOptions(mcp=False, skills=False, templates=False), gate=broker
    )
    try:
        assert captured["before_tool_call"] == broker.before_tool_call
        assert brokered.gate is broker
    finally:
        await brokered.close()


async def test_runtime_writes_tool_end_without_a_chat_controller(runtime_factory):
    """``zen -p`` never builds a ChatController: the runtime owns the record."""
    from conftest import tool_then_text_stream

    runtime = await runtime_factory(stream_fn=tool_then_text_stream())

    await runtime.agent.prompt("read the readme")

    session = runtime.session
    session.ensure_loaded()
    tool_ends = [record for record in session.records if record.get("type") == "tool_end"]
    assert [record["call_id"] for record in tool_ends] == ["c1"]
    assert tool_ends[0]["tool_name"] == "read_file"
    assert isinstance(tool_ends[0].get("duration_ms"), int)


async def test_bootstrap_honours_read_only_even_in_zen_mode(runtime_factory):
    """``-p --mode zen --read-only`` must still refuse writes."""
    runtime = await runtime_factory(mode="zen", read_only=True)

    assert runtime.gate.read_only is True
    verdict = await runtime.gate.before_tool_call(
        "write_file", {"path": "blocked.txt", "content": "x"}
    )
    assert verdict is not None and verdict.get("block") is True
async def test_a_launch_with_no_conversation_never_creates_a_session_file(runtime_factory):
    """A session exists in memory from bootstrap; only a real message gives it
    a file, so an idle launch (even one that is killed) leaves nothing to list."""
    runtime = await runtime_factory()
    path = runtime.session.path
    assert not path.exists(), "bootstrap must not materialize the session"

    await runtime.close()

    assert not path.exists()


async def test_the_first_message_materializes_the_session(runtime_factory):
    from zenagent.types import UserMessage

    runtime = await runtime_factory()
    path = runtime.session.path
    assert not path.exists()

    await runtime.session.append(UserMessage(content="hello", timestamp=1))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert '"type": "session"' in lines[0]
    assert '"type": "mode"' in lines[1]
    assert '"role": "user"' in lines[2]


async def test_a_session_with_a_message_is_kept(runtime_factory):
    from zenagent.types import UserMessage

    runtime = await runtime_factory()
    await runtime.session.append(UserMessage(content="hello", timestamp=1))
    path = runtime.session.path

    await runtime.close()

    assert path.is_file()


async def test_a_resumed_empty_session_is_not_deleted(runtime_factory):
    """Files that existed before this process are never ours to remove."""
    from zenagent.agent.session import Session, unique_session_path

    path = unique_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    runtime = await runtime_factory(resume=path)
    await runtime.close()

    assert path.is_file()
    assert Session(path).messages == []


async def test_deleting_the_current_session_starts_a_droppable_one(runtime_factory):
    """/delete attaches a fresh session; quitting right after must not add a file."""
    from zenagent.app.sessions import SessionManager

    runtime = await runtime_factory()
    manager = SessionManager(runtime)
    doomed = runtime.session.path
    manager.delete(doomed)

    fresh = runtime.session.path
    await runtime.close()

    assert not fresh.exists()


async def test_an_abandoned_empty_session_is_dropped_too(runtime_factory):
    """Starting fresh, then resuming another session, must not orphan the first."""
    from zenagent.agent.session import Session, unique_session_path
    from zenagent.types import UserMessage

    other = unique_session_path()
    await Session(other).append(UserMessage(content="kept", timestamp=1))

    runtime = await runtime_factory()
    abandoned = runtime.session.path
    await runtime.switch_session(other)

    await runtime.close()

    assert not abandoned.exists(), "the launch's own empty file goes away"
    assert other.is_file(), "a session with a message is never touched"
