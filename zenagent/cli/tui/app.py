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
from pathlib import Path

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.events import MouseScrollDown, MouseScrollUp

from zenagent.app.appearance import APPEARANCES, resolve_theme
from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.runtime import Runtime
from zenagent.app.sessions import SessionManager
from zenagent.app.views import RunStatus
from zenagent.cli.tui import palette
from zenagent.cli.tui.app_parts.appearance import AppearanceMixin
from zenagent.cli.tui.app_parts.commands import CommandsMixin
from zenagent.cli.tui.app_parts.display import DisplayMixin
from zenagent.cli.tui.app_parts.drafts import DraftsMixin
from zenagent.cli.tui.app_parts.hints import HintsMixin
from zenagent.cli.tui.app_parts.models import ModelsMixin
from zenagent.cli.tui.app_parts.queue import DOUBLE_ESCAPE_SECONDS, QueueActionsMixin
from zenagent.cli.tui.app_parts.sessions import SessionsMixin
from zenagent.cli.tui.app_parts.update_notice import UpdateNoticeMixin
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
from zenagent.cli.tui.widgets.sidebar_resize import SidebarResize, SidebarResizeHandle
from zenagent.cli.tui.widgets.status_band import StatusBand
from zenagent.cli.tui.widgets.subagent_banner import SubagentBanner
from zenagent.cli.tui.widgets.todo_hud import TodoHud
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.cli.tui.widgets.work_indicator import WorkIndicator
from zenagent.cli.tui.widgets.work_strip import WorkStrip
from zenagent.i18n import LANGUAGE_CHOICES, resolve_language, set_locale, t

__all__ = ["DOUBLE_ESCAPE_SECONDS", "ZenAgentApp"]



def _saved_theme() -> str | None:
    """The persisted ``theme`` preference, read lazily (tests patch settings)."""
    from zenagent import settings

    return settings.get_pref("theme")


def _saved_language() -> str | None:
    """The persisted ``language`` preference, read lazily (tests patch settings)."""
    from zenagent import settings

    return settings.get_pref("language")


class ZenAgentApp(
    UpdateNoticeMixin,
    AppearanceMixin,
    ModelsMixin,
    SessionsMixin,
    HintsMixin,
    CommandsMixin,
    QueueActionsMixin,
    DraftsMixin,
    DisplayMixin,
    App,
):
    """A single-column chat app; no mouse, no animations."""

    CSS_PATH = "theme.tcss"
    TITLE = "ZenAgent"
    # Drag-to-select is on: it is the one mouse path an alt-buffer app cannot
    # inherit from the terminal the way omp does (omp's committed lines live in
    # the terminal's own scrollback). It was switched off after a Textual 8.2
    # Screen._forward_event crash on Markdown blocks; that crash no longer
    # reproduces on the pinned 8.2.8 (tests/test_tui_selection.py pins the
    # drag, the ctrl+c split and a drag during streaming).
    ALLOW_SELECT = True
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
        images: list | None = None,
    ) -> None:
        super().__init__()
        #: ``--image`` blocks attached before the first turn.
        self._initial_images = list(images or [])
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
        self._sidebar_resize = SidebarResize(self)
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
    # the "new output" bar), and dragging selects text. With a selection live,
    # ctrl+c copies it (Textual's own binding on the Screen wins over our
    # clear-draft chord); with no selection it still clears the draft — the same
    # split omp gives `tui.input.copy`.

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
        for block in self._initial_images:
            self.chat.attach_image(block)
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
                self.chat, self.models, width=self.sidebar_width
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
        return self._sidebar_resize.width

    def begin_sidebar_resize(self) -> None:
        self._sidebar_resize.begin()

    def end_sidebar_resize(self) -> None:
        self._sidebar_resize.end()

    def resize_sidebar(self, screen_x: int) -> None:
        self._sidebar_resize.resize(screen_x)

    def _band_width(self) -> int:
        return self._sidebar_resize.band_width

    def _apply_sidebar_width(self) -> None:
        self._sidebar_resize.apply()

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

    def open_theme_picker(self) -> None:
        from zenagent.cli.tui.screens.appearance import (
            open_theme_picker as _open_theme_picker,
        )

        _open_theme_picker(self)

    def open_language_picker(self) -> None:
        from zenagent.cli.tui.screens.appearance import (
            open_language_picker as _open_language_picker,
        )

        _open_language_picker(self)

    def open_settings(self) -> None:
        from zenagent.cli.tui.screens.pickers import open_settings as _open_settings

        _open_settings(self)

    def open_model_picker(self) -> None:
        from zenagent.cli.tui.screens.providers import (
            open_model_picker as _open_model_picker,
        )

        _open_model_picker(self)

    def open_model_keys(self) -> None:
        from zenagent.cli.tui.screens.providers import (
            open_model_keys as _open_model_keys,
        )

        _open_model_keys(self)

    def open_search_keys(self) -> None:
        from zenagent.cli.tui.screens.providers import (
            open_search_keys as _open_search_keys,
        )

        _open_search_keys(self)

    def open_search_provider(self) -> None:
        from zenagent.cli.tui.screens.providers import (
            open_search_provider as _open_search_provider,
        )

        _open_search_provider(self)

    def open_hotkeys(self) -> None:
        from zenagent.cli.tui.screens.pickers import open_hotkeys as _open_hotkeys

        _open_hotkeys(self)

    def open_usage_dashboard(self) -> None:
        from zenagent.cli.tui.screens.pickers import (
            open_usage_dashboard as _open_usage_dashboard,
        )

        _open_usage_dashboard(self)

    def open_mcp_add(self) -> None:
        from zenagent.cli.tui.screens.pickers import open_mcp_add as _open_mcp_add

        _open_mcp_add(self)

    def open_todo_editor(self) -> None:
        from zenagent.cli.tui.screens.pickers import (
            open_todo_editor as _open_todo_editor,
        )

        _open_todo_editor(self)

    def open_plan_review(self) -> None:
        from zenagent.cli.tui.screens.pickers import (
            open_plan_review as _open_plan_review,
        )

        _open_plan_review(self)

    def open_mode_picker(self) -> None:
        from zenagent.cli.tui.screens.pickers import (
            open_mode_picker as _open_mode_picker,
        )

        _open_mode_picker(self)

    def open_thinking_picker(self) -> None:
        from zenagent.cli.tui.screens.pickers import (
            open_thinking_picker as _open_thinking_picker,
        )

        _open_thinking_picker(self)

    def open_session_picker(self, scope: str | None = None) -> None:
        from zenagent.cli.tui.screens.pickers import (
            open_session_picker as _open_session_picker,
        )

        _open_session_picker(self, scope)

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

