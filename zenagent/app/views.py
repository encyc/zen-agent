"""UI-agnostic view models derived from the message transcript."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from zenagent.agent.loop import LOOP_GUARD_PREFIX
from zenagent.app.context import ContextReport
from zenagent.app.skill_commands import parse_skill_display
from zenagent.events import AgentEvent
from zenagent.types import (
    AssistantMessage,
    Cost,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

__all__ = [
    "JobLogView",
    "JobStatusView",
    "JobView",
    "MessageView",
    "RunStatus",
    "SubagentView",
    "ToolCallView",
    "TranscriptBuilder",
    "TranscriptNotice",
    "args_preview",
    "job_views",
    "messages_to_views",
    "sort_jobs",
    "usage_total",
    "user_view",
]

_PREVIEW_LIMIT = 120


@dataclass
class MessageView:
    """A rendered user/assistant/skill turn (text plus optional thinking)."""

    kind: Literal["user", "assistant", "skill"]
    text: str
    #: For ``kind == "skill"``: the user's arguments and the injected body.
    skill_args: str | None = None
    skill_body: str | None = None
    thinking: str | None = None
    #: ``time.monotonic()`` when the current reasoning block started; None when
    #: the model is not reasoning (history replay has no timing at all).
    thinking_started_at: float | None = None
    #: Finished reasoning seconds for this turn; None when timing is unknown.
    thinking_seconds: float | None = None


def user_view(text: str) -> MessageView:
    """A user turn, or the compact view of an injected ``/skill:`` message."""
    display = parse_skill_display(text)
    if display is not None:
        return MessageView(
            kind="skill",
            text=display.name,
            skill_args=display.args,
            skill_body=display.body,
        )
    return MessageView(kind="user", text=text)


@dataclass
class ToolCallView:
    """A tool call card; history lacks duration/diff, live events fill them."""

    call_id: str
    name: str
    args_preview: str
    args: dict = field(default_factory=dict)
    #: Structured tool-result data for UI rendering only (never persisted or sent
    #: to the model); empty on history replay, which keeps cards generic.
    details: dict = field(default_factory=dict)
    status: Literal["running", "ok", "error"] = "running"
    #: Last non-empty streamed output line while the tool is running
    #: (``tool_execution_update``); empty when the tool emits no progress.
    progress: str = ""
    result_text: str = ""
    is_error: bool = False
    duration_ms: int | None = None
    diff: str | None = None
    #: ``time.monotonic()`` when the call started (live only; history replay has
    #: no timing). The sidebar derives a subagent's live elapsed from it.
    started_at: float | None = None


@dataclass
class RunStatus:
    """Snapshot the status bar renders."""

    model_id: str
    thinking: str | None
    running: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    #: Cache-read share of prompt tokens, normalized per provider dialect by
    #: ``app.support.cache_hit_ratio``; None when nothing was cached.
    cache_hit_ratio: float | None = None
    cost: float = 0.0
    context_used: int = 0
    context_window: int = 0
    follow_ups: int = 0
    steering: int = 0
    #: Wall-clock time of the most recent turn and the output tokens it produced;
    #: both 0 before the first finished turn (the segments hide themselves).
    turn_ms: int = 0
    turn_output_tokens: int = 0
    #: Provider failure from the most recent run; the TUI keeps this on screen
    #: until it is dismissed or the next run starts.
    last_error: str = ""
    session_name: str = ""
    cwd: str = ""
    mode: str = ""
    git_branch: str = ""
    #: Current todo list as the tool's canonical phase details (``todo``
    #: ``details["phases"]`` / the ``<session>.todo.json`` sidecar); the TUI HUD
    #: renders it and hides itself while it is empty. Plain dicts keep this
    #: UI-agnostic module free of tool imports.
    todos: list[dict] = field(default_factory=list)
    #: Spend/usage attributed to ``task`` subagents this session (kept apart
    #: from the parent's own cost).
    subagent_cost: float = 0.0
    subagent_tokens: int = 0
    #: Background jobs (``bash async``) known to the session; running ones first,
    #: then newest finished. The sidebar caps the rendered rows.
    jobs: list[JobView] = field(default_factory=list)
    #: ``task`` subagents: the live one plus recent persisted ones, newest first.
    subagents: list[SubagentView] = field(default_factory=list)
    #: ``call_id`` of the subagent currently running, when there is one.
    active_subagent: str | None = None
    #: Context-window composition (measured total, estimated slices).
    context: ContextReport | None = None


#: Job states the sidebar renders; mirrors ``zenagent.tools.jobs.JobStatus``.
JobStatusView = Literal["running", "ok", "failed", "timed_out"]


@dataclass(frozen=True)
class JobView:
    """One background job as the sidebar renders it."""

    id: str
    command: str
    status: JobStatusView
    #: ``time.time()`` epoch seconds; the UI derives live elapsed itself.
    started_at: float
    duration_ms: int | None
    exit_code: int | None


@dataclass(frozen=True)
class JobLogView:
    """A background job's bounded log, for the read-only overlay."""

    id: str
    command: str
    status: str
    duration_ms: int | None
    exit_code: int | None
    output: str
    truncated: bool = False


