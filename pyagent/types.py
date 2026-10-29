"""Unified data model: messages, content blocks, usage, and tool definitions.

This module is the vocabulary of the whole project. Every layer speaks these
types; the JSON shapes produced by ``to_json`` are the on-disk session format
(one message per JSONL line), so serialization is lossless by contract.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

StopReason = Literal["pending", "stop", "length", "tool_use", "error", "aborted"]


@dataclass
class Cost:
    """Monetary cost of one model call, computed from catalog unit prices."""

    input: float = 0.0
    output: float = 0.0
    total: float = 0.0

    def to_json(self) -> dict:
        return {"input": self.input, "output": self.output, "total": self.total}


@dataclass
class Usage:
    """Token usage and cost for one model call."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int | None = None
    cost: Cost = field(default_factory=Cost)

    def to_json(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "reasoning": self.reasoning,
            "cost": self.cost.to_json(),
        }


@dataclass
class TextContent:
    text: str

    def to_json(self) -> dict:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingContent:
    """Model reasoning; ``signature`` round-trips for thinking-block providers."""

    thinking: str
    signature: str | None = None
    redacted: bool = False

    def to_json(self) -> dict:
        return {
            "type": "thinking",
            "thinking": self.thinking,
            "signature": self.signature,
            "redacted": self.redacted,
        }


@dataclass
class ImageContent:
    data: str
    mime_type: str

    def to_json(self) -> dict:
        return {"type": "image", "data": self.data, "mime_type": self.mime_type}


@dataclass
class ToolCall:
    """A tool invocation requested by the model.

    When the model emits arguments that are not valid JSON, adapters degrade
    gracefully to empty ``arguments`` plus a human-readable ``arguments_error``
    instead of crashing the loop.
    """

    id: str
    name: str
    arguments: dict
    arguments_error: str | None = None

    def to_json(self) -> dict:
        return {
            "type": "toolCall",
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
            "arguments_error": self.arguments_error,
        }


ContentBlock = TextContent | ThinkingContent | ImageContent | ToolCall


@dataclass
class SystemMessage:
    content: str
    timestamp: int

    def to_json(self) -> dict:
        return {
            "role": "system",
            "content": self.content,
            "timestamp": self.timestamp,
        }


@dataclass
class UserMessage:
    content: str | list[TextContent | ImageContent]
    timestamp: int

    def to_json(self) -> dict:
        if isinstance(self.content, str):
            content: str | list[dict] = self.content
        else:
            content = [block.to_json() for block in self.content]
        return {"role": "user", "content": content, "timestamp": self.timestamp}


@dataclass
class AssistantMessage:
    content: list[TextContent | ThinkingContent | ToolCall]
    provider: str
    model: str
    usage: Usage
    stop_reason: StopReason
    timestamp: int
    error_message: str | None = None
    response_id: str | None = None
    duration_ms: int | None = None

    def to_json(self) -> dict:
        return {
            "role": "assistant",
            "content": [block.to_json() for block in self.content],
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage.to_json(),
            "stop_reason": self.stop_reason,
            "timestamp": self.timestamp,
            "error_message": self.error_message,
            "response_id": self.response_id,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    is_error: bool
    timestamp: int

    def to_json(self) -> dict:
        return {
            "role": "toolResult",
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "content": [block.to_json() for block in self.content],
            "is_error": self.is_error,
            "timestamp": self.timestamp,
        }


Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage


@dataclass
class ToolDef:
    """A tool exposed to the model; ``parameters`` is a raw JSON Schema dict."""

    name: str
    description: str
    parameters: dict

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _require(d: dict, key: str, ctx: str) -> Any:
    if key not in d:
        raise ValueError(f"{ctx} is missing required key {key!r}")
    return d[key]


# Note: deserialization deliberately raises ValueError (not TypeError) for all
# malformed input — that is the message_from_json error contract.


def _block_from_json(d: Any) -> ContentBlock:
    if not isinstance(d, dict):
        raise ValueError(  # noqa: TRY004 - ValueError is the deserialization contract
            f"content block must be a dict, got {type(d).__name__}"
        )
    block_type = d.get("type")
    if block_type == "text":
        return TextContent(text=_require(d, "text", "text block"))
    if block_type == "thinking":
        return ThinkingContent(
            thinking=_require(d, "thinking", "thinking block"),
            signature=d.get("signature"),
            redacted=d.get("redacted", False),
        )
    if block_type == "image":
        return ImageContent(
            data=_require(d, "data", "image block"),
            mime_type=_require(d, "mime_type", "image block"),
        )
    if block_type == "toolCall":
        return ToolCall(
            id=_require(d, "id", "toolCall block"),
            name=_require(d, "name", "toolCall block"),
            arguments=_require(d, "arguments", "toolCall block"),
            arguments_error=d.get("arguments_error"),
        )
    raise ValueError(f"unknown content block type: {block_type!r}")


def _blocks_from_json(value: Any, ctx: str) -> list[ContentBlock]:
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004 - ValueError is the deserialization contract
            f"{ctx} must be a list of content blocks, got {type(value).__name__}"
        )
    return [_block_from_json(item) for item in value]


