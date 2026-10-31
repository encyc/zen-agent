"""Todo HUD: the one-row dock strip above the status band.

Data path under test: a ``todo`` tool result (``tool_execution_end`` with
``details["phases"]``) lands in ``ChatController.todos`` / ``RunStatus.todos``,
the app's normal refresh repaints :class:`TodoHud`, and the row hides itself
whenever there is no list. The HUD is mounted even when hidden so the dock
order stays stable.
"""

import json

from zenagent.agent.loop import ToolResultAgent
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.cli.tui.widgets.todo_hud import TodoHud
from zenagent.events import AgentEvent
from zenagent.tools.todo import todo_sidecar_path
from zenagent.types import TextContent


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


def two_tasks(**overrides):
    first = {"content": "read a", "status": "in_progress"}
    second = {"content": "read b", "status": "pending"}
    first.update(overrides)
    return [{"name": "Explore", "tasks": [first, second]}]


async def test_hud_is_mounted_and_hidden_without_todos(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        hud = app.query_one(TodoHud)
        assert hud.display is False
        assert hud.plain == ""


async def test_todo_result_renders_phase_counts_and_current_task(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        app.chat._on_event(todo_event(two_tasks()))
        await pilot.pause()
        await pilot.pause()

        hud = app.query_one(TodoHud)
        assert hud.display is True
        assert "Explore" in hud.plain
        assert "0/2 closed" in hud.plain
        assert "read a" in hud.plain, "the current task is the optional compact item"


async def test_hud_advances_after_a_done_result(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.chat._on_event(todo_event(two_tasks()))
        await pilot.pause()

        done = [
            {
                "name": "Explore",
                "tasks": [
                    {"content": "read a", "status": "completed"},
                    {"content": "read b", "status": "in_progress"},
                ],
            }
        ]
        app.chat._on_event(todo_event(done, op="done"))
        await pilot.pause()
        await pilot.pause()

        hud = app.query_one(TodoHud)
        assert "1/2 closed" in hud.plain
        assert "read b" in hud.plain


async def test_hud_shows_all_done_when_every_task_is_closed(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        closed = [
            {
                "name": "Explore",
                "tasks": [
                    {"content": "read a", "status": "completed"},
                    {"content": "read b", "status": "completed"},
                ],
            }
        ]
        app.chat._on_event(todo_event(closed, op="done"))
        await pilot.pause()
        await pilot.pause()

        hud = app.query_one(TodoHud)
        assert "2/2 closed" in hud.plain
        assert "all done" in hud.plain


async def test_hud_hides_again_when_the_list_is_cleared(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.chat._on_event(todo_event(two_tasks()))
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(TodoHud).display is True

        app.chat._on_event(todo_event([], op="rm"))
        await pilot.pause()
        await pilot.pause()

        hud = app.query_one(TodoHud)
        assert hud.display is False
        assert hud.plain == ""


async def test_hud_sits_directly_above_the_status_band(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.chat._on_event(todo_event(two_tasks()))
        await pilot.pause()
        await pilot.pause()

        hud = app.query_one(TodoHud)
        band = app.query_one(StatusBand)
        assert hud.display is True and hud.size.height == 1
        assert hud.region.y + hud.region.height == band.region.y


async def test_hud_loads_the_sidecar_on_start(runtime_factory):
    runtime = await runtime_factory()
    phases = [
        {
            "name": "Build",
            "tasks": [
                {"content": "write c", "status": "in_progress"},
                {"content": "run c", "status": "pending"},
            ],
        }
    ]
    sidecar = todo_sidecar_path(runtime.session.path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"phases": phases}), encoding="utf-8")

    app = ZenAgentApp(runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        hud = app.query_one(TodoHud)
        assert hud.display is True
        assert "Build" in hud.plain
        assert "0/2 closed" in hud.plain


async def test_status_snapshot_carries_the_phases(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        phases = two_tasks()
        app.chat._on_event(todo_event(phases))
        await pilot.pause()

        assert app.chat.todos == phases
        assert app.chat.status.todos == phases
