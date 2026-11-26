"""Auto session title: one small provider call names a new conversation.

The display name lives in the session titles sidecar, so every frontend (TUI
picker, ``--list-sessions``, the desktop sidebar) reads it without new
plumbing. This module owns the two halves that are easy to get wrong: turning
a model reply into something a sidebar can show (:func:`clean_title`) and the
background call itself (:class:`AutoTitler`). Failure is silent by contract —
naming is a convenience, so it may never disturb the run.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.agent.session import (
    goal_objective,
    read_session_titles,
    set_session_title,
)
from zenagent.events import AgentEvent
from zenagent.prompts import compose_title
from zenagent.providers.registry import safe_stream, stream_fn_for
from zenagent.providers.stream_options import StreamOptions
from zenagent.types import Message, SystemMessage, TextContent, UserMessage

if TYPE_CHECKING:
    from zenagent.app.runtime import Runtime

__all__ = [
    "AUTO_TITLE_LIMIT",
    "TITLE_MAX_TOKENS",
    "TITLE_PROMPT_LIMIT",
    "TITLE_TIMEOUT_S",
    "AutoTitler",
    "clean_title",
    "title_source",
]

#: Safety ceiling for a stored name; the prompt targets ~10 Han characters (or
#: 5-6 English words), and a model that ignores that is cut here, not at 10.
AUTO_TITLE_LIMIT = 60

#: Output budget for the naming call: a name, not a paragraph.
TITLE_MAX_TOKENS = 128

#: How much of the first user message goes into the prompt.
TITLE_PROMPT_LIMIT = 2000

#: Wall clock for the naming call; a slow gateway must not hold the task open.
TITLE_TIMEOUT_S = 15.0

#: Labels models like to prepend ("会话名：…", "Title: …") even when told not to.
_PREFIXES = ("会话名：", "会话名:", "会话名称：", "标题：", "名字：", "Title:", "title:")

#: Wrapping punctuation a name may arrive in: quotes, CJK brackets, code ticks.
_WRAPPERS = "\"'“”‘’「」『』《》`*"

#: Sentence punctuation a bare name should not end with.
_TRAILING = "。．.!！?？,，;；:：、…"


def clean_title(raw: str) -> str:
    """One provider reply -> a display name; ``""`` means give up.

    A fenced block loses its fence, only the first non-blank line survives, a
    known label is dropped, wrapping punctuation is stripped from both ends,
    whitespace flattens, and trailing sentence punctuation goes. Anything left
    longer than :data:`AUTO_TITLE_LIMIT` is cut (no ellipsis: a name is not a
    snippet). Unicode-aware ``len`` keeps CJK characters whole.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text[3:]
        if "\n" in text:
            text = text.split("\n", 1)[1]
        text = text.rstrip().removesuffix("```")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[0]
    for prefix in _PREFIXES:
        if line.startswith(prefix):
            line = line[len(prefix) :].strip()
            break
    line = line.strip(_WRAPPERS).strip()
    line = " ".join(line.split())
    line = line.rstrip(_TRAILING).strip()
    if len(line) > AUTO_TITLE_LIMIT:
        line = line[:AUTO_TITLE_LIMIT].rstrip()
    return line


def title_source(message: UserMessage) -> str:
    """The text the name is generated from; ``""`` when there is none.

    Text blocks are enough (a message that also carries images still has
    something to summarize); a goal turn names the objective it carries rather
    than the wrapper that delivered it.
    """
    content = message.content
    if isinstance(content, str):
        text = content
    else:
        text = "".join(
            block.text for block in content if isinstance(block, TextContent)
        )
    text = text.strip()
    if not text:
        return ""
    if message.goal:
        return goal_objective(text) or text
    return text


class AutoTitler:
    """One background provider call per new session, fired by its first message.

    It is an agent listener: ``message_end`` for the session's first user
    message schedules the call instead of awaiting it, so the run never waits
    on naming. A resumed session already has messages, and a stored title
    (a ``/rename`` or a previous run) short-circuits every write, including the
    one rechecked after the round trip.
    """

    def __init__(self, runtime: Runtime, *, timeout_s: float = TITLE_TIMEOUT_S) -> None:
        self._runtime = runtime
        self._timeout_s = timeout_s
        #: Sessions already handled (or in flight); one call each, ever.
        self._started: set[Path] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def __call__(self, event: AgentEvent) -> None:
        if event.type != "message_end":
            return
        message = event.message
        if not isinstance(message, UserMessage):
            return
        runtime = self._runtime
        path = runtime.session.path
        if path in self._started:
            return
        # A resumed transcript already carries earlier user turns; only the
        # session's very first message is worth naming.
        if sum(isinstance(item, UserMessage) for item in runtime.agent.messages) != 1:
            return
        if read_session_titles(path.parent).get(path.name):
            return
        text = title_source(message)
        if not text:
            return
        self._started.add(path)
        task = asyncio.create_task(self._run(path, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def finish(self, timeout_s: float = 2.0) -> None:
        """Let the in-flight call land, then cancel it; shutdown never hangs."""
        pending = list(self._tasks)
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout_s)
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _run(self, path: Path, text: str) -> None:
        try:
            raw = await asyncio.wait_for(self._request(text), self._timeout_s)
        except Exception:  # noqa: BLE001 - naming never disturbs the run
            return
        name = clean_title(raw)
        if not name:
            return
        try:
            # Recheck right before writing, with no await in between: a
            # ``/rename`` typed during the round trip must win.
            if read_session_titles(path.parent).get(path.name):
                return
            set_session_title(path, name)
        except (OSError, ValueError):
            return

    async def _request(self, text: str) -> str:
        """One provider round trip; ``""`` for anything but a clean answer."""
        agent = self._runtime.agent
        system, user = compose_title(text[:TITLE_PROMPT_LIMIT])
        now = int(time.time())
        request: list[Message] = [
            SystemMessage(content=system, timestamp=now),
            UserMessage(content=user, timestamp=now),
        ]
        # A second caller of the provider, like compaction: the session id must
        # ride along (a gateway that routes on it rejects a request without it),
        # and no abort token is inherited from the run.
        options = StreamOptions(
            api_key=agent.api_key,
            thinking=None,
            max_tokens=TITLE_MAX_TOKENS,
            session_id=agent.session_id,
        )
        stream = agent.stream_fn or stream_fn_for(agent.model)
        async for event in safe_stream(stream, agent.model, request, [], options):
            if event.type != "done":
                continue
            return "".join(
                block.text
                for block in event.partial.content
                if isinstance(block, TextContent)
            )
        return ""
