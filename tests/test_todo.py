"""Session todo tool: ops, content addressing, atomicity, sidecar, wiring.

The tool is driven directly (``execute(tool_call_id, args, abort)``) like the
other built-in tools; the TUI/controller wiring lives at the bottom so one file
covers tool semantics and the small app-layer surface it needs.
"""

import json

from zenagent.agent.loop import ToolResultAgent
from zenagent.app.chat import ChatController
from zenagent.app.permissions import PermissionGate, tool_is_read_only
from zenagent.events import AgentEvent
from zenagent.tools import ALL_TOOLS
from zenagent.tools.todo import (
    build_todo_tool,
    load_todo_phases,
    summarize_todo,
    todo_sidecar_path,
)
from zenagent.types import TextContent


async def call(tool, **args):
    return await tool.execute("call_1", args, None)


def contents(result):
    """Every task content in the result's canonical phase snapshot."""
    return [
        task["content"]
        for phase in result.details["phases"]
        for task in phase["tasks"]
    ]


def status_of(result, content):
    for phase in result.details["phases"]:
        for task in phase["tasks"]:
            if task["content"] == content:
                return task["status"]
    raise AssertionError(f"{content!r} not in {result.details['phases']}")


def task_of(result, content):
    for phase in result.details["phases"]:
        for task in phase["tasks"]:
            if task["content"] == content:
                return task
    raise AssertionError(f"{content!r} not in {result.details['phases']}")


def init_list(*phases):
    """``init_list(("Explore", ["a", "b"]), ...)`` -> init ``list`` argument."""
    return [{"phase": name, "items": list(items)} for name, items in phases]


def todo_event(phases, *, op="init", text="todo"):
    return AgentEvent(
        type="tool_execution_end",
        tool_call_id="t1",
        tool_name="todo",
        result=ToolResultAgent(
            content=[TextContent(text=text)],
            details={"op": op, "phases": phases},
        ),
    )


def write_sidecar(session_path, phases):
    sidecar = todo_sidecar_path(session_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"phases": phases}), encoding="utf-8")


# ---------------------------------------------------------------------------
# declaration
# ---------------------------------------------------------------------------


async def test_todo_is_a_read_tier_sequential_tool():
    tool = build_todo_tool(None)

    assert tool.name == "todo"
    assert tool.approval == "read"
    assert tool.execution_mode == "sequential"
    assert tool.parameters["required"] == ["op"]
    assert "exact content" in tool.description.lower()
    assert set(tool.parameters["properties"]["op"]["enum"]) == {
        "init",
        "start",
        "done",
        "rm",
        "drop",
        "block",
        "unblock",
        "append",
        "view",
    }


async def test_todo_is_classified_read_only_by_the_permission_gate():
    tool = build_todo_tool(None)
    assert tool_is_read_only("todo", tool) is True

    # Read tier: allowed even in plan mode without a confirmation.
    gate = PermissionGate(interactive=False, mode="plan")
    allowed, reason = await gate.check("todo", {"op": "view"}, tool)
    assert allowed is True and reason == ""


# ---------------------------------------------------------------------------
# init + the in-progress pointer
# ---------------------------------------------------------------------------


async def test_init_creates_phases_and_advances_the_pointer():
    tool = build_todo_tool(None)
    result = await call(
        tool,
        op="init",
        list=init_list(("Explore", ["read a", "read b"]), ("Build", ["write c"])),
    )

    assert not result.is_error
    assert [phase["name"] for phase in result.details["phases"]] == ["Explore", "Build"]
    assert status_of(result, "read a") == "in_progress"
    assert status_of(result, "read b") == "pending"
    assert status_of(result, "write c") == "pending"

    text = result.content[0].text
    assert "0/3 closed" in text
    assert "3 remaining" in text
    assert "phase: Explore" in text


async def test_init_accepts_a_flat_items_list_under_a_default_phase():
    tool = build_todo_tool(None)
    result = await call(tool, op="init", items=["a", "b"])

    assert not result.is_error
    assert [phase["name"] for phase in result.details["phases"]] == ["Tasks"]
    assert contents(result) == ["a", "b"]
    assert status_of(result, "a") == "in_progress"


