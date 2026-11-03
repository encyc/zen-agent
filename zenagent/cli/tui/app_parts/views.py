"""Transcript scrolling plus the subagent/job-log view swaps."""

from __future__ import annotations

from textual.events import MouseScrollDown, MouseScrollUp

from zenagent.cli.tui.widgets.new_content import NewContentBar
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.subagent_banner import SubagentBanner
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.i18n import t


class ViewActionsMixin:
    """View state (``_viewing``, ``_job_log_screen``) stays on the App."""

    def _scroll_chat(self, rows: int) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_lines(rows)

    def _on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._scroll_chat(-self.WHEEL_ROWS)

    def _on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self._scroll_chat(self.WHEEL_ROWS)

    def action_scroll_up(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_pages(-1)

    def action_scroll_down(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_pages(1)

    def action_jump_to_bottom(self) -> None:
        for transcript in self.query(TranscriptView):
            transcript.scroll_to_bottom()

    def on_transcript_view_pending_changed(
        self, message: TranscriptView.PendingChanged
    ) -> None:
        bar = self.query_one(NewContentBar)
        if message.pending:
            bar.show_marker()
        else:
            bar.hide_marker()

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

