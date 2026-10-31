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
from zenagent.cli.tui.widgets.prompt_input import PromptInput
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


def _plain(widget) -> str:
    content = widget.content
    return content.plain if hasattr(content, "plain") else str(content)


async def _todos_ready(app, pilot):
    app.chat._on_event(todo_event(two_tasks()))
    app._refresh_status()
    await pilot.pause()


async def test_hud_click_opens_the_read_only_todo_overlay(fake_runtime):
    from zenagent.cli.tui.screens.todo_list import TodoListScreen

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _todos_ready(app, pilot)

        await pilot.click(TodoHud)
        await pilot.pause()

        assert isinstance(app.screen, TodoListScreen)
        items = [_plain(widget) for widget in app.screen.query(".todo-item")]
        assert any("read a" in item for item in items)
        assert any("read b" in item for item in items)
        assert any("in progress" in item for item in items)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TodoListScreen)
        assert app.focused is app.query_one(PromptInput)


async def test_sidebar_todo_row_opens_the_same_overlay(fake_runtime):
    from zenagent.cli.tui.screens.todo_list import TodoListScreen
    from zenagent.cli.tui.widgets.sidebar import SidebarRow

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _todos_ready(app, pilot)
        await pilot.press("ctrl+b")
        await pilot.pause()

        row = next(
            row for row in app.query(SidebarRow) if row.key == "task:0:0"
        )
        await pilot.click(row)
        await pilot.pause()

        assert isinstance(app.screen, TodoListScreen)


async def test_overlay_shows_the_full_item_text_not_the_clipped_hud(fake_runtime):
    long = "refactor the sidebar so clicking a subagent opens its transcript view"
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.chat._on_event(
            todo_event(
                [
                    {
                        "name": "Explore",
                        "tasks": [{"content": long, "status": "pending"}],
                    }
                ]
            )
        )
        app._refresh_status()
        await pilot.pause()
        app.action_show_todo_list()
        await pilot.pause()

        items = [_plain(widget) for widget in app.screen.query(".todo-item")]
        assert any(long in item for item in items)


async def test_palette_lists_the_todo_overlay_command(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        names = [command.title for command in app.get_system_commands(app.screen)]

        assert "Show todo list" in names
