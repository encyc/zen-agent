"""Queue, steering and rewind key actions: thin dispatch to the controller."""

from __future__ import annotations

import time

from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.i18n import t

#: Two empty-draft Escapes this close together mean "rewind the last turn"
#: (omp's Esc chain level 13); a slower second press just interrupts nothing.
DOUBLE_ESCAPE_SECONDS = 0.6


class QueueActionsMixin:
    """Turn-level actions. Timing state (``_last_escape_at``) stays on the App."""

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

    def action_follow_up(self) -> None:
        """ctrl+q: extend the running turn, otherwise submit normally.

        The three lanes have three intents: Enter while running parks the text
        for the *next* run, ctrl+s steers (interrupts after the tool round),
        and ctrl+q rides the kernel follow-up channel — the run keeps going
        with this text as its next user turn. Images cannot ride that channel,
        so an attachment falls back to the queue lane.
        """
        prompt = self.query_one(PromptInput)
        text = prompt.text.strip()
        if not text:
            return
        prompt.clear()
        if not self.chat.status.running:
            self.run_worker(self.chat.submit(text))
            return
        if self.chat.attachments:
            self.chat.queue_follow_up(text)
            return
        self.chat.follow_up(text)
        if self.chat.status.continuing:
            # The controller falls back to the queue lane when no run is
            # streaming (compaction); the pending band already shows that one.
            self.notify(t("tui.queue.continuing"), timeout=2)

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

    def action_retry(self) -> None:
        """F5 / alt+r: same path as ``/retry`` so the two cannot drift."""
        self.run_worker(self._dispatch_command("retry", ""))

