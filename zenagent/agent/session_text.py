"""Session JSONL -> searchable :class:`Turn`s: the one parse path for search.

Cross-session search must read transcripts it did not write, in bulk, and only
ever wants *text*: the user's words, the assistant's answer, and (when asked)
what the tools were called with and what they said back. Two deliberate
differences from :class:`zenagent.agent.session.Session`:

* **no data-model round trip.** ``Session.load`` rebuilds ``Message`` objects,
  hydrates offloaded image blobs and can replay a boundary-delimited segment.
  Search wants none of that: it reads the JSONL lines as they sit on disk, so a
  session whose image blobs were pruned is still searchable, and a search never
  pays for hydration it will not use. The formats agree because both are the
  same file: a line with a ``"role"`` key is a message, anything else is a
  side-channel record.
* **thinking is dropped right here.** Reasoning traces are private to the model
  and were never meant to be read back by a person -- and certainly not
  resurfaced by search. Dropping them at this single parse point (rather than
  teaching each of the three tools to filter) is what makes "never read
  thinking" one rule in one place; :data:`LEVELS` widens what is *rendered*,
  never whether reasoning is.

Resilience matches the rest of the session layer: a torn final line (a process
killed mid-append) is skipped, never fatal. ``errors="replace"`` keeps a stray
invalid byte from aborting a whole search.

The parsing here is intentionally flat and forgiving -- a malformed block is
skipped, and a record whose shape this module does not recognize yields no
turns. Nothing in here raises except an unknown ``include`` level.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LEVELS", "Turn", "count_messages", "excerpt", "iter_turns"]

#: The three render levels, narrowest first. ``conversation`` is what a person
#: said and was told; ``tools`` adds the calls (name + arguments); ``all`` adds
#: the tool result bodies. Each level is a superset of the one before it.
LEVELS: tuple[str, ...] = ("conversation", "tools", "all")

#: ``include`` -> the widest block kind it renders (compared with ``>=``).
_LEVEL_MASK = {"conversation": 0, "tools": 1, "all": 2}

#: The message roles search knows how to render. Any other role is counted as a
#: message (``count_messages``) but yields no turns.
_USER_ROLE = "user"
_ASSISTANT_ROLE = "assistant"
_TOOL_RESULT_ROLE = "toolResult"


@dataclass(frozen=True)
class Turn:
    """One searchable piece of a session, tagged with the message it came from.

    ``index`` is the 0-based **message line** number (side-channel records do
    not count), which is the same numbering ``read_session`` reports as
    ``#N``: one message can yield several turns (text, then a tool call), and
    they all share its index. ``role`` is the message role, ``kind`` is what
    this particular turn renders, and ``tool_name`` is set for calls/results.
    """

    index: int
    role: str
    kind: str
    text: str
    tool_name: str = ""


def _mask(include: str) -> int:
    try:
        return _LEVEL_MASK[include]
    except KeyError:
        raise ValueError(
            f"unknown include {include!r}; choose one of {', '.join(LEVELS)}"
        ) from None


def iter_turns(path: Path, include: str = "conversation") -> Iterator[Turn]:
    """Searchable turns of the session file at ``path``, in file order.

    ``include`` selects how much is rendered (:data:`LEVELS`); an unknown level
    raises ``ValueError`` before the file is touched. A missing file reads as
    empty, and unreadable lines are skipped -- a torn tail must never make a
    search fail.
    """
    mask = _mask(include)
    return _iter_turns(Path(path), mask)


def count_messages(path: Path) -> int:
    """Message lines in ``path``: the numbering :func:`iter_turns` reports.

    Independent of ``include`` (a conversation-level session and an all-level
    one have the same count) and independent of how many turns each message
    renders as, so ``#N`` cannot drift between the two tools that show it. Walks
    the file on its own rather than over :func:`iter_turns`, which filters
    blocks by level and so cannot count message lines.
    """
    return sum(1 for _index, _record in _messages(path))


def excerpt(
    text: str, start: int, end: int, *, before: int = 60, after: int = 90
) -> str:
    """A one-line window of ``text`` around ``[start:end]``.

    Takes ``before`` characters ahead of the match and ``after`` behind it,
    clamps at the start of the text, and flattens every newline/run of spaces to
    single spaces: a hit is shown on one line, so a huge or multi-line body
    cannot blow up a result listing.
    """
    window = text[max(0, start - before) : end + after]
    return " ".join(window.split())


def _messages(path: Path) -> Iterator[tuple[int, dict]]:
    """``(message index, record)`` for every message line, skipping the rest.

    Side-channel records (a ``"type"`` and no ``"role"``) are consumed without
    taking an index, so the numbering counts what a person would call messages.
    """
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        index = 0
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # torn tail or a hand-edited line: drop it, keep going
            if not isinstance(record, dict) or "role" not in record:
                continue
            yield index, record
            index += 1


def _iter_turns(path: Path, mask: int) -> Iterator[Turn]:
    for index, record in _messages(path):
        yield from _turns_of(index, record, mask)


def _turns_of(index: int, record: dict, mask: int) -> Iterator[Turn]:
    """The turns one message line renders, honoring ``mask``."""
    role = record.get("role")
    content = record.get("content")
    if role == _USER_ROLE:
        # A string is the common case; a user message carrying attachments is
        # a block list, and its text is still what the person typed.
        text = content if isinstance(content, str) else "\n".join(_texts(content))
        if text:
            yield Turn(index, _USER_ROLE, "text", text)
    elif role == _ASSISTANT_ROLE:
        for block in _blocks(content):
            kind = block.get("type")
            if kind == "text":
                block_text = block.get("text")
                if isinstance(block_text, str) and block_text:
                    yield Turn(index, _ASSISTANT_ROLE, "text", block_text)
            elif kind == "toolCall" and mask >= 1:
                raw_name = block.get("name")
                name = raw_name if isinstance(raw_name, str) else ""
                arguments = block.get("arguments")
                yield Turn(
                    index,
                    _ASSISTANT_ROLE,
                    "toolCall",
                    f"{name} {json.dumps(arguments, ensure_ascii=False)}",
                    tool_name=name,
                )
            # ``thinking`` (and every other block kind) never becomes a turn:
            # reasoning is not readable material, at any level.
    elif role == _TOOL_RESULT_ROLE and mask >= 2:
        text = "\n".join(_texts(content))
        if text:
            tool_name = record.get("tool_name")
            yield Turn(
                index,
                _TOOL_RESULT_ROLE,
                "result",
                text,
                tool_name=tool_name if isinstance(tool_name, str) else "",
            )


def _blocks(content: object) -> Iterator[dict]:
    """Dict blocks of a content list; anything else is ignored."""
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict):
            yield block


def _texts(content: object) -> Iterator[str]:
    """Text of every ``text`` block in a content list (empty ones dropped)."""
    for block in _blocks(content):
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            yield text
