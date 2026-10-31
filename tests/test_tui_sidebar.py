"""Sidebar work panel: six sections, keyed rows and live work rows.

The sidebar is the "what is happening right now" surface: session/model/runtime
info, the todo summary (when the side panel owns it), live subagents and
background jobs. Rows are keyed so a 1-second refresh updates text in place
instead of rebuilding the widget tree.
"""

import time

from zenagent.agent.loop import ToolResultAgent
from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.widgets.sidebar import Sidebar, SidebarRow, SidebarSection
from zenagent.events import AgentEvent
from zenagent.types import TextContent


def section(app: ZenAgentApp, title: str) -> SidebarSection:
    for candidate in app.query_one(Sidebar).query(SidebarSection):
        if candidate.title == title:
            return candidate
    raise AssertionError(f"no {title} section")


def rows(app: ZenAgentApp, title: str) -> list[str]:
    return [row.plain for row in section(app, title).query(SidebarRow)]


def todo_event(phases: list[dict]) -> AgentEvent:
    return AgentEvent(
        type="tool_execution_end",
        tool_call_id="t1",
        tool_name="todo",
        result=ToolResultAgent(
            content=[TextContent(text="todo")],
            details={"op": "init", "phases": phases},
        ),
    )


def task_start(call_id: str, name: str) -> AgentEvent:
    return AgentEvent(
        type="tool_execution_start",
        tool_call_id=call_id,
        tool_name="task",
        args={"prompt": "go", "name": name},
    )


def task_end(call_id: str, name: str, *, status: str = "ok") -> AgentEvent:
    return AgentEvent(
        type="tool_execution_end",
        tool_call_id=call_id,
        tool_name="task",
        args={"prompt": "go", "name": name},
        result=ToolResultAgent(
            content=[TextContent(text="<task-result …>")],
            details={
                "status": status,
                "duration_ms": 18400,
                "cost": 0.0031,
                "tokens": 812,
            },
        ),
        is_error=status == "error",
        duration_ms=18400,
    )


async def open_sidebar(app: ZenAgentApp, pilot) -> None:
    await pilot.press("ctrl+b")
    await pilot.pause()


async def test_sidebar_mounts_the_six_sections_in_order(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await open_sidebar(app, pilot)

        titles = [item.title for item in app.query_one(Sidebar).query(SidebarSection)]

        assert titles == [
            "Session",
            "Model",
            "Runtime",
            "Todo",
            "Subagent",
            "Jobs",
        ]


async def test_session_model_and_runtime_sections_are_populated(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await open_sidebar(app, pilot)

        session_rows = rows(app, "Session")
        assert app.runtime.session.path.stem in " ".join(session_rows)
        assert any("message" in row for row in session_rows)

        model_rows = rows(app, "Model")
        assert any(row.startswith("ctx ") for row in model_rows)
        assert any("$" in row for row in model_rows)

        runtime_rows = rows(app, "Runtime")
        assert "idle" in " ".join(runtime_rows)


async def test_work_sections_are_hidden_while_empty(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await open_sidebar(app, pilot)

        assert section(app, "Todo").display is False
        assert section(app, "Subagent").display is False
        assert section(app, "Jobs").display is False


async def test_todo_section_tracks_the_hud_snapshot(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await open_sidebar(app, pilot)

        app.chat._on_event(
            todo_event(
                [
                    {
                        "name": "Explore",
                        "tasks": [
                            {"content": "read a", "status": "in_progress"},
                            {"content": "read b", "status": "pending"},
                        ],
                    }
                ]
            )
        )
        app._refresh_status()

        todo = section(app, "Todo")
        assert todo.display is True
        assert "0/2" in " ".join(rows(app, "Todo"))
        assert "Explore" in " ".join(rows(app, "Todo"))

        app.chat._on_event(todo_event([]))
        app._refresh_status()
        assert todo.display is False


async def test_subagent_rows_are_ordered_and_updated_in_place(fake_runtime):
    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await open_sidebar(app, pilot)

        app.chat._on_event(task_start("t2", "research"))
        app.chat._on_event(task_end("t2", "research"))
        app.chat._on_event(task_start("t3", "scan"))
        app._refresh_status()

        subagent = section(app, "Subagent")
        assert subagent.display is True
        keys = [row.key for row in subagent.query(SidebarRow)]
        assert keys == ["t3", "t2"]
        assert "scan" in rows(app, "Subagent")[0]
        assert "research" in rows(app, "Subagent")[1]

        first_row = subagent.query(SidebarRow)[0]
        app.chat._cards["t3"].started_at = time.monotonic() - 12
        app._refresh_status()

        assert subagent.query(SidebarRow)[0] is first_row
        assert "12s" in first_row.plain


async def test_jobs_section_renders_the_status_snapshot(fake_runtime, monkeypatch):
    class _Running:
        id = "bg_2"
        command = "pytest -q"
        status = "running"
        started_at = time.time() - 4
        duration_ms = None
        exit_code = None

    class _Finished:
        id = "bg_1"
        command = "ls"
        status = "ok"
        started_at = time.time() - 10
        duration_ms = 120
        exit_code = 0

    class _Manager:
        def list(self) -> list:
            return [_Finished(), _Running()]

    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: _Manager())

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await open_sidebar(app, pilot)
        app._refresh_status()

        jobs = section(app, "Jobs")
        assert jobs.display is True
        assert [row.key for row in jobs.query(SidebarRow)] == ["bg_2", "bg_1"]
        assert "bg_2" in rows(app, "Jobs")[0]
