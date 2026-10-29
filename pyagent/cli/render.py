"""Pure rendering: agent events in, rich renderables out.

Nothing here touches a terminal, so it is unit-testable: :class:`AssistantLiveRenderer`
turns the streaming events of one assistant message into a rich renderable, and
the helper functions turn a tool result / progress update / session into one
line of text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from pyagent.agent.loop import ToolResultAgent
from pyagent.agent.session import Session
from pyagent.events import AssistantEvent
from pyagent.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)

__all__ = [
    "AssistantLiveRenderer",
    "LiveUpdate",
    "aggregate_usage",
    "context_percent",
    "render_diff",
    "session_header",
    "status_line",
    "tool_progress_line",
    "tool_result_line",
]

#: Characters of tool output kept in a one-line preview.
_PREVIEW_CHARS = 100


@dataclass
class LiveUpdate:
    """What to draw now, and whether the message is finished."""

    renderable: RenderableType | None
    finished: bool


@dataclass
class AssistantLiveRenderer:
    """Accumulates one streamed assistant message into a live renderable.

    The adapter guarantees every event carries a complete ``partial`` snapshot,
    so the renderer just reads that snapshot instead of stitching deltas itself.
    """

    thinking: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_tokens: int | None = None

    def feed(self, event: AssistantEvent) -> LiveUpdate:
        partial: AssistantMessage = event.partial
        self.thinking = "".join(
            block.thinking for block in partial.content if isinstance(block, ThinkingContent)
        )
        self.text = "".join(
            block.text for block in partial.content if isinstance(block, TextContent)
        )
        self.tool_calls = [b for b in partial.content if isinstance(b, ToolCall)]
        self.reasoning_tokens = partial.usage.reasoning
        finished = event.type in ("done", "error")
        return LiveUpdate(renderable=self.render(), finished=finished)

    def render(self) -> RenderableType:
        parts: list[RenderableType] = []
        if self.thinking:
            tokens = self.reasoning_tokens
            if tokens is None:
                tokens = max(1, len(self.thinking) // 4)  # cheap estimate
            parts.append(
                Panel(
                    Text(self.thinking, style="grey62"),
                    title=f"thinking · ~{tokens} tokens",
                    border_style="grey35",
                    padding=(0, 1),
                )
            )
        if self.text:
            parts.append(Markdown(self.text))
        for call in self.tool_calls:
            parts.append(Text(f"⚙ {call.name}({_brief(call.arguments)})", style="cyan"))
        return Group(*parts) if len(parts) > 1 else parts[0] if parts else Text("")


def _brief(arguments: dict, limit: int = 60) -> str:
    rendered = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _sanitize(text: str) -> str:
    """Collapse to a single printable line (no ANSI, no control characters)."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    return cleaned.strip()


def _first_line(result: ToolResultAgent) -> str:
    for block in result.content:
        if isinstance(block, TextContent) and block.text.strip():
            return _sanitize(block.text.strip().splitlines()[0])
    return ""


def tool_result_line(
    tool_name: str, result: ToolResultAgent, duration_ms: int | None = None
) -> str:
    """One-line summary of a finished tool call."""
    mark = "✗" if result.is_error else "✓"
    timing = f" {duration_ms}ms" if duration_ms is not None else ""
    preview = _first_line(result)
    if len(preview) > _PREVIEW_CHARS:
        preview = preview[: _PREVIEW_CHARS - 1] + "…"
    return f"{mark} {tool_name}{timing}  {preview}".rstrip()


def tool_progress_line(tool_name: str, partial: ToolResultAgent) -> str:
    """One-line, overwrite-in-place progress for a running tool."""
    text = "".join(
        block.text for block in partial.content if isinstance(block, TextContent)
    )
    lines = text.splitlines()
    last = _sanitize(lines[-1]) if lines else ""
    if len(last) > _PREVIEW_CHARS:
        last = last[: _PREVIEW_CHARS - 1] + "…"
    return f"⚙ {tool_name} ({len(text)} bytes) {last}".rstrip()


def aggregate_usage(messages: list) -> Usage:
    """Sum the usage of every assistant message (the run's bill)."""
    total = Usage()
    for message in messages:
        if isinstance(message, AssistantMessage):
            total.input += message.usage.input
            total.output += message.usage.output
            total.cache_read += message.usage.cache_read
            total.cache_write += message.usage.cache_write
            total.cost.input += message.usage.cost.input
            total.cost.output += message.usage.cost.output
            total.cost.cache_read += message.usage.cost.cache_read
            total.cost.cache_write += message.usage.cost.cache_write
            total.cost.total += message.usage.cost.total
    return total


def _human_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def context_percent(usage: Usage, context_window: int) -> int | None:
    """How full the context is, as a rounded percentage (None without a window)."""
    if context_window <= 0:
        return None
    used = usage.input + usage.cache_read
    return round(100 * used / context_window)


def status_line(
    *,
    model_id: str,
    thinking: str | None,
    messages: int,
    usage: Usage,
    context_window: int,
    session_name: str | None = None,
) -> str:
    """One line of run state: model, level, size, context fill, spend."""
    used = usage.input + usage.cache_read
    context = f"ctx {_human_tokens(used)}/{_human_tokens(context_window)}"
    percent = context_percent(usage, context_window)
    if percent is not None:
        context += f" ({percent}%)"
    parts = [
        model_id,
        f"thinking {thinking or 'off'}",
        f"{messages} msgs",
        context,
        f"${usage.cost.total:.4f}",
    ]
    if session_name:
        parts.append(session_name)
    return " · ".join(parts)


def render_diff(diff: str) -> RenderableType:
    """Colour a unified diff for the terminal (+ green, - red, @@ cyan)."""
    text = Text()
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            style = "bold"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        else:
            style = "grey62"
        text.append(line + "\n", style=style)
    return text


def session_header(session: Session) -> str:
    """A one-line header for the current session."""
    count = len(session.messages)
    return f"session {session.path.name} · {count} messages"
