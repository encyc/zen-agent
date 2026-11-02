"""Textual application wiring the app layer to widgets.

Layout follows omp's single-column dock (see
``docs/research/omp-cli-study/02-layout.md``):

    transcript (1fr)  →  status band (1 row)  →  prompt row

There is no bottom status bar: the band *is* the status line, and it sits above
the prompt. The sidebar is opt-in (ctrl+b) and hidden by default.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.events import MouseScrollDown, MouseScrollUp

from zenagent import update_check
from zenagent.app.appearance import APPEARANCES, resolve_theme
from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager
from zenagent.app.skill_commands import build_skill_submission, slash_token_at
from zenagent.app.views import RunStatus
from zenagent.cli.tui import palette
from zenagent.cli.tui.app_theme import textual_theme, theme_name
from zenagent.cli.tui.history import PromptHistory
from zenagent.cli.tui.widgets.attachments import AttachmentChips
from zenagent.cli.tui.widgets.command_hints import CommandHints
from zenagent.cli.tui.widgets.error_banner import ErrorBanner
from zenagent.cli.tui.widgets.new_content import NewContentBar
from zenagent.cli.tui.widgets.pending_band import PendingBand
from zenagent.cli.tui.widgets.prompt_gutter import PromptGutter
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import NARROW_WIDTH, Sidebar
from zenagent.cli.tui.widgets.sidebar_resize import SidebarResizeHandle
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.cli.tui.widgets.subagent_banner import SubagentBanner
from zenagent.cli.tui.widgets.todo_hud import TodoHud
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.cli.tui.widgets.work_indicator import WorkIndicator
from zenagent.cli.tui.widgets.work_strip import WorkStrip
from zenagent.i18n import LANGUAGE_CHOICES, resolve_language, set_locale, t
from zenagent.templates import expand

__all__ = ["DOUBLE_ESCAPE_SECONDS", "ZenAgentApp"]

#: Two empty-draft Escapes this close together mean "rewind the last turn"
#: (omp's Esc chain level 13); a slower second press just interrupts nothing.
DOUBLE_ESCAPE_SECONDS = 0.6


def _saved_theme() -> str | None:
    """The persisted ``theme`` preference, read lazily (tests patch settings)."""
    from zenagent import settings

    return settings.get_pref("theme")


def _saved_language() -> str | None:
    """The persisted ``language`` preference, read lazily (tests patch settings)."""
    from zenagent import settings

    return settings.get_pref("language")


class ZenAgentApp(App):
    """A single-column chat app; no mouse, no animations."""

    CSS_PATH = "theme.tcss"
    TITLE = "ZenAgent"
    # Textual's text selection is the one mouse path that crashed with Markdown
    # blocks (a Screen._forward_event bug); we never select text, so switch it
    # off and keep every other mouse event.
    ALLOW_SELECT = False
    # omp's chord set (``docs/research/omp-cli-study/01-interaction-hierarchy.md``):
    # Esc interrupts, ctrl+c clears the draft, ctrl+d exits, ctrl+q queues.
    BINDINGS = [  # noqa: RUF012 - Textual class API
        ("escape", "interrupt", "Interrupt"),
        ("ctrl+c", "clear_draft", "Clear draft"),
        ("ctrl+d", "quit_app", "Quit"),
        ("ctrl+q", "follow_up", "Follow-up"),
        ("ctrl+p", "cycle_model", "Next model"),
        ("shift+ctrl+p", "cycle_model_backward", "Previous model"),
        ("alt+p", "pick_model", "Pick model"),
        ("shift+tab", "cycle_thinking", "Thinking"),
        ("alt+shift+p", "toggle_plan", "Plan mode"),
        ("f5", "retry", "Retry"),
        ("alt+r", "retry", "Retry"),
        ("ctrl+t", "toggle_thinking", "Expand thinking"),
        ("ctrl+o", "toggle_tools", "Expand tools"),
        ("ctrl+shift+o", "toggle_tool_visibility", "Show/hide tools"),
        ("alt+l", "display_reset", "Reset display"),
        ("ctrl+k", "command_palette", "Commands"),
        ("ctrl+r", "history_search", "History"),
        ("ctrl+g", "edit_external", "Editor"),
        ("ctrl+v", "paste_clipboard", "Paste"),
        ("ctrl+b", "toggle_sidebar", "Info"),
        ("ctrl+s", "steer", "Steer"),
        ("ctrl+x", "clear_queue", "Clear queue"),
        ("alt+up", "dequeue", "Dequeue"),
    ]

    def __init__(
        self,
        runtime: Runtime,
        *,
        chat: ChatController | None = None,
        history_path: Path | None = None,
        theme: str | None = None,
        language: str | None = None,
    ) -> None:
        super().__init__()
        # Resolve and register the appearance before the first paint, so a
        # light terminal never flashes the dark palette on startup.
        appearance_warnings: list[str] = []
        self._appearance = resolve_theme(
            theme, pref=_saved_theme(), warnings=appearance_warnings
        )
        self._appearance_warnings = appearance_warnings
        #: The vocabulary value currently selected in the theme picker.
        self._theme_spec = (theme or _saved_theme() or "auto").strip().lower()
        if self._theme_spec not in ("auto", "dark", "light"):
            self._theme_spec = "auto"
        self.register_theme(textual_theme(APPEARANCES["dark"]))
        self.register_theme(textual_theme(APPEARANCES["light"]))
        self.theme = theme_name(self._appearance)
        palette.bind(self._appearance)
        # Interface language is resolved before any command/description exists,
        # so a fresh process never builds English chrome under a zh locale.
        language_warnings: list[str] = []
        self._language_spec = (language or _saved_language() or "auto").strip().lower()
        if self._language_spec not in LANGUAGE_CHOICES:
            self._language_spec = "auto"
        set_locale(
            resolve_language(
                language, pref=_saved_language(), warnings=language_warnings
            )
        )
        self._language_warnings = language_warnings
        self.runtime = runtime
        self.chat = chat or ChatController(runtime, on_change=self._on_change)
        self.history = PromptHistory(history_path)
        self.exit_summary: str | None = None
        self._menus: dict[str, object] = {}
        #: Serializes ask dialogs: one interactive question surface at a time.
        self._ask_lock = asyncio.Lock()
        self._refresh_pending = False
        self._update_notice_shown = False
        self._hint_start = 0
        self._hint_end = 0
        self._hint_prefix = "/"
        self._hint_kind = "slash"
        self._session_scope = "project"
        self._last_escape_at: float | None = None
        #: ctrl+b intent; the actual visibility also depends on the width.
        self._sidebar_open = False
        self._sidebar_visible = False
        #: Mode to return to when alt+shift+p leaves plan (None = ask).
        self._mode_before_plan: str | None = None
        #: Drag-resized sidebar width (process-local; 26 at every launch).
        self._sidebar_width = self.DEFAULT_SIDEBAR_WIDTH
        self._sidebar_resizing = False
        #: 1s poll while background jobs/subagents are live (else none).
        self._work_timer = None
        #: ``task`` call id whose read-only transcript is on screen.
        self._viewing: str | None = None
        #: The open job-log overlay, refreshed while the job is running.
        self._job_log_screen = None
        self.command_hints: CommandHints | None = None
        self.models = ModelManager(runtime)
        self.sessions = SessionManager(runtime)
        self.commands = build_default_commands(
            AppServices(
                runtime=runtime, chat=self.chat, models=self.models, sessions=self.sessions
            )
        )

    #: Seconds an ask-mode tool approval may stay open before it counts as Deny.
    approval_timeout_s: float = 120.0

    async def request_tool_approval(
        self, tool_name: str, arguments: dict, verdict=None
    ) -> str:
        """Show the ask-mode modal; returns ``allow`` / ``always`` / ``deny``.

        The agent tool round is awaited on this future, so the modal blocks that
        tool (not the UI). A timeout resolves to ``deny`` and closes the modal.
        """
        from zenagent.app.permissions import tool_target
        from zenagent.cli.tui.screens.modals import ToolApproval

        screen = ToolApproval(
            tool_name,
            arguments,
            timeout=self.approval_timeout_s,
            target=tool_target(tool_name, arguments),
            verdict=verdict,
        )
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def done(choice: str | None) -> None:
            if not future.done():
                future.set_result(choice or "deny")

        self.push_screen(screen, done)
        try:
            return await asyncio.wait_for(future, self.approval_timeout_s)
        except TimeoutError:
            if self.screen is screen:
                self.pop_screen()
            self.notify(t("tui.app.approval_timeout"), timeout=3)
            return "deny"

    async def request_ask(self, questions: list[dict]) -> list[dict]:
        """Handle one ``ask`` tool call: one framed dialog per question.

        Returns the answer list the tool expects (``{"id": "selected"}``); an
        empty list means the user dismissed the ask, and a shorter list means
        they closed a later dialog. The tool call is blocked on this coroutine,
        so the agent loop waits exactly like it does for an approval — the UI
        itself stays responsive.
        """
        answers: list[dict] = []
        # One interactive surface at a time: two parallel ask calls must not
        # stack dialogs (the tool keeps the default parallel execution mode).
        async with self._ask_lock:
            for index, question in enumerate(questions, start=1):
                selected = await self._ask_question(
                    question, index=index, total=len(questions)
                )
                if selected is None:
                    break
                answers.append({"id": question["id"], "selected": list(selected)})
        return answers

    async def _ask_question(
        self, question: dict, *, index: int, total: int
    ) -> list[str] | None:
        """Show one question; ``None`` means the user cancelled the ask."""
        from zenagent.cli.tui.screens.modals import AskQuestion

        screen = AskQuestion(question, index=index, total=total)
        future: asyncio.Future[list[str] | None] = (
            asyncio.get_running_loop().create_future()
        )

        def done(selection: list[str] | None) -> None:
            if not future.done():
                future.set_result(selection)

        self.push_screen(screen, done)
        return await future

    def action_quit_app(self) -> None:
        status = self.chat.status
        session = getattr(self.runtime.session, "path", None)
        self.exit_summary = t(
            "tui.app.exit_summary",
            path=session,
            input=status.input_tokens,
            output=status.output_tokens,
            cost=f"{status.cost:.4f}",
        )
        self.exit()

    async def on_event(self, event) -> None:
        await super().on_event(event)

    # -- pointer ------------------------------------------------------------
    #
    # Mouse is supported the way omp supports it: the wheel scrolls the
    # transcript, clicks activate the things that are clickable (tool cards,
    # the "new output" bar). Text selection stays off (ALLOW_SELECT), which is
    # what used to crash the Markdown path.

    WHEEL_ROWS = 3

    #: Sidebar width bounds; the main column never drops below the minimum.
    DEFAULT_SIDEBAR_WIDTH = 26
    MIN_SIDEBAR_WIDTH = 20
    MAX_SIDEBAR_WIDTH = 60
    MAIN_COLUMN_MIN = 30

    def _on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._scroll_chat(-self.WHEEL_ROWS)

    def _on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self._scroll_chat(self.WHEEL_ROWS)

    def _scroll_chat(self, rows: int) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_lines(rows)

    def compose(self):
        with Horizontal(id="root"):
            with Vertical(id="main-column"):
                with Vertical(id="transcript-stack"):
                    yield SubagentBanner()
                    yield TranscriptView()
                    yield TranscriptView(id="subagent-transcript", welcome=False)
                yield NewContentBar()
                with Vertical(id="dock"):
                    yield ErrorBanner()
                    yield CommandHints()
                    yield AttachmentChips()
                    yield TodoHud()
                    yield WorkStrip()
                    yield StatusBand()
                    yield PendingBand()
                    with Horizontal(id="prompt-row"):
                        yield PromptGutter()
                        yield PromptInput()
                        yield WorkIndicator()
            yield SidebarResizeHandle()
            yield Sidebar()

    def on_mount(self) -> None:
        self.command_hints = self.query_one(CommandHints)
        self.query_one(WorkStrip).set_handler(self._open_sidebar_from_strip)
        self.query_one(TodoHud).set_handler(self.action_show_todo_list)
        self.query_one(PendingBand).set_handler(self._promote_pending)
        self.query_one(Sidebar).set_handlers(
            on_todo=self.action_show_todo_list,
            on_subagent=self.enter_subagent_view,
            on_job=self.show_job_log,
            on_context=self.action_show_context_breakdown,
        )
        from zenagent.tools.task import set_subagent_observer

        set_subagent_observer(self.chat.on_subagent_event)
        self._announce_appearance_warnings()
        self._announce_language_warnings()
        self._announce_missing_key()
        # A resumed session already has messages: build the transcript views
        # before the first paint, or history (and its subagent rows) is blank.
        self.chat.reload_transcript()
        self._refresh_status()
        self.query_one(PromptInput).focus()
        self._start_update_check()
        # The ask tool is a static builtin: this mounted app is the interactive
        # surface that answers it. Headless frontends simply leave it unset.
        from zenagent.tools.ask import set_ask_handler

        set_ask_handler(self.request_ask)

    def on_unmount(self) -> None:
        """Drop the tool handlers so a later headless run degrades, not hangs."""
        from zenagent.tools.ask import set_ask_handler
        from zenagent.tools.task import set_subagent_observer

        set_ask_handler(None)
        set_subagent_observer(None)

    def _announce_language_warnings(self) -> None:
        """Report a rejected language pref without stopping the app."""
        warnings, self._language_warnings = self._language_warnings, []
        if not warnings:
            return
        transcript = self.query_one(TranscriptView)
        for warning in warnings:
            transcript.append_notice(f"language: {warning}", warning=True)

    def _announce_appearance_warnings(self) -> None:
        """Report a rejected theme pref without stopping the app."""
        warnings, self._appearance_warnings = self._appearance_warnings, []
        if not warnings:
            return
        transcript = self.query_one(TranscriptView)
        for warning in warnings:
            transcript.append_notice(f"theme: {warning}", warning=True)

    def _start_update_check(self) -> None:
        """Kick off the cached notice and background refresh; never break startup."""
        try:
            if update_check.is_disabled() or not update_check.installed_version():
                return
            self._maybe_show_cached_update()
            self.run_worker(
                self._refresh_update_check(), exclusive=False, exit_on_error=False
            )
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return

    def _maybe_show_cached_update(self) -> None:
        try:
            version = update_check.pending_notice()
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return
        if version:
            self._show_update_notice(version)

    def _show_update_notice(self, version: str) -> None:
        try:
            if self._update_notice_shown:
                return
            installed = update_check.installed_version()
            if not installed:
                return
            notice = update_check.format_notice(version, installed)
            # Parent transcript only: the subagent view is a read-only swap.
            self.query_one("#transcript", TranscriptView).append_notice(notice)
            update_check.mark_notified(version)
            self._update_notice_shown = True
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return

    async def _refresh_update_check(self) -> None:
        try:
            version = await update_check.refresh()
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return
        if version:
            self._show_update_notice(version)

    def _announce_missing_key(self) -> None:
        """A keyless first launch still opens; the banner says where to paste one."""
        env_name = self.runtime.missing_api_key
        if not env_name:
            return
        self.chat.set_last_error(
            t(
                "tui.app.no_api_key",
                model=self.runtime.model.id,
                env=env_name,
            )
        )

    def _on_change(self) -> None:
        # Streaming fires this per delta; coalesce bursts into one repaint.
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.call_later(self._run_refresh)

    def _run_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh_status()

    def _refresh_status(self) -> None:
        status = self.chat.status
        width = self._band_width()
        for band in self.query(StatusBand):
            band.update_status(status, width)
        # Pending messages sit between the status band and the composer; the
        # band hides itself while both lanes are empty.
        for pending_band in self.query(PendingBand):
            pending_band.set_pending(status.pending, width)
        # Todo state rides the same snapshot as everything else in the dock;
        # the row hides itself while the session has no list.
        for hud in self.query(TodoHud):
            hud.set_todos(status.todos)
        parent = self.query_one("#transcript", TranscriptView)
        parent.sync(self.chat.transcript, status)
        if self._viewing is not None:
            child_views = self.chat.subagent_transcript(self._viewing)
            if child_views is None:
                self._exit_subagent_view(restore_focus=False)
            else:
                self.query_one("#subagent-transcript", TranscriptView).sync(
                    child_views, status
                )
                # The view stays open after the child finishes; the banner is
                # what tells the user it is now a finished transcript.
                entry = next(
                    (
                        view
                        for view in status.subagents
                        if view.call_id == self._viewing
                    ),
                    None,
                )
                self.query_one(SubagentBanner).show(
                    entry.name if entry is not None else self._viewing,
                    status=entry.status if entry is not None else None,
                    transcript_truncated=(
                        entry.transcript_truncated if entry is not None else False
                    ),
                )
        for notice in self.chat.take_notices():
            if notice.kind == "divider":
                parent.append_divider(notice.text)
            else:
                parent.append_notice(notice.text)
        for indicator in self.query(WorkIndicator):
            indicator.set_running(status.running)
        for banner in self.query(ErrorBanner):
            banner.set_error(status.last_error)
        for chips in self.query(AttachmentChips):
            chips.set_attachments(self.chat.attachments)
        if self._job_log_screen is not None:
            view = self.chat.job_log(self._job_log_screen.job_id)
            if view is None:
                self._job_log_screen = None
            else:
                self._job_log_screen.set_view(view)
        for sidebar in self.query(Sidebar):
            sidebar.refresh_contents(
                self.chat, self.models, width=self._sidebar_width
            )
        self._sync_chrome(status)
        self._sync_work_timer(status)

    def action_show_todo_list(self) -> None:
        """Open the read-only checklist behind the one-line HUD summary."""
        from zenagent.cli.tui.screens.todo_list import TodoListScreen

        phases = self.chat.todos
        if not phases:
            self.notify(t("tui.app.no_todo"))
            return

        def restore_focus(_result=None) -> None:
            self.query_one(PromptInput).focus()

        self.push_screen(TodoListScreen(phases), restore_focus)

    # -- context -------------------------------------------------------------

    def action_show_context_breakdown(self) -> None:
        """Open the full context split (sidebar row click / palette caller)."""
        from zenagent.cli.tui.screens.context_breakdown import ContextBreakdownScreen

        report = self.chat.status.context
        if report is None:
            self.notify(t("tui.app.no_context"))
            return

        def restore_focus(_result=None) -> None:
            self.query_one(PromptInput).focus()

        self.push_screen(ContextBreakdownScreen(report), restore_focus)

    # -- job log -------------------------------------------------------------

    def show_job_log(self, job_id: str) -> None:
        """Open a job's bounded output; a retired job only gets a notice."""
        from zenagent.cli.tui.screens.job_log import JobLogScreen

        view = self.chat.job_log(job_id)
        if view is None:
            self.notify(t("tui.app.job_gone", id=job_id))
            return
        screen = JobLogScreen(view)
        self._job_log_screen = screen
        self.push_screen(screen, self._job_log_closed)

    def _job_log_closed(self, _result=None) -> None:
        self._job_log_screen = None
        self.query_one(PromptInput).focus()

    # -- subagent view -------------------------------------------------------

    def enter_subagent_view(self, call_id: str) -> None:
        """Swap the main area to the child transcript (read-only)."""
        views = self.chat.subagent_transcript(call_id)
        if views is None:
            self.notify(t("tui.app.no_subagent_transcript"))
            return
        name = next(
            (
                view.name
                for view in self.chat.status.subagents
                if view.call_id == call_id
            ),
            call_id,
        )
        entry = next(
            (view for view in self.chat.status.subagents if view.call_id == call_id),
            None,
        )
        self._viewing = call_id
        child = self.query_one("#subagent-transcript", TranscriptView)
        child.reset()
        child.sync(views, self.chat.status)
        child.display = True
        self.query_one("#transcript", TranscriptView).display = False
        self.query_one(SubagentBanner).show(
            name,
            status=entry.status if entry is not None else None,
            transcript_truncated=(
                entry.transcript_truncated if entry is not None else False
            ),
        )
        self.query_one(PromptInput).disabled = True
        child.focus()

    def _exit_subagent_view(self, *, restore_focus: bool = True) -> None:
        self._viewing = None
        self.query_one(SubagentBanner).hide()
        self.query_one("#subagent-transcript", TranscriptView).display = False
        self.query_one("#transcript", TranscriptView).display = True
        prompt = self.query_one(PromptInput)
        prompt.disabled = False
        if restore_focus:
            prompt.focus()

    # -- sidebar width -------------------------------------------------------

    @property
    def sidebar_width(self) -> int:
        """The current sidebar width (clamped to the bounds above)."""
        return self._sidebar_width

    def begin_sidebar_resize(self) -> None:
        """Mouse-down on the handle; the move handler owns the actual updates."""
        self._sidebar_resizing = True

    def end_sidebar_resize(self) -> None:
        self._sidebar_resizing = False

    def resize_sidebar(self, screen_x: int) -> None:
        """Anchor the sidebar's left edge at ``screen_x`` (the right edge is fixed)."""
        width = self.size.width - int(screen_x)
        room = self.size.width - self.MAIN_COLUMN_MIN
        self._sidebar_width = max(
            self.MIN_SIDEBAR_WIDTH,
            min(self.MAX_SIDEBAR_WIDTH, room, width),
        )
        self._apply_sidebar_width()
        # Dragging changes the dock column: re-degrade the status segments on
        # the next coalesced repaint instead of leaving the old layout on screen.
        self._on_change()

    def _band_width(self) -> int:
        """Width the dock actually has: the terminal minus the sidebar chrome.

        Segment degradation must use this, not ``size.width``: with the sidebar
        open the dock is narrower, and dragging changes it.
        """
        if self._sidebar_open and self.size.width >= NARROW_WIDTH:
            return max(20, self.size.width - self._sidebar_width - 1)
        return self.size.width

    def _apply_sidebar_width(self) -> None:
        for sidebar in self.query(Sidebar):
            sidebar.styles.width = self._sidebar_width

    # -- chrome -------------------------------------------------------------

    def _open_sidebar_from_strip(self) -> None:
        """Work strip click: reveal the sidebar the strip is standing in for."""
        self._sidebar_open = True
        self._sync_chrome()

    def _sync_work_timer(self, status: RunStatus) -> None:
        """Poll while background work is live; go quiet when it is not.

        Jobs have no change events (they finish outside any agent event), so a
        slow poll is what keeps the sidebar honest. Nothing live means no timer.
        """
        live = status.active_subagent is not None or any(
            job.status == "running" for job in status.jobs
        )
        if live and self._work_timer is None:
            self._work_timer = self.set_interval(1.0, self._on_change)
        elif not live and self._work_timer is not None:
            self._work_timer.stop()
            self._work_timer = None

    def _sync_chrome(self, status: RunStatus | None = None) -> None:
        """Decide sidebar / todo HUD / work strip visibility in one place.

        ``ctrl+b`` is an intent, not a display flag: the panel only appears when
        the terminal is wide enough, and the todo keeps exactly one visible home
        (the sidebar when it is shown, the dock HUD otherwise).
        """
        if status is None:
            status = self.chat.status
        visible = self._sidebar_open and self.size.width >= NARROW_WIDTH
        self._sidebar_visible = visible
        for sidebar in self.query(Sidebar):
            sidebar.display = visible
        for handle in self.query(SidebarResizeHandle):
            handle.display = visible
        self._apply_sidebar_width()
        for hud in self.query(TodoHud):
            hud.display = bool(status.todos) and not visible
        running = sum(1 for job in status.jobs if job.status == "running")
        task = None
        if status.active_subagent is not None:
            task = next(
                (
                    view.name
                    for view in status.subagents
                    if view.call_id == status.active_subagent
                ),
                "task",
            )
        for strip in self.query(WorkStrip):
            strip.set_work(running, task, sidebar_hidden=not visible)

    def dismiss_error(self) -> None:
        """Click on the error banner: drop it and repaint the dock."""
        self.chat.clear_last_error()
        self._refresh_status()

    def remove_attachment(self, index: int) -> None:
        """Click on a chip: drop that pending image."""
        self.chat.remove_attachment(index)
        self._refresh_status()

    def on_resize(self, event) -> None:
        self._refresh_status()

    def on_text_area_changed(self, event) -> None:
        """Keep the suggestion list in sync: commands, argument values, skills."""
        hints = self.command_hints
        if hints is None:
            return
        prompt = self.query_one(PromptInput)
        text = prompt.text
        cursor = prompt.document.get_index_from_location(prompt.cursor_location)
        matches, prefix, start, end, kind = self._hint_context(text, cursor)
        self._hint_prefix = prefix
        self._hint_start = start
        self._hint_end = end
        self._hint_kind = kind
        if matches:
            hints.show(matches, prefix=prefix)
        else:
            hints.hide()

    def _hint_context(
        self, text: str, cursor: int
    ) -> tuple[list[tuple[str, str]], str, int, int, str]:
        """``(matches, prefix, replace start, replace end, kind)`` for the draft.

        Line-start ``/`` lists commands and skill commands; an embedded ``/``
        token (mid-sentence) lists only ``/skill:<name>`` entries; after a known
        command's space we list that command's argument values.
        """
        if "\n" in text:
            return [], "/", 0, 0, "slash"
        token = slash_token_at(text, cursor)
        if token is not None:
            line_prefix = text[token.line_start : token.start].strip()
            if not line_prefix:
                if token.text.startswith("/skill:"):
                    matches = self._skill_matches(token.text)
                else:
                    matches = [*self._command_matches(token.text), *self._skill_matches(token.text)]
                    matches.sort(key=lambda item: item[0])
                return matches, "/", token.start, token.end, "slash"
            line = text[token.line_start :].lstrip()
            if line.startswith("/") and not line.startswith("/skill:"):
                return [], "/", 0, 0, "slash"
            return (
                self._skill_matches(token.text),
                "/",
                token.start,
                token.end,
                "slash",
            )

        if cursor == len(text) and text.startswith("/") and " " in text:
            head, _, rest = text.partition(" ")
            name = head[1:].lower()
            if " " not in rest and self.commands.get(name) is not None:
                needle = rest.strip().lower()
                matches = [
                    (value, description)
                    for value, description in self._argument_values(name)
                    if value.lower().startswith(needle)
                ]
                return matches, "", len(head) + 1, len(text), "arg"
        return [], "/", 0, 0, "slash"

    def _command_matches(self, token_text: str) -> list[tuple[str, str]]:
        needle = token_text[1:].lower()
        return [
            (
                command.name,
                f"{t('type.command')} · {command.description}",
            )
            for command in self.commands.names()
            if command.name.startswith(needle)
        ]

    def _skill_matches(self, token_text: str) -> list[tuple[str, str]]:
        skills = [skill for skill in (self.runtime.skills or [])]
        if not skills:
            return []
        if token_text.startswith("/skill:"):
            needle = token_text[len("/skill:") :].lower()
            selected = [
                skill
                for skill in skills
                if skill.name.lower().startswith(needle)
            ]
        else:
            needle = token_text[1:].lower()
            selected = [
                skill for skill in skills if skill.name.lower().startswith(needle)
            ]
            if not selected:
                selected = [
                    skill
                    for skill in skills
                    if f"skill:{skill.name}".lower().startswith(needle)
                ]
        return [
            (
                f"skill:{skill.name}",
                f"{t('type.skill')} · {skill.description}",
            )
            for skill in sorted(selected, key=lambda item: item.name)
        ]

    def _argument_values(self, name: str) -> list[tuple[str, str]]:
        """Fixed vocabulary from the shared table; model ids come from the catalog."""
        from zenagent.app.slash_args import argument_values

        if name == "model":
            # Enter executes the highlighted value, so the list leads with the
            # current model and then the usable ones: a blind Enter is a no-op
            # instead of a switch to a model whose key is missing.
            entries = sorted(
                self.models.entries(),
                key=lambda entry: (not entry.current, not entry.available),
            )
            return [
                (
                    entry.id,
                    f"{entry.provider} · "
                    + (
                        t("tui.app.model_ready")
                        if entry.available
                        else entry.reason or t("tui.app.model_unavailable")
                    ),
                )
                for entry in entries
            ]
        return argument_values("/" + name)

    def action_hint_next(self) -> None:
        if self.command_hints:
            self.command_hints.move(1)

    def action_hint_prev(self) -> None:
        if self.command_hints:
            self.command_hints.move(-1)

    def action_hint_dismiss(self) -> None:
        if self.command_hints:
            self.command_hints.hide()

    def action_hint_accept(self) -> None:
        hints = self.command_hints
        if hints is None or not hints.active:
            return
        selected = hints.selected
        if not selected:
            return
        prompt = self.query_one(PromptInput)
        draft = prompt.text
        if self._hint_kind == "arg":
            # Argument values replace the typed value and keep the old shape.
            replacement = f"{selected} "
            tail = ""
        else:
            # Slash entries may be completed mid-sentence: replace only the
            # token, keep the prose before and after it.
            replacement = f"/{selected} "
            tail = draft[self._hint_end :]
        new_text = draft[: self._hint_start] + replacement + tail
        prompt.clear()
        prompt.insert(new_text)
        prompt.move_cursor(
            prompt.document.get_location_from_index(
                self._hint_start + len(replacement)
            )
        )
        prompt.focus()
        hints.hide()

    def action_scroll_up(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_pages(-1)

    def action_scroll_down(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_pages(1)

    def on_transcript_view_pending_changed(
        self, message: TranscriptView.PendingChanged
    ) -> None:
        bar = self.query_one(NewContentBar)
        if message.pending:
            bar.show_marker()
        else:
            bar.hide_marker()

    def action_jump_to_bottom(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_to_bottom()

    def on_prompt_input_resized(self, message: PromptInput.Resized) -> None:
        for gutter in self.query(PromptGutter):
            gutter.styles.height = message.rows

    def action_toggle_sidebar(self) -> None:
        self._sidebar_open = not self._sidebar_open
        self._sync_chrome()
        # The dock column changed width: re-degrade the status segments.
        self._refresh_status()

    def get_system_commands(self, screen):
        from textual.app import SystemCommand

        yield from super().get_system_commands(screen)
        yield SystemCommand(
            t("tui.app.show_todo"),
            t("tui.app.show_todo_desc"),
            self.action_show_todo_list,
        )
        for command in self.commands.names():
            yield SystemCommand(
                command.name,
                command.description,
                lambda name=command.name: self._run_command(name),
            )

    def _run_command(self, name: str) -> None:
        """Palette entries run the same path as typing the command."""
        self.run_worker(self._dispatch_command(name, ""))

    def open_menu(self, kind: str, factory, callback) -> None:
        """Single-instance menus: a repeat request focuses, never stacks.

        omp models this with ``#openMenus`` and deliberately has no raise API
        (``05-overlays-and-surfaces.md`` §4); one entry per kind is enough here
        because our palettes are all rooted at the same screen.
        """
        if kind in self._menus:
            # A repeat request must not stack a second panel; focus the open one.
            existing = self._menus[kind]
            focus = getattr(existing, "focus", None)
            if callable(focus):
                focus()
            return
        screen = factory()
        self._menus[kind] = screen

        async def finished(result=None) -> None:
            self._menus.pop(kind, None)
            outcome = callback(result)
            if inspect.isawaitable(outcome):
                await outcome

        self.push_screen(screen, finished)

    def _show_help(self) -> None:
        for log in self.query(TranscriptView):
            log.append_notice(t("tui.app.help_header"))
            for command in self.commands.names():
                log.append_notice(
                    f"  /{command.name}  {command.description}"
                )

    def set_appearance(self, spec: str) -> str:
        """Switch the palette to ``spec`` (auto|dark|light); persist known values."""
        from zenagent import settings
        from zenagent.app.appearance import THEME_CHOICES

        normalized = (spec or "").strip().lower()
        self._apply_appearance(resolve_theme(normalized))
        if normalized in THEME_CHOICES:
            settings.set_pref("theme", normalized)
            self._theme_spec = normalized
        return theme_name(self._appearance)

    def _apply_appearance(self, appearance) -> None:
        palette.bind(appearance)
        self._appearance = appearance
        self.theme = theme_name(appearance)
        self._repaint_rich()

    def _repaint_rich(self) -> None:
        """Rebuild the text that carries literal colours (not CSS variables).

        A theme switch repaints stylesheet-driven widgets on its own, but the
        transcript rows and status band embed Rich styles from ``palette``, so
        they are rebuilt from the current views instead.
        """
        for log in self.query(TranscriptView):
            log.reset()
        self._refresh_status()

    def open_theme_picker(self) -> None:
        from zenagent.app.slash_args import argument_values
        from zenagent.cli.tui.screens.modals import ChoicePicker

        async def selected(value: str | None) -> None:
            if not value:
                return
            self.set_appearance(value)
            self.notify(t("tui.app.theme_set", value=value), timeout=3)

        self.open_menu(
            "theme",
            lambda: ChoicePicker(
                t("tui.app.theme_header"),
                argument_values("/theme"),
                self._theme_spec,
            ),
            selected,
        )

    def set_language(self, spec: str) -> str:
        """Switch the interface language (auto|en|zh) and rebuild the chrome."""
        from zenagent import settings

        normalized = (spec or "").strip().lower()
        code = resolve_language(normalized)
        set_locale(code)
        if normalized in LANGUAGE_CHOICES:
            settings.set_pref("language", normalized)
            self._language_spec = normalized
        self._rebuild_commands()
        for sidebar in self.query(Sidebar):
            sidebar.refresh_language()
        self._repaint_rich()
        return code

    def _rebuild_commands(self) -> None:
        """Describe commands in the active language (registry text is resolved)."""
        self.commands = build_default_commands(
            AppServices(
                runtime=self.runtime,
                chat=self.chat,
                models=self.models,
                sessions=self.sessions,
            )
        )

    def open_language_picker(self) -> None:
        from zenagent.app.slash_args import argument_values
        from zenagent.cli.tui.screens.modals import ChoicePicker

        async def selected(value: str | None) -> None:
            if not value:
                return
            self.set_language(value)
            self.notify(t("tui.app.language_set", value=value), timeout=3)

        self.open_menu(
            "language",
            lambda: ChoicePicker(
                t("tui.app.language_header"),
                argument_values("/lang"),
                self._language_spec,
            ),
            selected,
        )

    def open_settings(self) -> None:
        from zenagent.cli.tui.screens.modals import SettingsPicker

        async def selected(choice: str | None) -> None:
            if not choice:
                return
            if choice == "sidebar":
                self.action_toggle_sidebar()
            elif choice == "thinking":
                self.action_cycle_thinking()
            elif choice == "theme":
                self.open_theme_picker()
            elif choice == "language":
                self.open_language_picker()
            elif choice == "model-keys":
                self.open_model_keys()
            elif choice == "search-keys":
                self.open_search_keys()
            elif choice == "search-provider":
                self.open_search_provider()
            else:
                await self._dispatch_command(choice, "")

        status = self.chat.status
        self.open_menu(
            "settings",
            lambda: SettingsPicker(
                status.thinking,
                status.model_id,
                self._theme_spec,
                self._language_spec,
            ),
            selected,
        )

    # -- runtime settings: API keys and the search provider -----------------

    def _model_providers(self) -> list[tuple[str, str, bool]]:
        from zenagent import settings

        rows = []
        for provider_id, provider in self.runtime.catalog.items():
            configured = settings.get_api_key(provider.api_key_env) is not None
            rows.append((f"{provider_id}", provider.api_key_env, configured))
        return rows

    def _search_providers(self) -> list[tuple[str, str, bool]]:
        from zenagent import settings
        from zenagent.tools.web import LABELS
        from zenagent.tools.web.providers import PROVIDERS

        rows = []
        for name in PROVIDERS:
            env_name = f"{name.upper()}_API_KEY"
            if name in ("bing", "duckduckgo"):
                continue  # keyless
            configured = settings.get_api_key(env_name) is not None
            rows.append((LABELS.get(name, name), env_name, configured))
        return rows

    def open_model_keys(self) -> None:
        from zenagent.cli.tui.screens.secrets import ProviderKeyPicker

        async def selected(env_name: str | None) -> None:
            if env_name:
                self._prompt_secret(env_name, "model")

        self.open_menu(
            "model-keys",
            lambda: ProviderKeyPicker(self._model_providers(), scope="model"),
            selected,
        )

    def open_search_keys(self) -> None:
        from zenagent.cli.tui.screens.secrets import ProviderKeyPicker

        async def selected(env_name: str | None) -> None:
            if env_name:
                self._prompt_secret(env_name, "search")

        self.open_menu(
            "search-keys",
            lambda: ProviderKeyPicker(self._search_providers(), scope="search"),
            selected,
        )

    def open_search_provider(self) -> None:
        from zenagent.cli.tui.screens.secrets import SearchProviderPicker

        async def selected(name: str | None) -> None:
            if not name:
                return
            from zenagent import settings

            settings.set_pref("search_provider", None if name == "auto" else name)
            self.notify(
                t("tui.app.search_provider_set", name=name), timeout=3
            )
            self._refresh_status()

        self.open_menu("search-provider", SearchProviderPicker, selected)

    def _prompt_secret(self, env_name: str, scope: str) -> None:
        from zenagent import settings
        from zenagent.cli.tui.screens.secrets import SecretInput

        async def entered(value: str | None) -> None:
            if not value:
                return
            settings.set_api_key(env_name, value)
            recovered = self.runtime.reload_api_key()
            if recovered:
                self.chat.clear_last_error()
            self.notify(
                t(
                    "tui.app.saved_key_ready" if recovered else "tui.app.saved_key",
                    env=env_name,
                ),
                timeout=3,
            )
            self._refresh_status()

        scope_key = (
            "tui.secrets.scope_model" if scope == "model" else "tui.secrets.scope_search"
        )
        self.push_screen(
            SecretInput(
                title=env_name,
                hint=t("tui.secrets.hint", scope=t(scope_key)),
            ),
            entered,
        )

    def action_pick_model(self) -> None:
        self.open_model_picker()

    def _apply_model(self, model_id: str) -> None:
        """Switch models and repeat every side effect, for every entry point.

        The picker (``alt+p`` / bare ``/model``), the argument path
        (``/model <id>``, including Enter on a value hint) and ``ctrl+p``
        cycling must not drift: the banner healing, the confirmation toast and
        the status repaint happen here once.
        """
        result = self.models.switch(model_id)
        if not result.ok:
            self.notify(result.reason or t("tui.app.switch_failed"))
            return
        if self.runtime.missing_api_key:
            # Picking a model that already has a key heals the banner.
            self.runtime.missing_api_key = None
            self.chat.clear_last_error()
        self.notify(t("tui.app.model_set", model=self.models.current()), timeout=3)
        self._refresh_status()

    def open_model_picker(self) -> None:
        from zenagent.cli.tui.screens.modals import ModelPicker

        async def selected(model_id: str | None) -> None:
            if model_id:
                self._apply_model(model_id)

        self.open_menu("model", lambda: ModelPicker(self.models.entries()), selected)

    def open_hotkeys(self) -> None:
        """Read-only key-binding sheet (omp's /hotkeys overlay)."""
        from zenagent.cli.tui.screens.modals import HotkeysSheet
        from zenagent.cli.tui.widgets.prompt_input import APP_CHORDS

        lines = [f"{key:<16} {description}" for key, _action, description in self.BINDINGS]
        lines.extend(f"{key:<16} {action}" for key, action in APP_CHORDS.items())
        self.open_menu("hotkeys", lambda: HotkeysSheet(lines), lambda _result: None)

    def open_usage_dashboard(self) -> None:
        """Current-session totals plus the recent-session usage report."""
        from zenagent.cli.tui.screens.modals import UsageDashboard

        status = self.chat.status
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
        self.open_menu(
            "usage", lambda: UsageDashboard(lines), lambda _result: None
        )

    def open_mcp_add(self) -> None:
        """Two-field wizard that appends a stdio server to mcp.json."""
        from zenagent.cli.tui.screens.modals import McpAdd

        async def added(result) -> None:
            if not result:
                return
            import shlex

            from zenagent.mcp.config import save_server

            name, command_text = result
            try:
                command = shlex.split(command_text)
            except ValueError:
                command = [command_text]
            try:
                save_server(name, command)
            except ValueError as exc:
                self.notify(t("tui.app.mcp_add_failed", error=exc), timeout=3)
                return
            self.notify(t("tui.app.mcp_added", name=name))
            try:
                await self.runtime.refresh_mcp()
            except RuntimeError:
                pass
            self._refresh_status()

        self.open_menu("mcp-add", lambda: McpAdd(), added)

    def open_todo_editor(self) -> None:
        """Editable todo list; writes the same sidecar the model's tool uses."""
        from zenagent.cli.tui.screens.modals import TodoEditor
        from zenagent.tools.todo import save_todo_phases

        async def edited(phases) -> None:
            if phases is None:
                return
            session = getattr(self.runtime, "session", None)
            if session is None:
                return
            try:
                save_todo_phases(Path(session.path), phases)
            except OSError as exc:
                self.notify(t("tui.app.todo_save_failed", error=exc), timeout=3)
                return
            self.chat.reload_todos()
            self._refresh_status()

        self.open_menu(
            "todo-edit", lambda: TodoEditor(self.chat.todos), edited
        )

    def open_plan_review(self) -> None:
        """Read the last plan, then approve (→ full) or keep planning."""
        from zenagent.cli.tui.screens.modals import PlanReview

        plan = self.chat.last_assistant_text()
        if not plan:
            self.notify(t("tui.app.no_plan"), timeout=3)
            return

        async def reviewed(choice: str | None) -> None:
            if choice != "approve":
                return
            self._mode_before_plan = None
            self.runtime.set_mode("full")
            self.notify(t("tui.app.mode_set", mode="full"))
            self._refresh_status()

        self.open_menu("plan-review", lambda: PlanReview(plan), reviewed)

    def open_mode_picker(self) -> None:
        from zenagent.app.slash_args import argument_values
        from zenagent.cli.tui.screens.modals import ChoicePicker

        async def selected(mode: str | None) -> None:
            if mode:
                self.runtime.set_mode(mode)
                self.notify(t("tui.app.mode_set", mode=mode))
                self._refresh_status()

        self.open_menu(
            "mode",
            lambda: ChoicePicker(
                t("tui.app.mode_header"),
                argument_values("/mode"),
                self.runtime.mode,
            ),
            selected,
        )

    def open_thinking_picker(self) -> None:
        from zenagent.app.slash_args import argument_values
        from zenagent.cli.tui.screens.modals import ChoicePicker

        agent = self.runtime.agent

        async def selected(level: str | None) -> None:
            if level:
                agent.thinking = None if level == "off" else level
                self.notify(t("tui.app.thinking_set", level=level))
                self._refresh_status()

        self.open_menu(
            "thinking",
            lambda: ChoicePicker(
                t("tui.app.thinking_header"),
                argument_values("/thinking"),
                agent.thinking or "off",
            ),
            selected,
        )

    def open_session_picker(self, scope: str | None = None) -> None:
        from zenagent.cli.tui.screens.modals import SessionPicker

        self._session_scope = scope or self._session_scope
        current_scope = self._session_scope

        async def selected(action) -> None:
            if action is None:
                return
            if action.kind == "toggle-scope":
                other = "project" if current_scope == "all" else "all"
                self.call_later(self.open_session_picker, other)
                return
            # Never await a new modal from inside the dismiss callback: Textual
            # runs it in the message pump and the modal would deadlock.
            self.run_worker(self._apply_session_action(action), exclusive=False)

        self.open_menu(
            "sessions",
            lambda: SessionPicker(
                self.sessions.entries(scope=current_scope), scope=current_scope
            ),
            selected,
        )

    async def _apply_session_action(self, action) -> None:
        from zenagent.agent.session import session_title

        if action.kind == "resume" and action.path is not None:
            from zenagent import project

            owner = project.project_key_for_session(action.path)
            current = project.project_key(project.find_project_root())
            await self.sessions.resume(action.path)
            self._reset_after_switch()
            if owner is not None and owner != current:
                self.notify(t("tui.app.cross_project"))
            elif owner is None:
                self.notify(t("tui.app.legacy_session"))
            return
        if action.kind in ("new", "fork"):
            if action.kind == "new":
                await self.sessions.new()
            else:
                await self.sessions.fork()
            self._reset_after_switch()
            return
        target = action.path or self.sessions.current()
        if action.kind == "delete":
            prompt = t("tui.app.delete_confirm", name=target.name)
            if not await self._confirm(prompt):
                return
            was_current = target == self.sessions.current()
            self.sessions.delete(target)
            if was_current:
                self._reset_after_switch()
            self._refresh_status()
            self.notify(t("tui.app.session_trashed"))
        elif action.kind == "export":
            exported = self.sessions.export(target)
            self.notify(t("tui.app.exported", path=exported))
        elif action.kind == "rename":
            name = await self._prompt_rename(session_title(target))
            if name is None:
                return
            try:
                stored = self.sessions.rename(target, name)
            except ValueError as exc:
                self.notify(str(exc))
                return
            self.notify(t("tui.app.renamed", name=stored))

    def _reset_after_switch(self) -> None:
        if self._viewing is not None:
            self._exit_subagent_view(restore_focus=False)
        for log in self.query(TranscriptView):
            log.reset()
        self.chat.reload_transcript(drop_pending=True)
        self._refresh_status()

    async def _modal_result(self, screen):
        """Push a modal and await its dismissed value."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def done(result=None) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(screen, done)
        return await future

    async def _confirm(self, prompt: str) -> bool:
        from zenagent.cli.tui.screens.modals import Confirm

        return bool(await self._modal_result(Confirm(prompt)))

    async def _prompt_rename(self, value: str) -> str | None:
        from zenagent.cli.tui.screens.modals import RenamePrompt

        return await self._modal_result(RenamePrompt(value))

    async def on_prompt_input_submitted(self, message: PromptInput.Submitted) -> None:
        text = message.text
        templates = {
            template.name: template for template in (self.runtime.templates or [])
        }
        extension_commands = self.runtime.extension_commands or {}
        # A /skill: token can appear at line start or inside a sentence; the
        # parser rejects drafts owned by another slash command.
        if "/skill:" in text:
            resolution = build_skill_submission(
                text,
                list(self.runtime.skills or []),
                reserved_names={*templates, *extension_commands},
            )
            if resolution is not None:
                if resolution.error is not None:
                    for log in self.query(TranscriptView):
                        log.append_notice(resolution.error)
                    return
                submission = resolution.submission
                if submission is not None:
                    # Keep the user's own `/skill:...` line in history; the
                    # injected body goes to the model, the chip to the transcript.
                    self.history.add(text)
                    await self.chat.submit(submission.prompt)
                    return
        if text.startswith("/"):
            name, _, argument = text[1:].partition(" ")
            if self.commands.get(name) is not None:
                await self._dispatch_command(name, argument)
                return
            template = templates.get(name)
            if template is not None:
                # Prompt templates are the REPL's `/name args` feature; the TUI
                # shares the same expansion so a saved command works in both.
                self.history.add(text)
                await self.chat.submit(expand(template.body, argument))
                return
            extension = extension_commands.get(name)
            if extension is not None:
                self.history.add(text)
                await self._run_extension_command(extension, argument)
                return
            for log in self.query(TranscriptView):
                log.append_notice(t("tui.app.unknown_command", name=name))
            return
        self.history.add(text)
        await self.chat.submit(text)

    async def _run_extension_command(self, command, argument: str) -> None:
        """Run an extension's slash command; its return value becomes a notice."""
        try:
            result = command.handler(self.runtime.agent, argument)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - an extension must not kill the app
            for log in self.query(TranscriptView):
                log.append_notice(
                    t("tui.app.command_failed", name=command.name, error=exc)
                )
            return
        if result:
            for log in self.query(TranscriptView):
                log.append_notice(str(result))

    async def _dispatch_command(self, name: str, argument: str) -> None:
        try:
            result = await self.commands.dispatch(name, argument)
        except Exception as exc:  # noqa: BLE001 - a command must never exit the app
            # Slash commands run inside an input handler: anything escaping here
            # reaches Textual's _handle_exception, which tears the app down.
            for log in self.query(TranscriptView):
                log.append_notice(
                    t("tui.app.command_failed", name=name, error=exc)
                )
            return
        if result.model:
            # `/model <id>` (or Enter on a value hint) switches directly; only a
            # bare `/model` reaches the picker branch below.
            self._apply_model(result.model)
        if result.screen == "model-picker":
            self.open_model_picker()
        elif result.screen == "session-picker":
            self.open_session_picker("project")
        elif result.screen == "session-picker-all":
            self.open_session_picker("all")
        elif result.screen == "mode-picker":
            self.open_mode_picker()
        elif result.screen == "thinking-picker":
            self.open_thinking_picker()
        elif result.screen == "theme-picker":
            self.open_theme_picker()
        elif result.screen == "language-picker":
            self.open_language_picker()
        elif result.screen == "settings":
            self.open_settings()
        elif result.screen == "plan-review":
            self.open_plan_review()
        elif result.screen == "display-reset":
            self.action_display_reset()
        elif result.screen == "hotkeys":
            self.open_hotkeys()
        elif result.screen == "usage-dashboard":
            self.open_usage_dashboard()
        elif result.screen == "mcp-add":
            self.open_mcp_add()
        elif result.screen == "todo-edit":
            self.open_todo_editor()
        elif result.screen == "search-settings":
            self.open_search_provider()
        elif result.show_help:
            self._show_help()
        elif result.reset_transcript:
            for log in self.query(TranscriptView):
                log.reset()
            self._refresh_status()
        elif result.quit:
            self.action_quit_app()
        elif result.message:
            for log in self.query(TranscriptView):
                log.append_notice(result.message)
        if result.appearance:
            applied = self.set_appearance(result.appearance)
            for log in self.query(TranscriptView):
                log.append_notice(t("tui.app.theme_set", value=applied))
        if result.language:
            code = self.set_language(result.language)
            for log in self.query(TranscriptView):
                log.append_notice(t("tui.app.language_set", value=code))
        # `/mode` and friends change state the band renders; refresh after every
        # dispatch so the change is visible without waiting for the next event.
        self._refresh_status()

    def action_interrupt(self) -> None:
        if self._viewing is not None:
            self._exit_subagent_view()
            return

        """Esc: abort a run; on an empty idle draft, a double press rewinds.

        omp's chain: ``Esc`` always protects the draft, and two quick presses
        with nothing in the composer mean "undo the last turn". Anything else
        (a slower second press, a non-empty draft) is a no-op.
        """
        if self.chat.status.running:
            self._last_escape_at = None
            self.chat.abort()
            self.notify(t("tui.app.interrupted"), timeout=2)
            return
        prompt = self.query_one(PromptInput)
        if prompt.text:
            self._last_escape_at = None
            return
        now = time.monotonic()
        if (
            self._last_escape_at is not None
            and now - self._last_escape_at <= DOUBLE_ESCAPE_SECONDS
        ):
            self._last_escape_at = None
            self.run_worker(self._undo_last_turn())
        else:
            self._last_escape_at = now

    async def _undo_last_turn(self) -> None:
        """Double-Esc rewind: same path as ``/undo`` (new session file, recoverable)."""
        await self._dispatch_command("undo", "")

    def action_dequeue(self) -> None:
        """Alt+Up: take the last queued follow-up back into the prompt."""
        text = self.chat.dequeue_follow_up()
        if text is None:
            return
        prompt = self.query_one(PromptInput)
        prompt.insert(text)
        prompt.focus()

    def action_history_search(self) -> None:
        """Ctrl+R: search submitted prompts and recall one into the composer."""
        entries = self.history.entries()
        if not entries:
            self.notify(t("tui.app.no_history"), timeout=2)
            return

        from zenagent.cli.tui.screens.modals import HistorySearch

        async def selected(text: str | None) -> None:
            if not text:
                return
            prompt = self.query_one(PromptInput)
            prompt.clear()
            prompt.insert(text)
            prompt.focus()

        self.open_menu(
            "history", lambda: HistorySearch(list(reversed(entries))), selected
        )

    def action_clear_draft(self) -> None:
        """ctrl+c clears the draft (omp keeps a draft through Esc)."""
        prompt = self.query_one(PromptInput)
        if prompt.text:
            prompt.clear()

    def action_follow_up(self) -> None:
        """ctrl+q: queue while running, otherwise submit normally."""
        prompt = self.query_one(PromptInput)
        text = prompt.text.strip()
        if not text:
            return
        prompt.clear()
        if self.chat.status.running:
            self.chat.queue_follow_up(text)
        else:
            self.run_worker(self.chat.submit(text))

    def action_cycle_model(self) -> None:
        self._cycle_model(1)

    def action_cycle_model_backward(self) -> None:
        self._cycle_model(-1)

    def _cycle_model(self, step: int) -> None:
        entries = [entry for entry in self.models.entries() if entry.available]
        if not entries:
            return
        ids = [entry.id for entry in entries]
        current = self.chat.status.model_id
        try:
            index = (ids.index(current) + step) % len(ids)
        except ValueError:
            index = 0
        self._apply_model(ids[index])

    def action_toggle_plan(self) -> None:
        """alt+shift+p: flip plan mode on, and back to the previous mode off."""
        if self.runtime.mode == "plan":
            mode = self._mode_before_plan or "ask"
            self._mode_before_plan = None
        else:
            self._mode_before_plan = self.runtime.mode
            mode = "plan"
        self.runtime.set_mode(mode)
        self.notify(t("tui.app.mode_set", mode=mode))
        self._refresh_status()

    def action_retry(self) -> None:
        """F5 / alt+r: same path as ``/retry`` so the two cannot drift."""
        self.run_worker(self._dispatch_command("retry", ""))

    def action_edit_external(self) -> None:
        """ctrl+g: edit the draft in ``$VISUAL``/``$EDITOR`` and put it back."""
        prompt = self.query_one(PromptInput)
        edited = self.edit_draft_externally(prompt.text)
        if edited is None:
            return
        prompt.clear()
        prompt.insert(edited)

    def action_paste_clipboard(self) -> None:
        """ctrl+v: attach an image path from the clipboard, else paste the text.

        Terminals do not hand image bytes to the app, so the pragmatic contract
        is "copy a path, press ctrl+v": an existing image file is attached
        through the same channel as ``/image``; anything else is inserted into
        the draft as text.
        """
        from zenagent.attachments import read_image_argument
        from zenagent.cli.tui.clipboard import read_clipboard_text

        text = read_clipboard_text()
        if not text:
            self.notify(t("tui.app.clipboard_empty"), timeout=2)
            return
        candidate = text.strip()
        if candidate and "\n" not in candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                try:
                    block = read_image_argument(path)
                except ValueError:
                    block = None
                if block is not None:
                    model = self.runtime.agent.model
                    if not model.supports_vision:
                        self.notify(
                            t("cmdmsg.image_not_supported", model=model.id),
                            timeout=3,
                        )
                        return
                    self.chat.attach_image(block)
                    self.notify(
                        t("tui.app.image_attached", mime=block.mime_type), timeout=2
                    )
                    return
        self.query_one(PromptInput).insert(text)

    def edit_draft_externally(self, text: str) -> str | None:
        """Open ``text`` in the user's editor; return the result or ``None``.

        Returns ``None`` (leaving the draft untouched) when no editor is
        configured, the editor exits non-zero, or the process cannot start.
        Split out from the action so tests can drive it without a terminal.
        """
        raw = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not raw or not raw.strip():
            self.notify(t("tui.app.no_editor"), timeout=3)
            return None
        try:
            command = shlex.split(raw)
        except ValueError:
            command = []
        if not command:
            self.notify(t("tui.app.no_editor"), timeout=3)
            return None
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(text)
            name = handle.name
        path = Path(name)
        try:
            try:
                with self.suspend():
                    code = subprocess.call([*command, str(path)])
            except Exception as exc:  # noqa: BLE001 - never kill the app
                self.notify(t("tui.app.editor_failed", error=exc), timeout=3)
                return None
            if code != 0:
                self.notify(t("tui.app.editor_failed", error=f"exit {code}"), timeout=3)
                return None
            return path.read_text(encoding="utf-8")
        finally:
            path.unlink(missing_ok=True)

    def action_cycle_thinking(self) -> None:
        levels = ["off", "low", "medium", "high"]
        agent = self.runtime.agent
        current = agent.thinking or "off"
        try:
            agent.thinking = levels[(levels.index(current) + 1) % len(levels)]
        except ValueError:
            agent.thinking = "medium"
        self._refresh_status()

    def action_toggle_thinking(self) -> None:
        """ctrl+t expands/collapses every thinking row (omp toggles the block)."""
        from zenagent.cli.tui.widgets.rows import ThinkingRow

        rows = list(self.query(ThinkingRow))
        if not rows:
            return
        expand = any(row.collapsed for row in rows)
        for row in rows:
            row.set_expanded(expand)

    def _promote_pending(self, pending_id: int) -> None:
        """Click / Ctrl+S on a queued message: move it to the steering lane."""
        result = self.chat.promote(pending_id)
        if result == "ok":
            self.notify(t("tui.queue.promoted"), timeout=2)
        elif result == "blocked_images":
            self.notify(t("tui.queue.blocked_images"), timeout=2)
        self._refresh_status()

    def action_steer(self) -> None:
        prompt = self.query_one(PromptInput)
        text = prompt.text.strip()
        if text:
            if self.chat.status.running:
                prompt.clear()
                self.chat.steer(text)
            return
        queued = [item for item in self.chat.status.pending if item.lane == "queue"]
        if queued:
            self._promote_pending(queued[-1].id)

    def action_clear_queue(self) -> None:
        cleared = self.chat.clear_pending()
        if cleared:
            self.notify(t("tui.queue.cleared"), timeout=2)
        self._refresh_status()

    def action_toggle_tool_visibility(self) -> None:
        """ctrl+shift+o: hide/show every tool card (omp's tools.toggleVisibility)."""
        transcript = self.query_one(TranscriptView)
        transcript.set_tools_hidden(not transcript.tools_hidden)

    def action_display_reset(self) -> None:
        """alt+l: restore the default display state after fiddling with cards."""
        from zenagent.cli.tui.widgets.rows import ThinkingRow

        transcript = self.query_one(TranscriptView)
        transcript.set_tools_hidden(False)
        for row in self.query(ThinkingRow):
            if not row.collapsed:
                row.action_toggle()
        transcript.scroll_end(animate=False)

    def action_toggle_tools(self) -> None:
        """Batch expand/collapse; single cards keep Enter/space (omp parity)."""
        from zenagent.cli.tui.widgets.tool_card import ToolCard

        cards = list(self.query(ToolCard))
        if not cards:
            return
        expand = any(card.collapsed for card in cards)
        for card in cards:
            card.set_collapsed(not expand)