async def test_init_replaces_a_previous_list():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["old"])
    result = await call(tool, op="init", items=["new"])

    assert contents(result) == ["new"]
    assert status_of(result, "new") == "in_progress"


async def test_init_rejects_duplicate_task_content_atomically():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a", "b"])

    failed = await call(
        tool,
        op="init",
        list=init_list(("X", ["same"]), ("Y", ["same"])),
    )

    assert failed.is_error
    assert "Duplicate task" in failed.content[0].text
    assert contents(failed) == ["a", "b"], "a failed init must not replace the list"


async def test_start_demotes_the_previous_task():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a", "b"])

    result = await call(tool, op="start", task="b")

    assert not result.is_error
    assert status_of(result, "a") == "pending"
    assert status_of(result, "b") == "in_progress"


# ---------------------------------------------------------------------------
# done / drop / rm
# ---------------------------------------------------------------------------


async def test_done_advances_to_the_next_pending_task():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a", "b"])

    result = await call(tool, op="done", task="a")

    assert status_of(result, "a") == "completed"
    assert status_of(result, "b") == "in_progress"
    text = result.content[0].text
    assert "1/2 closed" in text
    assert "working on: b" in text


async def test_done_with_a_phase_completes_it_and_advances():
    tool = build_todo_tool(None)
    await call(
        tool,
        op="init",
        list=init_list(("Explore", ["read a", "read b"]), ("Build", ["write c"])),
    )

    result = await call(tool, op="done", phase="Explore")

    assert status_of(result, "read a") == "completed"
    assert status_of(result, "read b") == "completed"
    assert status_of(result, "write c") == "in_progress"


async def test_drop_marks_a_task_abandoned_and_counts_it_closed():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a", "b"])

    result = await call(tool, op="drop", task="a")

    assert status_of(result, "a") == "abandoned"
    assert status_of(result, "b") == "in_progress"
    assert "1/2 closed" in result.content[0].text


async def test_rm_removes_a_task():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a", "b"])

    result = await call(tool, op="rm", task="a")

    assert contents(result) == ["b"]
    assert status_of(result, "b") == "in_progress"


async def test_rm_clears_a_phase():
    tool = build_todo_tool(None)
    await call(tool, op="init", list=init_list(("Explore", ["a"]), ("Build", ["b"])))

    result = await call(tool, op="rm", phase="Build")

    phases = {phase["name"]: phase["tasks"] for phase in result.details["phases"]}
    assert phases == {"Explore": [{"content": "a", "status": "in_progress"}], "Build": []}


async def test_rm_without_a_target_clears_every_task():
    tool = build_todo_tool(None)
    await call(tool, op="init", list=init_list(("Explore", ["a"]), ("Build", ["b"])))

    result = await call(tool, op="rm")

    assert contents(result) == []
    assert "cleared" in result.content[0].text.lower()


# ---------------------------------------------------------------------------
# content addressing
# ---------------------------------------------------------------------------


async def test_unknown_task_is_an_error_that_lists_available_tasks():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["write parser", "run tests"])

    result = await call(tool, op="done", task="write parzer")

    assert result.is_error
    text = result.content[0].text
    assert "not found" in text.lower()
    assert "write parser" in text and "run tests" in text
    # The failed call changes nothing: the pointer is still on the first task.
    assert status_of(result, "write parser") == "in_progress"
    assert status_of(result, "run tests") == "pending"


async def test_generated_ids_are_rejected_with_the_content_hint():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a"])

    result = await call(tool, op="start", task="task-1")

    assert result.is_error
    text = result.content[0].text.lower()
    assert "task-1" in text
    assert "content" in text and "id" in text


async def test_unknown_phase_is_an_error_that_lists_available_phases():
    tool = build_todo_tool(None)
    await call(tool, op="init", list=init_list(("Explore", ["a"]), ("Build", ["b"])))

    result = await call(tool, op="done", phase="Depoloy")

    assert result.is_error
    text = result.content[0].text
    assert "Depoloy" in text and "Explore" in text and "Build" in text


# ---------------------------------------------------------------------------
# append + block/unblock
# ---------------------------------------------------------------------------


async def test_append_adds_a_phase_and_tasks():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a"])

    result = await call(tool, op="append", phase="Verify", items=["b", "c"])

    assert not result.is_error
    assert contents(result) == ["a", "b", "c"]
    assert [phase["name"] for phase in result.details["phases"]] == ["Tasks", "Verify"]