@dataclass(frozen=True)
class SubagentView:
    """One ``task`` subagent: a live card or a persisted transcript's meta."""

    call_id: str
    name: str
    status: Literal["running", "ok", "error", "aborted", "truncated", "interrupted"]
    duration_ms: int
    cost: float
    tokens: int
    #: False for replayed/missing transcripts: the sidebar row is not clickable.
    has_transcript: bool = False


@dataclass(frozen=True)
class TranscriptNotice:
    """An out-of-band line a controller wants rendered (divider, info notice)."""

    kind: Literal["divider", "info"]
    text: str


def args_preview(arguments: dict) -> str:
    """Compact one-line argument summary for tool cards."""
    text = json.dumps(arguments, ensure_ascii=False)
    if len(text) > _PREVIEW_LIMIT:
        return text[: _PREVIEW_LIMIT - 3] + "..."
    return text


def _text_of(content) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append(f"[image {block.mime_type}]")
    return "\n".join(part for part in parts if part)


def messages_to_views(messages: list[Message]) -> list[MessageView | ToolCallView]:
    """Convert stored messages into view models, pairing calls with results."""
    views: list[MessageView | ToolCallView] = []
    cards: dict[str, ToolCallView] = {}

    for message in messages:
        if isinstance(message, UserMessage):
            text = (
                message.content
                if isinstance(message.content, str)
                else _text_of(message.content)
            )
            if text.startswith(LOOP_GUARD_PREFIX):
                continue  # synthetic model-only corrective, not a user turn
            views.append(user_view(text))
        elif isinstance(message, AssistantMessage):
            text = _text_of(message.content)
            thinking = "\n".join(
                block.thinking
                for block in message.content
                if isinstance(block, ThinkingContent) and block.thinking
            )
            if text or thinking:
                views.append(
                    MessageView(
                        kind="assistant",
                        text=text,
                        thinking=thinking or None,
                    )
                )
            for block in message.content:
                if not isinstance(block, ToolCall):
                    continue
                card = ToolCallView(
                    call_id=block.id,
                    name=block.name,
                    args_preview=args_preview(block.arguments),
                    args=block.arguments,
                )
                cards[block.id] = card
                views.append(card)
        elif isinstance(message, ToolResultMessage):
            card = cards.get(message.tool_call_id)
            if card is None:
                continue  # orphan result from a compacted/torn history
            card.result_text = _text_of(message.content)
            card.is_error = message.is_error
            card.status = "error" if message.is_error else "ok"

    return views


def job_views(jobs: Iterable[object]) -> list[JobView]:
    """Map ``JobManager`` entries to UI-neutral views (duck-typed)."""
    return [
        JobView(
            id=str(job.id),
            command=str(job.command),
            status=job.status,
            started_at=float(job.started_at),
            duration_ms=job.duration_ms,
            exit_code=job.exit_code,
        )
        for job in jobs
    ]


def sort_jobs(views: list[JobView]) -> list[JobView]:
    """Running jobs first, then the most recently started finished ones."""
    return sorted(views, key=lambda view: (view.status != "running", -view.started_at))


def usage_total(messages: list[Message]) -> Usage:
    """Sum usage across every assistant message in the transcript."""
    total = Usage()
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        usage = message.usage
        total.input += usage.input
        total.output += usage.output
        total.cache_read += usage.cache_read
        total.cache_write += usage.cache_write
        total.cost = Cost(
            input=total.cost.input + usage.cost.input,
            output=total.cost.output + usage.cost.output,
            cache_read=total.cost.cache_read + usage.cost.cache_read,
            cache_write=total.cost.cache_write + usage.cost.cache_write,
            total=total.cost.total + usage.cost.total,
        )
    return total


