"""Kernel-level result budget (safety net above the per-tool caps).

Every tool — built-in, MCP or extension — runs its result through this policy
before it is written back to the transcript:
- a single overlong line is capped (logs/minified files would otherwise blow
  up one line at a time);
- a result whose text payload exceeds ``MAX_RESULT_BYTES`` keeps its head and
  tail and gets one uniform truncation notice;
- image blocks are preserved untouched.

The module deliberately does not import the loop at runtime (the loop imports
this), so the result object is handled structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zenagent.types import ImageContent, TextContent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from zenagent.agent.loop import ToolResultAgent

__all__ = [
    "HEAD_BYTES",
    "LINE_TRUNCATION_MARKER",
    "MAX_LINE_CHARS",
    "MAX_RESULT_BYTES",
    "TAIL_BYTES",
    "apply_result_budget",
]

#: A single line longer than this (in characters) is capped.
MAX_LINE_CHARS = 2_000

#: Total text bytes above which the result keeps only head and tail.
MAX_RESULT_BYTES = 40_000

#: Bytes kept from the head / tail of an oversized result.
HEAD_BYTES = 20_000
TAIL_BYTES = 12_000

#: Marker inserted where an overlong line was middle-truncated.
LINE_TRUNCATION_MARKER = " …[line truncated]"
_LINE_MARKER = LINE_TRUNCATION_MARKER


def _cap_long_lines(text: str) -> tuple[str, int]:
    """Middle-truncate overlong lines; returns the text and changed-line count.

    Keeping both ends of the line matters: after the byte budget has kept the
    head and tail of the whole payload, a naive prefix cut on the head line
    would throw the tail away again.
    """
    truncated = 0
    out: list[str] = []
    keep = MAX_LINE_CHARS // 2
    for line in text.split("\n"):
        if len(line) > MAX_LINE_CHARS:
            out.append(line[:keep] + _LINE_MARKER + line[-keep:])
            truncated += 1
        else:
            out.append(line)
    return "\n".join(out), truncated


def _truncate_bytes(text: str, total_bytes: int) -> str:
    """Keep the head and tail of ``text`` and mark the removed middle."""
    raw = text.encode("utf-8")
    head = raw[:HEAD_BYTES].decode("utf-8", "ignore")
    tail = raw[-TAIL_BYTES:].decode("utf-8", "ignore")
    removed = total_bytes - HEAD_BYTES - TAIL_BYTES
    notice = (
        f"\n\n… [truncated {removed} of {total_bytes} bytes: kept the first "
        f"{HEAD_BYTES} and last {TAIL_BYTES} (long lines trimmed); narrow the "
        "tool call (read with offset/limit, or grep) for the rest] …\n\n"
    )
    return head + notice + tail


def apply_result_budget(result: ToolResultAgent) -> ToolResultAgent:
    """Cap one tool result's text payloads; image blocks pass through."""
    texts = [
        block.text for block in result.content if isinstance(block, TextContent)
    ]
    if not texts:
        return result
    # Idempotent: a second pass must not re-wrap or double-count.
    if result.details.get("budget", {}).get("truncated") is True:
        return result

    text = "\n".join(texts)
    original_bytes = len(text.encode("utf-8"))
    if original_bytes <= MAX_RESULT_BYTES:
        # Under budget the payload is returned untouched. Capping a single
        # long line here would destroy one-line payloads (minified HTML/JSON,
        # compact `jq -c` output) that are small enough to keep whole.
        return result

    budget: dict = {"truncated": True, "original_bytes": original_bytes}
    # Byte-budget first: a single overlong line must not swallow the head and
    # tail that make the truncation recoverable.
    capped = _truncate_bytes(text, original_bytes)
    capped, truncated_lines = _cap_long_lines(capped)
    if truncated_lines:
        budget["truncated_lines"] = truncated_lines

    images = [
        block for block in result.content if isinstance(block, ImageContent)
    ]
    details = dict(result.details)
    details["budget"] = budget
    # ``type(result)`` avoids importing the loop at module load time (it imports
    # us); the loop's contract is that a tool returns that dataclass, and using
    # the actual class keeps any subclass intact when it has the same fields.
    return type(result)(
        content=[TextContent(text=capped), *images],
        details=details,
        is_error=result.is_error,
        terminate=result.terminate,
    )