async def test_append_is_atomic_when_one_item_already_exists():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a"])

    result = await call(tool, op="append", phase="More", items=["b", "a"])

    assert result.is_error
    assert "already exists" in result.content[0].text
    assert contents(result) == ["a"], "neither the task nor the new phase may be added"


async def test_block_records_a_one_line_reason_and_unblock_restores_pending():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a", "b"])

    blocked = await call(tool, op="block", task="a", reason="waiting on\nupstream fix")
    assert status_of(blocked, "a") == "blocked"
    assert task_of(blocked, "a")["blocker"] == "waiting on upstream fix"
    assert backend_pointer(blocked) == "b"

    unblocked = await call(tool, op="unblock", task="a")
    assert status_of(unblocked, "a") == "pending"
    assert "blocker" not in task_of(unblocked, "a")


def backend_pointer(result):
    """Content the tool considers the current (in-progress) task."""
    for phase in result.details["phases"]:
        for task in phase["tasks"]:
            if task["status"] == "in_progress":
                return task["content"]
    return None


async def test_block_requires_a_target():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a"])

    result = await call(tool, op="block")

    assert result.is_error
    assert "requires" in result.content[0].text
    assert status_of(result, "a") == "in_progress"


async def test_block_skips_closed_tasks():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a"])
    await call(tool, op="done", task="a")

    result = await call(tool, op="block", task="a", reason="too late")

    assert not result.is_error
    assert status_of(result, "a") == "completed"


# ---------------------------------------------------------------------------
# view / validation / summary
# ---------------------------------------------------------------------------


async def test_view_is_read_only_and_does_not_rewrite_the_sidecar(tmp_path):
    session = tmp_path / "20260101-120000.jsonl"
    tool = build_todo_tool(session)
    await call(tool, op="init", items=["a", "b"])

    sidecar = todo_sidecar_path(session)
    before = sidecar.read_text(encoding="utf-8")
    result = await call(tool, op="view")

    assert not result.is_error
    assert result.details["op"] == "view"
    assert sidecar.read_text(encoding="utf-8") == before
    text = result.content[0].text
    assert "[/]" in text and "[ ]" in text, "view renders the checklist markers"
    assert "a" in text and "b" in text


async def test_view_of_an_empty_list_says_so():
    result = await call(build_todo_tool(None), op="view")

    assert not result.is_error
    assert "empty" in result.content[0].text.lower()


async def test_unknown_op_is_an_error_and_leaves_state_untouched():
    tool = build_todo_tool(None)
    await call(tool, op="init", items=["a"])

    result = await call(tool, op="nonsense")

    assert result.is_error
    assert "unknown op" in result.content[0].text.lower()
    assert contents(result) == ["a"]


async def test_summarize_todo_counts_and_current_phase():
    phases = [
        {
            "name": "Explore",
            "tasks": [
                {"content": "a", "status": "completed"},
                {"content": "b", "status": "in_progress"},
            ],
        },
        {"name": "Build", "tasks": [{"content": "c", "status": "blocked", "blocker": "x"}]},
    ]

    info = summarize_todo(phases)

    assert info["total"] == 3
    assert info["closed"] == 1
    assert info["remaining"] == 2
    assert info["blocked"] == 1
    assert info["phase"] == "Explore"
    assert info["current"] == "b"


# ---------------------------------------------------------------------------
# persistence: <session>.todo.json
# ---------------------------------------------------------------------------


async def test_sidecar_is_written_next_to_the_session_and_round_trips(tmp_path):
    session = tmp_path / "20260101-120000.jsonl"
    tool = build_todo_tool(session)
    await call(tool, op="init", items=["a", "b"])
    await call(tool, op="done", task="a")

    sidecar = todo_sidecar_path(session)
    assert sidecar.name == "20260101-120000.jsonl.todo.json"
    assert sidecar.parent == session.parent
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert [task["status"] for task in payload["phases"][0]["tasks"]] == [
        "completed",
        "in_progress",
    ]
    assert not list(tmp_path.glob("*.tmp")), "atomic replace must leave no temp file"

    # A fresh instance for the same session resumes the same list.
    resumed = build_todo_tool(session)
    viewed = await call(resumed, op="view")
    assert viewed.details["storage"] == "session"
    assert contents(viewed) == ["a", "b"]
    assert status_of(viewed, "a") == "completed"
    assert load_todo_phases(session) == viewed.details["phases"]


