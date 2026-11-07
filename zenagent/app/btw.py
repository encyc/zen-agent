"""``/btw``: a side question that never enters the main transcript.

The main conversation is left byte-for-byte untouched: the question goes to the
provider as a throwaway request (system prompt + question), and only the answer
is shown to the user. This is the cheap version of omp's ``/btw`` panel — one
answer, no history, no tool calls.
"""

from __future__ import annotations

import time

from zenagent.agent import Agent
from zenagent.providers.registry import safe_stream, stream_fn_for
from zenagent.providers.stream_options import StreamOptions
from zenagent.types import SystemMessage, UserMessage

__all__ = ["ask_side_question"]


async def ask_side_question(agent: Agent, question: str) -> str:
    """Ask ``question`` out-of-band; returns the answer text.

    Raises ``ValueError`` for an empty question and ``RuntimeError`` when the
    provider fails or returns nothing.
    """
    question = question.strip()
    if not question:
        raise ValueError("usage: /btw <question>")
    now = int(time.time())
    messages = [
        SystemMessage(content=agent.system_prompt, timestamp=now),
        UserMessage(
            content=(
                "Side question (answer concisely; this is not part of the main "
                f"conversation): {question}"
            ),
            timestamp=now,
        ),
    ]
    options = StreamOptions(api_key=agent.api_key, thinking=None)
    fn = agent.stream_fn or stream_fn_for(agent.model)
    text = ""
    error: str | None = None
    async for event in safe_stream(fn, agent.model, messages, [], options):
        if event.type == "text_delta" and event.delta:
            text += event.delta
        elif event.type in ("done", "error"):
            error = error or event.partial.error_message
    if error:
        raise RuntimeError(f"side question failed: {error}")
    if not text.strip():
        raise RuntimeError("side question produced no answer")
    return text.strip()
