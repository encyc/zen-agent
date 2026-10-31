"""UI-agnostic view models derived from the message transcript."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from zenagent.agent.loop import LOOP_GUARD_PREFIX
from zenagent.app.skill_commands import parse_skill_display
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
    "MessageView",
    "RunStatus",
    "ToolCallView",
    "TranscriptNotice",
    "args_preview",
    "messages_to_views",
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
