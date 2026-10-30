"""UI-agnostic view models derived from the message transcript."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

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
    "args_preview",
    "messages_to_views",
    "usage_total",
]

_PREVIEW_LIMIT = 120


@dataclass
class MessageView:
    """A rendered user/assistant turn (text plus optional thinking)."""

    kind: Literal["user", "assistant"]
    text: str
    thinking: str | None = None


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
    cost: float = 0.0
    context_used: int = 0
    context_window: int = 0
    follow_ups: int = 0
    steering: int = 0
    session_name: str = ""
    cwd: str = ""
    mode: str = ""
    git_branch: str = ""


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
            views.append(
                MessageView(
                    kind="user",
                    text=message.content
                    if isinstance(message.content, str)
                    else _text_of(message.content),
                )
            )
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
