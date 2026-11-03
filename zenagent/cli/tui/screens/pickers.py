"""Settings/picker flows: orchestration glue over one modal each.

Each ``open_*(app)`` is the App method of the same name from before the split;
the App keeps a one-line delegate so key bindings, the command registry and the
tests keep addressing ``app.open_*``.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from zenagent.app.slash_args import argument_values
from zenagent.cli.tui.screens.modals import (
    ChoicePicker,
    HotkeysSheet,
    McpAdd,
    PlanReview,
    SessionPicker,
    SettingsPicker,
    TodoEditor,
    UsageDashboard,
)
from zenagent.i18n import t


def open_settings(app: App) -> None:
    async def selected(choice: str | None) -> None:
        if not choice:
            return
        if choice == "sidebar":
            app.action_toggle_sidebar()
        elif choice == "thinking":
            app.action_cycle_thinking()
        elif choice == "theme":
            app.open_theme_picker()
        elif choice == "language":
            app.open_language_picker()
        elif choice == "model-keys":
            app.open_model_keys()
        elif choice == "search-keys":
            app.open_search_keys()
        elif choice == "search-provider":
            app.open_search_provider()
        else:
            await app._dispatch_command(choice, "")

    status = app.chat.status
    app.open_menu(
        "settings",
        lambda: SettingsPicker(
            status.thinking,
            status.model_id,
            app._theme_spec,
            app._language_spec,
        ),
        selected,
    )


def open_hotkeys(app: App) -> None:
    """Read-only key-binding sheet (omp's /hotkeys overlay)."""
    from zenagent.cli.tui.widgets.prompt_input import APP_CHORDS

    lines = [f"{key:<16} {description}" for key, _action, description in app.BINDINGS]
    lines.extend(f"{key:<16} {action}" for key, action in APP_CHORDS.items())
    app.open_menu("hotkeys", lambda: HotkeysSheet(lines), lambda _result: None)


def open_usage_dashboard(app: App) -> None:
    """Current-session totals plus the recent-session usage report."""
    status = app.chat.status
    lines = [
        t("tui.usage.current"),
        (
            f"  in {status.input_tokens} · out {status.output_tokens} · "
            f"cache {status.cache_read} · ${status.cost:.4f}"
        ),
        f"  context {status.context_used}/{status.context_window}",
    ]
    if status.subagent_cost or status.subagent_tokens:
        lines.append(
            f"  subagents: ${status.subagent_cost:.4f} · "
            f"{status.subagent_tokens} tokens"
        )
    try:
        from zenagent.usage import collect_usage, render_report, session_paths

        report = collect_usage(session_paths()[-50:])
        lines.append("")
        lines.extend(render_report(report).splitlines())
    except Exception:  # noqa: BLE001 - the dashboard must never crash the app
        lines.append(t("tui.usage.unavailable"))
    app.open_menu("usage", lambda: UsageDashboard(lines), lambda _result: None)


def open_mcp_add(app: App) -> None:
    """Two-field wizard that appends a stdio server to mcp.json."""
    import shlex

    async def added(result) -> None:
        if not result:
            return
        from zenagent.mcp.config import save_server

        name, command_text = result
        try:
            command = shlex.split(command_text)
        except ValueError:
            command = [command_text]
        try:
            save_server(name, command)
        except ValueError as exc:
            app.notify(t("tui.app.mcp_add_failed", error=exc), timeout=3)
            return
        app.notify(t("tui.app.mcp_added", name=name))
        try:
            await app.runtime.refresh_mcp()
        except RuntimeError:
            pass
        app._refresh_status()

    app.open_menu("mcp-add", lambda: McpAdd(), added)


def open_todo_editor(app: App) -> None:
    """Editable todo list; writes the same sidecar the model's tool uses."""
    from zenagent.tools.todo import save_todo_phases

    async def edited(phases) -> None:
        if phases is None:
            return
        session = getattr(app.runtime, "session", None)
        if session is None:
            return
        try:
            save_todo_phases(Path(session.path), phases)
        except OSError as exc:
            app.notify(t("tui.app.todo_save_failed", error=exc), timeout=3)
            return
        app.chat.reload_todos()
        app._refresh_status()

    app.open_menu("todo-edit", lambda: TodoEditor(app.chat.todos), edited)


def open_plan_review(app: App) -> None:
    """Read the last plan, then approve (→ zen) or keep planning."""
    plan = app.chat.last_assistant_text()
    if not plan:
        app.notify(t("tui.app.no_plan"), timeout=3)
        return

    async def reviewed(choice: str | None) -> None:
        if choice != "approve":
            return
        app._mode_before_plan = None
        app.runtime.set_mode("zen")
        app.notify(t("tui.app.mode_set", mode="zen"))
        app._refresh_status()

    app.open_menu("plan-review", lambda: PlanReview(plan), reviewed)


def open_mode_picker(app: App) -> None:
    async def selected(mode: str | None) -> None:
        if mode:
            app.runtime.set_mode(mode)
            app.notify(t("tui.app.mode_set", mode=mode))
            app._refresh_status()

    app.open_menu(
        "mode",
        lambda: ChoicePicker(
            t("tui.app.mode_header"),
            argument_values("/mode"),
            app.runtime.mode,
        ),
        selected,
    )


def open_thinking_picker(app: App) -> None:
    agent = app.runtime.agent

    async def selected(level: str | None) -> None:
        if level:
            agent.thinking = None if level == "off" else level
            app.notify(t("tui.app.thinking_set", level=level))
            app._refresh_status()

    app.open_menu(
        "thinking",
        lambda: ChoicePicker(
            t("tui.app.thinking_header"),
            argument_values("/thinking"),
            agent.thinking or "off",
        ),
        selected,
    )


def open_session_picker(app: App, scope: str | None = None) -> None:
    app._session_scope = scope or app._session_scope
    current_scope = app._session_scope

    async def selected(action) -> None:
        if action is None:
            return
        if action.kind == "toggle-scope":
            other = "project" if current_scope == "all" else "all"
            app.call_later(app.open_session_picker, other)
            return
        # Never await a new modal from inside the dismiss callback: Textual
        # runs it in the message pump and the modal would deadlock.
        app.run_worker(app._apply_session_action(action), exclusive=False)

    app.open_menu(
        "sessions",
        lambda: SessionPicker(
            app.sessions.entries(scope=current_scope), scope=current_scope
        ),
        selected,
    )


def open_memory_manager(app: App) -> None:
    from zenagent.cli.tui.screens.memory_manager import MemoryManager

    store = app.runtime.memory_store
    if store is None:
        return
    app.open_menu(
        "memory-manager", lambda: MemoryManager(store), lambda _result: None
    )


def open_memory_editor(
    app: App, entry_id: str | None = None, *, title_prefill: str = ""
) -> None:
    from zenagent.cli.tui.screens.memory_manager import MemoryEditor

    store = app.runtime.memory_store
    if store is None:
        return
    entry = store.read_entry(entry_id) if entry_id else None

    def saved(result) -> None:
        if result is not None:
            app.notify(
                t("cmdmsg.memory_saved", id=result.id, title=result.title),
                timeout=3,
            )

    app.open_menu(
        "memory-editor",
        lambda: MemoryEditor(store, entry, title_prefill=title_prefill),
        saved,
    )