async def test_missing_or_corrupt_sidecar_loads_empty(tmp_path):
    session = tmp_path / "s.jsonl"
    assert load_todo_phases(session) == []

    todo_sidecar_path(session).write_text("{not json", encoding="utf-8")
    assert load_todo_phases(session) == []
    tool = build_todo_tool(session)
    viewed = await call(tool, op="view")
    assert viewed.details["phases"] == []


async def test_a_failed_call_does_not_rewrite_the_sidecar(tmp_path):
    session = tmp_path / "s.jsonl"
    tool = build_todo_tool(session)
    await call(tool, op="init", items=["a"])
    sidecar = todo_sidecar_path(session)
    before = sidecar.read_text(encoding="utf-8")

    failed = await call(tool, op="done", task="ghost")

    assert failed.is_error
    assert sidecar.read_text(encoding="utf-8") == before


async def test_memory_only_tool_keeps_state_without_a_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tool = build_todo_tool(None)

    result = await call(tool, op="init", items=["write tests"])

    assert not result.is_error
    assert result.details["storage"] == "memory"
    assert list(tmp_path.iterdir()) == []
    other = build_todo_tool(None)
    viewed = await call(other, op="view")
    assert contents(viewed) == [], "memory-only state is per tool instance"


# ---------------------------------------------------------------------------
# runtime + controller wiring
# ---------------------------------------------------------------------------


async def test_runtime_wires_the_todo_tool_outside_all_tools(runtime_factory):
    assert "todo" not in [tool.name for tool in ALL_TOOLS]

    runtime = await runtime_factory()
    names = [tool.name for tool in runtime.agent.tools]
    assert "todo" in names
    assert "todo" in [tool.name for tool in runtime._base_tools]
    assert "todo" in runtime.agent.system_prompt


async def test_runtime_binds_the_todo_tool_to_the_session_sidecar(runtime_factory):
    runtime = await runtime_factory()
    tool = next(tool for tool in runtime.agent.tools if tool.name == "todo")

    result = await tool.execute("c1", {"op": "init", "items": ["step one"]}, None)

    assert not result.is_error
    assert todo_sidecar_path(runtime.session.path).is_file()


async def test_switching_sessions_rebinds_the_todo_tool(runtime_factory):
    runtime = await runtime_factory()
    tool = next(tool for tool in runtime.agent.tools if tool.name == "todo")
    await tool.execute("c1", {"op": "init", "items": ["step one"]}, None)
    assert todo_sidecar_path(runtime.session.path).is_file()

    target = runtime.session.path.with_name("other-session.jsonl")
    target.write_text("", encoding="utf-8")
    other = [
        {"name": "Other", "tasks": [{"content": "x", "status": "in_progress"}]}
    ]
    write_sidecar(target, other)

    await runtime.switch_session(target)

    viewed = await tool.execute("c2", {"op": "view"}, None)
    assert viewed.details["phases"] == other, "the tool follows the new session"

    fresh = runtime.session.path.with_name("fresh-session.jsonl")
    fresh.write_text("", encoding="utf-8")
    await runtime.switch_session(fresh)
    empty = await tool.execute("c3", {"op": "view"}, None)
    assert empty.details["phases"] == []


async def test_chat_controller_exposes_todos_from_tool_results(runtime_factory):
    runtime = await runtime_factory()
    changes: list[int] = []
    chat = ChatController(runtime, on_change=lambda: changes.append(1))
    assert chat.todos == []

    phases = [
        {
            "name": "Explore",
            "tasks": [
                {"content": "read a", "status": "in_progress"},
                {"content": "read b", "status": "pending"},
            ],
        }
    ]
    chat._on_event(todo_event(phases))

    assert chat.todos == phases
    assert chat.status.todos == phases
    assert changes


async def test_chat_controller_loads_the_sidecar_for_the_current_session(runtime_factory):
    runtime = await runtime_factory()
    phases = [{"name": "Build", "tasks": [{"content": "write c", "status": "in_progress"}]}]
    write_sidecar(runtime.session.path, phases)

    chat = ChatController(runtime)

    assert chat.todos == phases