def _usage_from_json(d: Any) -> Usage:
    if not isinstance(d, dict):
        raise ValueError(  # noqa: TRY004 - ValueError is the deserialization contract
            f"usage must be a dict, got {type(d).__name__}"
        )
    cost = d.get("cost")
    if not isinstance(cost, dict):
        cost = {}
    return Usage(
        input=d.get("input", 0),
        output=d.get("output", 0),
        cache_read=d.get("cache_read", 0),
        cache_write=d.get("cache_write", 0),
        reasoning=d.get("reasoning"),
        cost=Cost(
            input=cost.get("input", 0.0),
            output=cost.get("output", 0.0),
            total=cost.get("total", 0.0),
        ),
    )


def message_from_json(d: dict) -> Message:
    """Deserialize a message dict produced by ``to_json`` back into a Message.

    Dispatches on the ``role`` key. Raises ValueError on an unknown role or
    malformed structure with a message naming what is wrong.
    """
    if not isinstance(d, dict):
        raise ValueError(  # noqa: TRY004 - ValueError is the deserialization contract
            f"message must be a dict, got {type(d).__name__}"
        )
    role = d.get("role")
    if role == "system":
        content = _require(d, "content", "system message")
        if not isinstance(content, str):
            raise ValueError(
                f"system message content must be a str, got {type(content).__name__}"
            )
        return SystemMessage(
            content=content,
            timestamp=_require(d, "timestamp", "system message"),
        )
    if role == "user":
        content = _require(d, "content", "user message")
        if not isinstance(content, (str, list)):
            raise ValueError(
                "user message content must be a str or a list of content blocks, "
                f"got {type(content).__name__}"
            )
        if isinstance(content, list):
            content = _blocks_from_json(content, "user message content")
        return UserMessage(
            content=content,
            timestamp=_require(d, "timestamp", "user message"),
        )
    if role == "assistant":
        return AssistantMessage(
            content=_blocks_from_json(
                _require(d, "content", "assistant message"), "assistant message content"
            ),
            provider=_require(d, "provider", "assistant message"),
            model=_require(d, "model", "assistant message"),
            usage=_usage_from_json(_require(d, "usage", "assistant message")),
            stop_reason=_require(d, "stop_reason", "assistant message"),
            timestamp=_require(d, "timestamp", "assistant message"),
            error_message=d.get("error_message"),
            response_id=d.get("response_id"),
            duration_ms=d.get("duration_ms"),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=_require(d, "tool_call_id", "toolResult message"),
            tool_name=_require(d, "tool_name", "toolResult message"),
            content=_blocks_from_json(
                _require(d, "content", "toolResult message"),
                "toolResult message content",
            ),
            is_error=_require(d, "is_error", "toolResult message"),
            timestamp=_require(d, "timestamp", "toolResult message"),
        )
    if role is None:
        raise ValueError("message is missing 'role' key")
    raise ValueError(f"unknown message role: {role!r}")
