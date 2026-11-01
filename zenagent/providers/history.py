"""Replay-safe view of the transcript for provider requests.

The durable transcript deliberately keeps every turn the UI showed, including
turns that failed (``stop_reason="error"``) and turns the user aborted
mid-stream. Providers do not accept those turns back verbatim:

* a strict chat-completions endpoint rejects an assistant message that has
  neither text nor tool calls (DeepSeek answers ``400 Invalid assistant
  message: content or tool_calls must be set``) — an aborted turn that only
  produced thinking, or an error turn with empty content, is exactly that;
* every tool-calling API rejects an assistant ``tool_calls`` entry whose
  results are missing from the transcript — an abort mid-tool-call leaves one
  behind, and an aborted turn's tool calls were never executed;
* Anthropic rejects an assistant turn that ends up with no content blocks.

``replayable`` projects the stored messages onto the subset that is legal to
send. It never mutates the transcript: a turn that needs blocks removed is
copied with :func:`dataclasses.replace`, and turns that cannot be repaired are
dropped from the returned list only. A conversation with no failed/aborted
turns and no dangling calls is returned unchanged (same message objects), so
the common path costs one pass and no copies.
"""

from __future__ import annotations

from dataclasses import replace

from zenagent.types import (
    AssistantMessage,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)

__all__ = ["replayable"]

#: Stop reasons of turns that never completed: their partial thinking is not a
#: finished block, and their tool calls were never executed.
_FAILED_STOP_REASONS = frozenset({"pending", "error", "aborted"})


def _replay_content(
    message: AssistantMessage,
    index: int,
    result_at: dict[str, int],
) -> list[TextContent | ThinkingContent | ToolCall]:
    """The blocks of ``message`` that may be replayed (possibly empty)."""
    failed = message.stop_reason in _FAILED_STOP_REASONS
    blocks: list[TextContent | ThinkingContent | ToolCall] = []
    for block in message.content:
        if isinstance(block, TextContent):
            blocks.append(block)
        elif isinstance(block, ToolCall):
            # A call is replayable only when its result follows it in the
            # transcript; an unanswered call makes the whole request invalid.
            if not failed and result_at.get(block.id, -1) > index:
                blocks.append(block)
        elif isinstance(block, ThinkingContent) and not failed:
            # Partial reasoning from a failed turn is not a finished block.
            blocks.append(block)
    return blocks


def replayable(messages: list[Message]) -> list[Message]:
    """Return the messages of ``messages`` that a provider may be sent.

    Failed/aborted assistant turns are reduced to their text (and dropped when
    they have none); tool calls without a following result are dropped
    together with any result that lost its call; assistant turns left with no
    blocks are dropped. Everything else keeps its identity and order.
    """
    # First result position per call id: a call is answered only by a result
    # that comes after it, so a corrupt ordering cannot resurrect a dangling
    # call.
    result_at: dict[str, int] = {}
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage):
            result_at.setdefault(message.tool_call_id, index)

    kept: list[Message] = []
    kept_call_ids: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage):
            if message.tool_call_id in kept_call_ids:
                kept.append(message)
            continue
        if isinstance(message, AssistantMessage):
            content = _replay_content(message, index, result_at)
            if not content:
                continue
            kept_call_ids.update(
                block.id for block in content if isinstance(block, ToolCall)
            )
            if len(content) != len(message.content):
                message = replace(message, content=content)
            kept.append(message)
            continue
        kept.append(message)
    return kept
