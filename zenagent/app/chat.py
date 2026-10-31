"""ChatController: one run at a time, view-model state, follow-up queue."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from zenagent.app.compact import compact, should_auto_compact
from zenagent.app.retry import prepare_retry
from zenagent.app.runtime import Runtime
from zenagent.app.support import cache_hit_ratio
from zenagent.app.views import (
    MessageView,
    RunStatus,
    ToolCallView,
    TranscriptNotice,
    args_preview,
    messages_to_views,
    usage_total,
)
from zenagent.events import AgentEvent
from zenagent.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    UserMessage,
)

__all__ = ["ChatController"]


class ChatController:
    """Drives the agent and exposes snapshots a frontend can render."""

    def __init__(
        self, runtime: Runtime, *, on_change: Callable[[], None] = lambda: None
    ) -> None:
        self.runtime = runtime
        self._on_change = on_change
        self._transcript: list[MessageView | ToolCallView] = []
        self._current: MessageView | None = None
        self._cards: dict[str, ToolCallView] = {}
        #: (text, images) pairs; images are captured when the turn is queued.
        self._follow_ups: list[tuple[str, list[ImageContent]]] = []
        self._task: asyncio.Task | None = None
        self._running = False
        #: True while an automatic compaction runs: the controller stays busy
        #: so a submit queues instead of racing the summarizer (C2).
        self._compacting = False
        #: Last finished turn: wall time and output tokens (status segments).
        self._turn_ms = 0
        self._turn_output = 0
        #: Provider failure from the last run, shown by the TUI's error banner.
        self._last_error = ""
        #: Out-of-band lines (compaction dividers, auto-compaction failures) the
        #: frontend drains on refresh.
        self._notices: list[TranscriptNotice] = []
        #: Images attached with /image that will ride along with the next prompt.
        self._attachments: list[ImageContent] = []
        runtime.agent.subscribe(self._on_event)

    # -- snapshots ----------------------------------------------------------

    @property
    def transcript(self) -> list[MessageView | ToolCallView]:
        return list(self._transcript)

    @property
    def status(self) -> RunStatus:
        agent = self.runtime.agent
        usage = usage_total(agent.messages)
        last = self.runtime.guard.last_usage
        context_used = (
            last.input + last.output if last is not None else usage.input + usage.output
        )
        return RunStatus(
            model_id=agent.model.id,
            thinking=agent.thinking,
            running=self._running or self._compacting,
            input_tokens=usage.input,
            output_tokens=usage.output,
            cache_read=usage.cache_read,
            cache_write=usage.cache_write,
            cache_hit_ratio=cache_hit_ratio(usage, agent.model.api),
            turn_ms=self._turn_ms,
            turn_output_tokens=self._turn_output,
            last_error=self._last_error,
            cost=usage.cost.total,
            context_used=context_used,
            context_window=agent.model.context_window,
            follow_ups=len(self._follow_ups),
            steering=agent.pending_steering,
            session_name=Path(self.runtime.session.path).stem,
            cwd=str(Path.cwd()),
            mode=self.runtime.mode,
        )

    # -- commands -----------------------------------------------------------

    async def submit(
        self, text: str, *, images: list[ImageContent] | None = None
    ) -> None:
        """Start a run, or queue the text when one is already running.

        ``images`` overrides the pending attachment list; otherwise whatever was
        attached with ``/image`` rides along and is cleared once the run starts.
        """
        if self._running or self._compacting:
            pending = list(images if images is not None else self._attachments)
            if images is None:
                self._attachments.clear()
            self.queue_follow_up(text, pending)
            return
        pending = list(images if images is not None else self._attachments)
        if images is None:
            self._attachments.clear()
        self._task = asyncio.create_task(self._run(text, pending))

    @property
    def attachments(self) -> list[ImageContent]:
        """Pending images attached but not yet sent."""
        return list(self._attachments)

    def attach_image(self, block: ImageContent) -> None:
        self._attachments.append(block)
        self._notify()

    def remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._attachments):
            del self._attachments[index]
            self._notify()

    def clear_attachments(self) -> None:
        if self._attachments:
            self._attachments.clear()
            self._notify()

    def queue_follow_up(
        self, text: str, images: list[ImageContent] | None = None
    ) -> None:
        """Queue a follow-up; ``images=None`` takes the pending attachments."""
        if not text.strip():
            return
        pending = list(images if images is not None else self._attachments)
        if images is None:
            self._attachments.clear()
        self._follow_ups.append((text, pending))
        self._notify()

    def clear_follow_ups(self) -> None:
        self._follow_ups.clear()
        self._notify()

    def dequeue_follow_up(self) -> str | None:
        """Take the most recently queued follow-up back out of the queue.

        omp's dequeue semantics: the last thing you queued is the one you most
        likely want to edit, so pop from the end. Returns ``None`` when empty.
        """
        if not self._follow_ups:
            return None
        text, images = self._follow_ups.pop()
        if images:
            self._attachments[:0] = images  # put the images back in the composer
        self._notify()
        return text

    def steer(self, text: str) -> None:
        if text.strip():
            self.runtime.agent.steer(text)
            self._notify()

    def abort(self) -> None:
        self.runtime.agent.abort()
        self._notify()

    def set_last_error(self, message: str) -> None:
        """Show a frontend-initiated problem in the dock banner.

        Used for setup states that block a run before the agent starts (a first
        launch without an API key); a provider failure still overwrites it on
        the next run.
        """
        message = (message or "").strip()
        if message == self._last_error:
            return
        self._last_error = message
        self._notify()

    def clear_last_error(self) -> None:
        """Dismiss the provider-error banner (click on the dock row)."""
        if self._last_error:
            self._last_error = ""
            self._notify()

    def take_notices(self) -> list[TranscriptNotice]:
        """Drain out-of-band lines the frontend should render (dividers, info)."""
        notices = self._notices
        self._notices = []
        return notices

    def _before_tokens(self) -> int:
        last = self.runtime.guard.last_usage
        if last is not None:
            return last.input + last.cache_read
        usage = usage_total(self.runtime.agent.messages)
        return usage.input + usage.output

    async def compact_now(self, *, force: bool = False) -> str | None:
        """Summarize the transcript into a new session file; returns the notice.

        Refuses mid-run (nested model calls would corrupt the transcript) and on
        an empty transcript. ``force`` is the auto path: the controller already
        owns the busy flag, so only the agent streaming check applies.
        """
        if not force and (self._running or self._compacting):
            return None
        if self.runtime.agent.is_streaming:
            return None
        if not self.runtime.agent.messages:
            return None
        before = self._before_tokens()
        path = await compact(self.runtime.agent)
        await self.runtime.switch_session(path)
        self.reload_transcript()
        text = f"compacted · {before:,} prompt tokens → summary session"
        self._notices.append(TranscriptNotice("divider", text))
        self._notify()
        return text

    def reload_transcript(self) -> None:
        """Rebuild views from the current agent messages (after a session switch)."""
        from zenagent.app.views import messages_to_views

        self._transcript = messages_to_views(self.runtime.agent.messages)
        self._cards.clear()
        self._current = None
        self._notify()

    def clear(self) -> None:
        self.runtime.agent.reset()
        self._transcript.clear()
        self._cards.clear()
        self._current = None
        self._attachments.clear()
        self._notify()

    def retry(self) -> str | None:
        """Drop everything after the last user message and run that turn again.

        The user message is dropped too and re-sent, so the transcript never
        ends up with two copies of the same prompt. Shares the truncation logic
        with the REPL through :func:`zenagent.app.retry.prepare_retry`.
        """
        if self._running:
            return None
        prepared = prepare_retry(self.runtime.agent, self.runtime.session)
        if prepared is None:
            return None
        text, new_session = prepared
        self.runtime.session = new_session
        self._transcript = messages_to_views(self.runtime.agent.messages)
        self._cards.clear()
        self._current = None
        self._task = asyncio.create_task(self._run(text))
        self._notify()
        return text

    async def wait_for_idle(self) -> None:
        while self._task is not None:
            task = self._task
            await asyncio.gather(task, return_exceptions=True)

    # -- run loop -----------------------------------------------------------

    async def _run(self, text: str, images: list[ImageContent] | None = None) -> None:
        self._running = True
        self._last_error = ""
        self._notify()
        started = time.monotonic()
        before_output = usage_total(self.runtime.agent.messages).output
        try:
            await self.runtime.agent.prompt(text, images=images or None)
        finally:
            if self._current is not None:
                # Aborts and provider errors skip message_end; do not leave a
                # row ticking after the run is over.
                self._finish_thinking(self._current)
            self._current = None
            self._turn_ms = int((time.monotonic() - started) * 1000)
            self._turn_output = (
                usage_total(self.runtime.agent.messages).output - before_output
            )
            if self.runtime.agent.termination == "error":
                detail = self.runtime.agent.error_message
                self._last_error = (
                    detail if detail and detail != "error" else "the run failed"
                )
            self._notify()
            # Stay busy (running=True, task set) through the summarizer call so
            # a submit during compaction queues instead of racing it. Queued
            # turns go first: compress only between bursts (Review Focus #1).
            if self.runtime.agent.termination != "aborted" and not self._follow_ups:
                await self._maybe_auto_compact()
            self._running = False
            self._task = None
            self._notify()
            if self._follow_ups and self.runtime.agent.termination != "aborted":
                text, images = self._follow_ups.pop(0)
                self._task = asyncio.create_task(self._run(text, images))
            elif self._follow_ups:
                self._notify()

    async def _maybe_auto_compact(self) -> None:
        """One automatic compaction; failures become a transcript notice."""
        if not should_auto_compact(
            self.runtime.guard.last_usage,
            self.runtime.agent.model.context_window,
        ):
            return
        self._compacting = True
        self._notify()
        try:
            await self.compact_now(force=True)
        except Exception as exc:  # noqa: BLE001 - never break the session
            self._notices.append(
                TranscriptNotice("info", f"auto-compaction failed: {exc}")
            )
        finally:
            self._compacting = False
            self._notify()

    # -- event → view models ------------------------------------------------

    def _on_event(self, event: AgentEvent) -> None:
        if event.type == "message_start" and event.message is not None:
            self._on_message_start(event.message)
        elif event.type == "message_update" and event.assistant_event is not None:
            self._on_assistant_event(event)
        elif event.type == "message_end" and isinstance(event.message, AssistantMessage):
            if self._current is not None:
                # Reasoning always ends before the message does, even when the
                # provider never sends thinking_end (fake streams, aborts).
                self._finish_thinking(self._current)
                # Canonicalize from the final message: some providers (and fake
                # streams) deliver the whole text in the terminal message only.
                self._current.text = _assistant_text(event.message)
                self._current.thinking = (
                    _assistant_thinking(event.message) or self._current.thinking
                )
                self._current = None
        elif event.type == "tool_execution_start":
            card = ToolCallView(
                call_id=event.tool_call_id or "",
                name=event.tool_name or "?",
                args_preview=args_preview(event.args or {}),
                args=event.args or {},
                status="running",
            )
            self._cards[card.call_id] = card
            self._transcript.append(card)
        elif event.type == "tool_execution_update":
            card = self._cards.get(event.tool_call_id or "")
            if (
                card is None
                or card.status != "running"
                or event.partial_result is None
            ):
                return
            card.progress = _progress_line(event.partial_result)
        elif event.type == "tool_execution_end":
            card = self._cards.get(event.tool_call_id or "")
            if card is not None:
                card.is_error = bool(event.is_error)
                card.status = "error" if event.is_error else "ok"
                card.result_text = _result_text(event.result)
                card.details = dict(getattr(event.result, "details", None) or {})
                card.duration_ms = event.duration_ms
        else:
            return
        self._notify()

    def _on_message_start(self, message) -> None:
        if isinstance(message, UserMessage):
            self._transcript.append(MessageView(kind="user", text=_user_text(message)))
        elif isinstance(message, AssistantMessage):
            self._current = MessageView(kind="assistant", text="")
            self._transcript.append(self._current)
        else:
            return

    def _on_assistant_event(self, event: AgentEvent) -> None:
        assistant_event = event.assistant_event
        if assistant_event is None:
            return
        kind = assistant_event.type
        if kind in ("thinking_start", "thinking_delta"):
            if self._current is not None:
                self._begin_thinking(self._current)
                if assistant_event.delta:
                    self._current.thinking = (
                        self._current.thinking or ""
                    ) + assistant_event.delta
        elif kind == "thinking_end":
            if self._current is not None:
                self._finish_thinking(self._current)
        elif kind in ("text_start", "text_delta"):
            if self._current is None:
                if not assistant_event.delta:
                    return
                self._current = MessageView(kind="assistant", text="")
                self._transcript.append(self._current)
            # The answer starting is what ends the reasoning block.
            self._finish_thinking(self._current)
            if assistant_event.delta:
                self._current.text += assistant_event.delta
        elif kind == "toolcall_end":
            # tool cards are appended on tool_execution_start (args complete there)
            return

    @staticmethod
    def _begin_thinking(view: MessageView) -> None:
        """Start the clock for a reasoning block (a turn may have several)."""
        if view.thinking_started_at is not None:
            return
        if view.thinking_seconds is None:
            view.thinking_seconds = 0.0
        view.thinking_started_at = time.monotonic()

    @staticmethod
    def _finish_thinking(view: MessageView) -> None:
        started = view.thinking_started_at
        if started is None:
            return
        view.thinking_seconds = (view.thinking_seconds or 0.0) + (
            time.monotonic() - started
        )
        view.thinking_started_at = None

    def _notify(self) -> None:
        self._on_change()


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(
        block.text for block in message.content if isinstance(block, TextContent) and block.text
    )


def _assistant_thinking(message: AssistantMessage) -> str | None:
    from zenagent.types import ThinkingContent

    text = "\n".join(
        block.thinking
        for block in message.content
        if isinstance(block, ThinkingContent) and block.thinking
    )
    return text or None


def _user_text(message: UserMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append(f"[image {block.mime_type}]")
    return "\n".join(parts)


def _progress_line(result) -> str:
    """Last non-empty streamed line, collapsed and bounded for a tool card."""
    content = getattr(result, "content", None) or []
    text = "\n".join(
        block.text for block in content if isinstance(block, TextContent)
    )
    lines = [
        "".join(ch if ch.isprintable() else " " for ch in line).strip()
        for line in text.splitlines()
    ]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    return lines[-1][:120]


def _result_text(result) -> str:
    content = getattr(result, "content", None) or []
    texts = [block.text for block in content if isinstance(block, TextContent)]
    return "\n".join(texts)
