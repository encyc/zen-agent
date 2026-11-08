"""The wire codec: event serialization and the protocol version.

Pure relocation from the pre-split ``cli/rpc.py``.
"""

from __future__ import annotations

import json

from zenagent.agent import Agent
from zenagent.app.skill_commands import (
    parse_skill_display,
)
from zenagent.app.views import usage_total
from zenagent.events import AgentEvent
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

PROTOCOL_VERSION = 2

_PARTIAL_RESULT_CHARS = 4000
#: Bound for one JSON-encoded tool argument blob in an event.
_ARGS_CHARS = 4000
#: Default approval timeout, matching the TUI modal.
_APPROVAL_TIMEOUT_S = 120.0

def _text_of(result) -> str:
    return "".join(
        block.text
        for block in getattr(result, "content", [])
        if isinstance(block, TextContent)
    )


def _message_text(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return _text_of(message)


def _message_projection(message) -> dict:
    """A bounded, client-friendly view of one message (never the raw object)."""
    if isinstance(message, AssistantMessage):
        role = "assistant"
    elif isinstance(message, ToolResultMessage):
        role = "toolResult"
    elif isinstance(message, UserMessage):
        role = "user"
    else:
        role = "message"
    if role == "user":
        # An injected /skill: turn is displayed as a chip, not as the raw body.
        display = parse_skill_display(_message_text(message))
        if display is not None:
            return {
                "role": "user",
                "text": display.name,
                "skill": True,
                "skill_args": display.args,
                "skill_body": display.body,
            }
    payload: dict = {
        "role": role,
        "text": _message_text(message)[-_PARTIAL_RESULT_CHARS:],
    }
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason:
        payload["stop_reason"] = stop_reason
    tool_name = getattr(message, "tool_name", None)
    if tool_name:
        payload["tool_name"] = tool_name
    is_error = getattr(message, "is_error", None)
    if is_error is not None:
        payload["is_error"] = is_error
    if getattr(message, "steering", False):
        payload["steering"] = True
    return payload


def _bounded_json(value) -> str:
    """One bounded JSON string: a huge blob cannot blow up a protocol line."""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:_ARGS_CHARS]
    except (TypeError, ValueError):
        return str(value)[:_ARGS_CHARS]


def _answers_from_selection(questions: list[dict], raw: object) -> list[dict]:
    """Turn per-question option lists into the ask tool's answer dicts.

    Values that are not one of the question's labels are the card's custom
    text; the tool keeps them in ``custom`` so the model still sees them.
    """
    if not isinstance(raw, list):
        return []
    answers: list[dict] = []
    for index, question in enumerate(questions):
        if index >= len(raw):
            break
        values = raw[index]
        if not isinstance(values, list):
            continue
        labels = [option["label"] for option in question["options"]]
        selected = [
            value for value in values if isinstance(value, str) and value in labels
        ]
        custom = ", ".join(
            value
            for value in values
            if isinstance(value, str) and value not in labels and value.strip()
        )
        if not selected and not custom:
            continue
        answer: dict = {"id": question["id"], "selected": selected}
        if custom:
            answer["custom"] = custom
        answers.append(answer)
    return answers


def serialize_event(event: AgentEvent) -> dict:
    """A small, stable projection of an AgentEvent (no prompts, no key material)."""
    payload: dict = {"type": event.type}
    for field in ("tool_name", "tool_call_id"):
        value = getattr(event, field, None)
        if value:
            payload[field] = value
    if getattr(event, "is_error", None) is not None:
        payload["is_error"] = event.is_error
    assistant_event = getattr(event, "assistant_event", None)
    if assistant_event is not None:
        payload["assistant_event"] = {"type": assistant_event.type}
        if assistant_event.delta:
            payload["assistant_event"]["delta"] = assistant_event.delta
        if assistant_event.type == "error":
            error = getattr(assistant_event.partial, "error_message", None)
            if error:
                payload["assistant_event"]["error"] = str(error)[:_PARTIAL_RESULT_CHARS]
    message = getattr(event, "message", None)
    if message is not None:
        # The canonical turn content: clients that do not reassemble deltas
        # (or providers that only send the terminal message) still get it.
        payload["message"] = _message_projection(message)
    new_messages = getattr(event, "new_messages", None)
    if new_messages:
        payload["new_messages"] = [
            _message_projection(item) for item in new_messages[:20]
        ]
    args = getattr(event, "args", None)
    if args is not None:
        # JSON text rather than a nested object: one bounded string keeps a
        # huge tool call from blowing up the line, and the client can parse it.
        payload["args"] = _bounded_json(args)
    result = getattr(event, "result", None)
    if result is not None:
        text = _text_of(result)
        if text:
            payload["result_text"] = text[-_PARTIAL_RESULT_CHARS:]
        details = getattr(result, "details", None)
        if details:
            # Structured tool data (diff, exit_code, task_id, todo phases,
            # subagent cost/tokens, ...) the TUI cards and HUD render.
            payload["details"] = _bounded_json(details)
    duration = getattr(event, "duration_ms", None)
    if duration is not None:
        payload["duration_ms"] = duration
    tool_stats = getattr(event, "tool_stats", None)
    if tool_stats:
        payload["tool_stats"] = tool_stats
    partial = getattr(event, "partial_result", None)
    if partial is not None:
        # A running tool's streamed output (bash progress, subagent text). The
        # tail is what an embedding UI can render; the full result follows in
        # ``tool_execution_end``. Never let one update carry unbounded bytes.
        text = _text_of(partial)
        if text:
            payload["partial_result"] = text[-_PARTIAL_RESULT_CHARS:]
    return payload


def _usage(agent: Agent) -> dict:
    total = usage_total(agent.messages)
    return {
        "input": total.input,
        "output": total.output,
        "cache_read": total.cache_read,
        "cache_write": total.cache_write,
        "cost": total.cost.total,
    }
