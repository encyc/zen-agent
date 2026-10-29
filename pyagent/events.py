"""Runtime event protocol: AssistantEvent, AgentEvent, and AbortToken.

Two event families, each a single dataclass discriminated by its ``type``
field (consumers ``match event.type`` — no union of small dataclasses, no
pydantic). Events are runtime-only: there is deliberately no serialization.

The core trick: every ``AssistantEvent`` carries ``partial``, the complete
assistant-message snapshot assembled so far. Delta assembly belongs entirely
to the adapter (done once); the loop and UI always receive ready snapshots.

AbortToken implements cooperative cancellation: aborting is an in-protocol
state, not an exception.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from pyagent.types import AssistantMessage, Message, StopReason, ToolCall


@dataclass
class AssistantEvent:
    """Streaming event emitted by a provider adapter.

    Lifecycle: ``start → (text|thinking|toolcall)_(start|delta|end)* → done | error``.
    """

    type: Literal[
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
        "error",
    ]
    partial: AssistantMessage
    content_index: int | None = None
    delta: str | None = None
    content: str | None = None
    tool_call: ToolCall | None = None
    reason: StopReason | None = None


@dataclass
class AgentEvent:
    """UI-facing event emitted by the agent loop."""

    type: Literal[
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
    ]
    message: Message | None = None
    assistant_event: AssistantEvent | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    args: Any | None = None
    result: Any | None = None
    partial_result: Any | None = None
    is_error: bool = False
    duration_ms: int | None = None
    new_messages: list[Message] | None = None


class Aborted(Exception):
    """Cooperative cancellation, raised inside the transport layer.

    Deliberately NOT ``asyncio.CancelledError``: that is a ``BaseException``
    and means "this task is being cancelled", so fabricating it would break
    task supervision. Cancellation is an in-protocol state here, so the
    transport raises this ordinary exception and ``safe_stream`` turns it
    into a terminal ``error(reason="aborted")`` event.
    """


class AbortToken:
    """Cooperative cancellation signal shared by the loop, adapters, and tools.

    ``wait()`` on an already-aborted token returns immediately.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def abort(self) -> None:
        self._event.set()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()
