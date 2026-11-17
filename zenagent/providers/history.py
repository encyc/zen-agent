"""Transcript projections for provider requests.

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

Images need two more projections. ``downgrade_unsupported_images`` gives a
text-only model explicit placeholders where it cannot see an image (silently
dropping the block makes a tool result look empty, and some providers reject
empty tool content). ``split_error_tool_result_images`` lifts an error result's
images out, because Anthropic rejects an error ``tool_result`` carrying an
image even though a successful one may include it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from zenagent.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

__all__ = [
    "EMPTY_TOOL_OUTPUT",
    "IMAGE_ONLY_TOOL_OUTPUT",
    "SplitErrorToolResult",
    "downgrade_unsupported_images",
    "replayable",
    "split_error_tool_result_images",
    "tool_image_caption",
]

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


#: Text that stands in for an image the model cannot see.
NON_VISION_USER_IMAGE = "(image omitted: model does not support images)"
NON_VISION_TOOL_IMAGE = "(tool image omitted: model does not support images)"

#: Wire placeholders for a tool result that carries no text of its own.
IMAGE_ONLY_TOOL_OUTPUT = "(see attached image)"
EMPTY_TOOL_OUTPUT = "(no tool output)"

#: Stands in for an error result's image; the image itself follows the results.
ERROR_TOOL_IMAGE = "(image attached after the tool results)"

#: Sentence that introduces tool images sent beside the results.
_IMAGE_NOTE = "{noun} returned by tool {tool} (call {call_id}):"


def tool_image_caption(tool_name: str, tool_call_id: str, count: int) -> str:
    """The line that ties hoisted tool images back to their call."""
    noun = "Image" if count == 1 else f"{count} images"
    return _IMAGE_NOTE.format(noun=noun, tool=tool_name, call_id=tool_call_id)


def _replace_images_with_placeholder(
    content: list[TextContent | ImageContent], placeholder: str
) -> list[TextContent | ImageContent]:
    """Replace each run of images with one placeholder (pi-ai's rule).

    A run of consecutive images collapses to a single placeholder, and an
    adjacent text block that already is the placeholder is not repeated.
    """
    result: list[TextContent | ImageContent] = []
    previous_was_placeholder = False
    for block in content:
        if isinstance(block, ImageContent):
            if not previous_was_placeholder:
                result.append(TextContent(text=placeholder))
            previous_was_placeholder = True
            continue
        result.append(block)
        previous_was_placeholder = block.text == placeholder
    return result


def downgrade_unsupported_images(
    messages: list[Message], *, supports_vision: bool
) -> list[Message]:
    """Replace images with explicit text on a model that cannot see them.

    Silently dropping the blocks makes a tool result look empty (and some
    providers reject empty tool content entirely), so a text-only model is told
    what it is missing. Vision models get the messages back untouched, with
    the same objects.
    """
    if supports_vision:
        return messages
    downgraded: list[Message] = []
    for message in messages:
        if isinstance(message, UserMessage) and isinstance(message.content, list):
            if any(isinstance(block, ImageContent) for block in message.content):
                downgraded.append(
                    replace(
                        message,
                        content=_replace_images_with_placeholder(
                            message.content, NON_VISION_USER_IMAGE
                        ),
                    )
                )
                continue
        elif isinstance(message, ToolResultMessage) and any(
            isinstance(block, ImageContent) for block in message.content
        ):
            downgraded.append(
                replace(
                    message,
                    content=_replace_images_with_placeholder(
                        message.content, NON_VISION_TOOL_IMAGE
                    ),
                )
            )
            continue
        downgraded.append(message)
    return downgraded


@dataclass
class SplitErrorToolResult:
    """A tool result whose images must travel after the tool-result blocks."""

    content: list[TextContent | ImageContent]
    caption: str
    images: list[ImageContent]


def split_error_tool_result_images(
    message: ToolResultMessage,
) -> SplitErrorToolResult | None:
    """Split an error result's images out, for APIs that allow text only there.

    Anthropic rejects an error ``tool_result`` that carries an image, but still
    lets the model see it when the image follows the tool results of the turn.
    Returns ``None`` for success results and for errors without images.
    """
    if not message.is_error:
        return None
    images = [block for block in message.content if isinstance(block, ImageContent)]
    if not images:
        return None
    return SplitErrorToolResult(
        content=_replace_images_with_placeholder(message.content, ERROR_TOOL_IMAGE),
        caption=(
            f"Images from the error result of tool call "
            f"{message.tool_call_id} ({message.tool_name}):"
        ),
        images=images,
    )
