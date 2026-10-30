"""ChatController: one run at a time, view-model state, follow-up queue."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from zenagent.agent.session import Session
from zenagent.app.runtime import Runtime
from zenagent.app.views import (
    MessageView,
    RunStatus,
    ToolCallView,
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
        self._follow_ups: list[str] = []
        self._task: asyncio.Task | None = None
        self._running = False
        #: Last finished turn: wall time and output tokens (status segments).
        self._turn_ms = 0
        self._turn_output = 0
        #: Provider failure from the last run, shown by the TUI's error banner.
        self._last_error = ""
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
            running=self._running,
            input_tokens=usage.input,
            output_tokens=usage.output,
            cache_read=usage.cache_read,
            cache_write=usage.cache_write,
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

    async def submit(self, text: str) -> None:
        """Start a run, or queue the text when one is already running."""
        if self._running:
            self.queue_follow_up(text)
            return
        self._task = asyncio.create_task(self._run(text))

    def queue_follow_up(self, text: str) -> None:
        if not text.strip():
            return
        self._follow_ups.append(text)
        self._notify()

    def clear_follow_ups(self) -> None:
        self._follow_ups.clear()
        self._notify()

    def steer(self, text: str) -> None:
        if text.strip():
            self.runtime.agent.steer(text)
            self._notify()

    def abort(self) -> None:
        self.runtime.agent.abort()
        self._notify()

    def clear_last_error(self) -> None:
        """Dismiss the provider-error banner (click on the dock row)."""
        if self._last_error:
            self._last_error = ""
            self._notify()

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
        self._notify()

    def retry(self) -> str | None:
        """Drop everything after the last user message and run that turn again.

        The user message is dropped too and re-sent, so the transcript never
        ends up with two copies of the same prompt.
        """
        if self._running:
            return None
        messages = list(self.runtime.agent.messages)
        index = next(
            (
                position
                for position in range(len(messages) - 1, -1, -1)
                if isinstance(messages[position], UserMessage)
            ),
            None,
        )
        if index is None or index == len(messages) - 1:
            return None  # nothing answered yet
        text = _user_text(messages[index])
        if not text.strip():
            return None
        truncated = messages[:index]
        self.runtime.agent.replace_messages(truncated)
        new_path = Session(Path(self.runtime.session.path)).rewrite(truncated)
        self.runtime.session = Session(new_path)
        self._transcript = messages_to_views(truncated)
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

    async def _run(self, text: str) -> None:
        self._running = True
        self._last_error = ""
        self._notify()
        started = time.monotonic()
        before_output = usage_total(self.runtime.agent.messages).output
        try:
            await self.runtime.agent.prompt(text)
        finally:
            self._running = False
            self._current = None
            self._task = None
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
            if self._follow_ups and self.runtime.agent.termination != "aborted":
                self._task = asyncio.create_task(self._run(self._follow_ups.pop(0)))
            elif self._follow_ups:
                self._notify()

    # -- event → view models ------------------------------------------------

    def _on_event(self, event: AgentEvent) -> None:
        if event.type == "message_start" and event.message is not None:
            self._on_message_start(event.message)
        elif event.type == "message_update" and event.assistant_event is not None:
            self._on_assistant_event(event)
        elif event.type == "message_end" and isinstance(event.message, AssistantMessage):
            if self._current is not None:
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
        if kind == "text_delta" and assistant_event.delta:
            if self._current is None:
                self._current = MessageView(kind="assistant", text="")
                self._transcript.append(self._current)
            self._current.text += assistant_event.delta
        elif kind in ("thinking_delta", "thinking_start") and assistant_event.delta:
            if self._current is not None:
                self._current.thinking = (self._current.thinking or "") + assistant_event.delta
        elif kind == "toolcall_end":
            # tool cards are appended on tool_execution_start (args complete there)
            return

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


def _result_text(result) -> str:
    content = getattr(result, "content", None) or []
    texts = [block.text for block in content if isinstance(block, TextContent)]
    return "\n".join(texts)