class TranscriptBuilder:
    """``AgentEvent`` → ``MessageView`` / ``ToolCallView``.

    The parent transcript and a subagent's transcript are the same shape, so
    the event→view mapping lives here once. A frontend repaints when
    :meth:`apply` returns True; ``current`` is the live assistant row until
    ``message_end`` (or :meth:`finalize` when a run dies mid-stream).
    """

    def __init__(self) -> None:
        self.views: list[MessageView | ToolCallView] = []
        self.cards: dict[str, ToolCallView] = {}
        self.current: MessageView | None = None

    # -- lifecycle ----------------------------------------------------------

    def reset(self) -> None:
        self.views.clear()
        self.cards.clear()
        self.current = None

    def load(self, messages: list[Message]) -> None:
        """Start over from a stored transcript (session switch / resume)."""
        self.views = messages_to_views(messages)
        self.cards = {
            view.call_id: view for view in self.views if isinstance(view, ToolCallView)
        }
        self.current = None

    def finalize(self) -> None:
        """Close a live row left behind by an abort or provider error."""
        if self.current is not None:
            self._finish_thinking(self.current)
            self.current = None

    # -- events -------------------------------------------------------------

    def apply(self, event: AgentEvent) -> bool:
        """Apply one agent event; True when the transcript visibly changed."""
        if event.type == "message_start" and event.message is not None:
            self._message_start(event.message)
        elif event.type == "message_update" and event.assistant_event is not None:
            self._assistant_event(event.assistant_event)
        elif event.type == "message_end" and isinstance(event.message, AssistantMessage):
            self._message_end(event.message)
        elif event.type == "tool_execution_start":
            card = ToolCallView(
                call_id=event.tool_call_id or "",
                name=event.tool_name or "?",
                args_preview=args_preview(event.args or {}),
                args=event.args or {},
                status="running",
                started_at=time.monotonic(),
            )
            self.cards[card.call_id] = card
            self.views.append(card)
        elif event.type == "tool_execution_update":
            card = self.cards.get(event.tool_call_id or "")
            if (
                card is None
                or card.status != "running"
                or event.partial_result is None
            ):
                return False
            card.progress = _progress_line(event.partial_result)
        elif event.type == "tool_execution_end":
            card = self.cards.get(event.tool_call_id or "")
            details = dict(getattr(event.result, "details", None) or {})
            if card is not None:
                card.is_error = bool(event.is_error)
                card.status = "error" if event.is_error else "ok"
                card.result_text = _result_text(event.result)
                card.details = details
                card.duration_ms = event.duration_ms
        else:
            return False
        return True

    # -- internals ----------------------------------------------------------

    def _message_start(self, message) -> None:
        if isinstance(message, UserMessage):
            self.views.append(user_view(_user_text(message)))
        elif isinstance(message, AssistantMessage):
            self.current = MessageView(kind="assistant", text="")
            self.views.append(self.current)

    def _message_end(self, message: AssistantMessage) -> None:
        if self.current is None:
            return
        # Reasoning always ends before the message does, even when the provider
        # never sends thinking_end (fake streams, aborts).
        self._finish_thinking(self.current)
        # Canonicalize from the final message: some providers (and fake streams)
        # deliver the whole text in the terminal message only.
        self.current.text = _assistant_text(message)
        self.current.thinking = _assistant_thinking(message) or self.current.thinking
        self.current = None

    def _assistant_event(self, assistant_event) -> None:
        kind = assistant_event.type
        if kind in ("thinking_start", "thinking_delta"):
            if self.current is not None:
                self._begin_thinking(self.current)
                if assistant_event.delta:
                    self.current.thinking = (
                        self.current.thinking or ""
                    ) + assistant_event.delta
        elif kind == "thinking_end":
            if self.current is not None:
                self._finish_thinking(self.current)
        elif kind in ("text_start", "text_delta"):
            if self.current is None:
                if not assistant_event.delta:
                    return
                self.current = MessageView(kind="assistant", text="")
                self.views.append(self.current)
            # The answer starting is what ends the reasoning block.
            self._finish_thinking(self.current)
            if assistant_event.delta:
                self.current.text += assistant_event.delta
        # toolcall_end: cards are appended on tool_execution_start instead.

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


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(
        block.text
        for block in message.content
        if isinstance(block, TextContent) and block.text
    )


def _assistant_thinking(message: AssistantMessage) -> str | None:
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
    text = "\n".join(block.text for block in content if isinstance(block, TextContent))
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
