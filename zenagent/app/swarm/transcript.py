"""Per-task run activity, sliced out of the role's own session file.

A role appends every run to one session, and each injected prompt marks the
start of a task's range. Reading the file between two prompts gives exactly
what one task did -- tool calls, interim text, results -- without the room
carrying a second copy of the transcript.

Rows are the same view dicts the coding transcript ships, so the desktop
replays a run through ``nodesFromViews`` instead of a second renderer.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from zenagent.app.views import messages_to_views
from zenagent.types import Message, UserMessage, message_from_json

__all__ = ["read_task_transcript", "read_task_transcript_from_disk"]

#: The injected prompt opens with this; two of them bracket one run.
_INJECTED_PREFIX = "[swarm] from "


def _injected(text: str) -> bool:
    return text.startswith(_INJECTED_PREFIX)


def _names_task(text: str, task_id: str) -> bool:
    # The prompt's second line is ``task: <id> · chain: ...``; the trailing
    # separator keeps t_1 from matching t_10.
    return _injected(text) and f"task: {task_id} ·" in text


def _plain_text(message: Message) -> str | None:
    if not isinstance(message, UserMessage):
        return None
    return message.content if isinstance(message.content, str) else None


def _load_messages(path: Path) -> list[Message]:
    messages: list[Message] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn tail is expected; the kernel drops it too
        if not isinstance(raw, dict) or "role" not in raw:
            continue  # session header or another side-channel record
        try:
            messages.append(message_from_json(raw))
        except ValueError:
            continue
    return messages


def read_task_transcript(
    path: Path, task_id: str, *, running: bool = False
) -> tuple[bool, list[dict]]:
    """The views of ``task_id``'s run, and whether the run was found.

    ``running`` keeps an in-flight tool call ``running`` instead of settling
    it as ``interrupted``: an unpaired call is only abnormal once the task
    has stopped.
    """
    try:
        messages = _load_messages(Path(path))
    except OSError:
        return False, []

    start: int | None = None
    for index, message in enumerate(messages):
        text = _plain_text(message)
        if text is not None and _names_task(text, task_id):
            start = index
    if start is None:
        return False, []

    end = len(messages)
    for index in range(start + 1, len(messages)):
        text = _plain_text(messages[index])
        if text is not None and _injected(text):
            end = index
            break

    rows = [asdict(view) for view in messages_to_views(messages[start + 1 : end])]
    if running:
        for row in rows:
            if (
                row["kind"] == "tool"
                and row["status"] == "interrupted"
                and not row["result_text"]
            ):
                row["status"] = "running"
    return True, rows


def read_task_transcript_from_disk(sessions_dir: Path, task_id: str) -> list[dict] | None:
    """Find one task's run in a session directory, without a live runtime.

    Needed because instances are built lazily and reclaimed when idle: every
    round of a closed work item has no runtime, so the detail panel has to read
    the session files itself. A ``reset`` inside a work item leaves several
    files in the directory, so each is searched rather than only the newest.
    """
    directory = Path(sessions_dir)
    try:
        files = sorted(directory.glob("*.jsonl"))
    except OSError:
        return None
    for path in files:
        try:
            available, rows = read_task_transcript(path, task_id)
        except OSError:
            continue
        if available:
            return rows
    return None
