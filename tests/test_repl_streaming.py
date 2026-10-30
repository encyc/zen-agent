"""The REPL printer must flush streamed deltas (no line-buffered lump)."""

from __future__ import annotations

from zenagent.cli.repl import _EventPrinter
from zenagent.events import AgentEvent, AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage


class _FakeStdout:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flushes = 0

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        self.flushes += 1


def _delta(text: str) -> AgentEvent:
    partial = AssistantMessage(
        content=[TextContent(text=text)],
        provider="p",
        model="m",
        usage=Usage(),
        stop_reason="pending",
        timestamp=0,
    )
    return AgentEvent(
        type="message_update",
        assistant_event=AssistantEvent(
            type="text_delta", partial=partial, content_index=0, delta=text
        ),
    )


def test_default_print_flushes_every_delta(monkeypatch):
    fake = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake)
    printer = _EventPrinter(print_fn=print)

    printer(_delta("AAAA"))
    printer(_delta("BBBB"))

    assert "".join(fake.writes) == "AAAABBBB"
    assert fake.flushes >= 2, "each delta must be flushed, not left in the buffer"


def test_custom_print_fn_is_not_given_a_flush_keyword():
    calls: list[tuple[str, str]] = []

    def capture(text: str, end: str = "") -> None:
        calls.append((text, end))

    printer = _EventPrinter(print_fn=capture)
    printer(_delta("hello"))

    assert calls == [("hello", "")]