async def test_chat_controller_reloads_todos_after_a_session_switch(runtime_factory):
    runtime = await runtime_factory()
    chat = ChatController(runtime)
    phases = [{"name": "Build", "tasks": [{"content": "write c", "status": "in_progress"}]}]
    chat._on_event(todo_event(phases))
    assert chat.todos == phases

    await runtime.switch_session(None)
    chat.reload_transcript()

    assert chat.todos == []


# ---------------------------------------------------------------------------
# P1-9 tool policy prompt
# ---------------------------------------------------------------------------


async def test_system_prompt_has_the_tool_policy_sections():
    from zenagent.app.support import system_prompt

    prompt = system_prompt(["read_file", "list_dir", "glob", "grep", "bash", "todo"])

    assert "You are zenagent" in prompt
    assert "Available tools:" in prompt
    for section in ("Tool policy", "Specialized", "General", "Exploration", "Parallel"):
        assert section in prompt
    assert "bash only when no specialized tool fits" in prompt
    assert "3+" in prompt and "todo" in prompt
    assert "exact content" in prompt.lower()


async def test_system_prompt_mentions_todo_only_when_it_is_registered():
    from zenagent.app.support import system_prompt

    with_todo = system_prompt(["read_file", "bash", "todo"])
    without_todo = system_prompt(["read_file", "bash"])

    assert "Todo:" in with_todo
    assert "Todo:" not in without_todo
    assert "3+" not in without_todo


async def test_system_prompt_stays_compact():
    from zenagent.app.support import system_prompt

    prompt = system_prompt(
        ["read_file", "write_file", "edit_file", "list_dir", "bash", "glob", "grep", "todo"]
    )

    assert len(prompt.split()) < 220, "the tool policy section must stay short"


async def test_todo_runs_through_the_agent_loop(runtime_factory):
    """End-to-end: a model-issued todo call persists and reaches the controller."""
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, ToolCall, ToolResultMessage, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        if isinstance(messages[-1], ToolResultMessage):
            partial.content = [TextContent(text="done")]
            partial.stop_reason = "stop"
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta="done"
            )
            yield AssistantEvent(type="done", partial=partial, reason="stop")
            return
        call = ToolCall(
            id="c1",
            name="todo",
            arguments={
                "op": "init",
                "list": [{"phase": "Explore", "items": ["read a", "read b"]}],
            },
        )
        partial.content = [call]
        yield AssistantEvent(
            type="toolcall_start", partial=partial, content_index=0, tool_call=call
        )
        yield AssistantEvent(
            type="toolcall_end", partial=partial, content_index=0, tool_call=call
        )
        partial.stop_reason = "tool_use"
        yield AssistantEvent(type="done", partial=partial, reason="tool_use")

    runtime = await runtime_factory(stream_fn=stream)
    chat = ChatController(runtime)

    await chat.submit("go")
    await chat.wait_for_idle()

    assert [phase["name"] for phase in chat.todos] == ["Explore"]
    assert [task["status"] for task in chat.todos[0]["tasks"]] == [
        "in_progress",
        "pending",
    ]
    assert todo_sidecar_path(runtime.session.path).is_file()
    assert "todo" in runtime.agent.system_prompt


def test_duplicate_task_content_in_a_sidecar_is_deduped_on_load(tmp_path):
    from zenagent.tools.todo import (
        load_todo_phases,
        save_todo_phases,
    )

    session = tmp_path / "s.jsonl"
    save_todo_phases(
        session,
        [
            {
                "name": "work",
                "tasks": [
                    {"content": "dup", "status": "pending"},
                    {"content": "dup", "status": "pending"},
                ],
            }
        ],
    )
    phases = load_todo_phases(session)
    tasks = phases[0]["tasks"]
    assert [task["content"] for task in tasks] == ["dup"]


def test_clear_removes_tasks_and_the_sidecar(tmp_path):
    from zenagent.tools.todo import build_todo_tool, load_todo_phases

    session = tmp_path / "s.jsonl"
    tool = build_todo_tool(session)
    import asyncio

    asyncio.run(
        tool.execute("1", {"op": "init", "items": ["one"]}, None)
    )
    tool.clear()
    assert load_todo_phases(session) == []
