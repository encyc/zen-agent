"""Side-channel session records: run boundaries, retries, mode changes.

The transcript stays pure ``Message`` lines; the runtime appends auxiliary
records (``{"type": ..., "timestamp": ..., ...}``) between them so one session
file tells the whole story — where it ran (the header), why every run ended,
which transient failures were retried, when the permission mode changed. The
same side-channel shape dsh keeps next to its conversation payload.

Everything here is best-effort: :meth:`Session.append_meta` swallows write
failures, so observability can never abort a run.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from zenagent.agent.session import Session
from zenagent.app import records
from zenagent.events import AgentEvent
from zenagent.types import AssistantMessage, Message

__all__ = [
    "TOOL_DETAILS_LIMIT",
    "TOOL_DIFF_MAX_LINES",
    "SessionMetaRecorder",
    "make_retry_recorder",
    "mode_record",
    "tool_end_record",
]

#: Re-exported so existing imports keep working; the vocabulary lives in
#: :mod:`zenagent.app.records`.
TOOL_DETAILS_LIMIT = records.TOOL_DETAILS_LIMIT
TOOL_DIFF_MAX_LINES = records.TOOL_DIFF_MAX_LINES
tool_end_record = records.tool_end
mode_record = records.mode


def _terminal_detail(messages: list[Message] | None) -> str | None:
    """The last assistant error message in the run, for the ``run_end`` record."""
    for message in reversed(messages or []):
        if isinstance(message, AssistantMessage) and message.error_message:
            return message.error_message
    return None


class SessionMetaRecorder:
    """Agent listener persisting run boundaries and ``tool_end`` records.

    The session is read through ``get_session`` at each event so session
    switches (new/fork/undo) are followed, mirroring how the runtime's own
    ``persist`` subscriber resolves the live session.
    """

    def __init__(self, get_session: Callable[[], Session]) -> None:
        self._get_session = get_session
        self._started_at: float | None = None

    def __call__(self, event: AgentEvent) -> None:
        if event.type == "tool_execution_end" and event.tool_call_id:
            # The card's duration, error flag and bounded details are the same
            # facts after a resume, so the runtime persists them for every
            # frontend — ``zen -p`` never builds a ChatController.
            details = dict(getattr(event.result, "details", None) or {})
            self._get_session().append_meta(
                records.tool_end(
                    event.tool_call_id,
                    event.tool_name or "",
                    event.duration_ms,
                    bool(event.is_error),
                    details,
                )
            )
        elif event.type == "agent_start":
            self._started_at = time.monotonic()
            self._get_session().append_meta(records.run_start())
        elif event.type == "agent_end":
            started, self._started_at = self._started_at, None
            self._get_session().append_meta(
                records.run_end(
                    termination=event.termination,
                    turns=event.turns,
                    duration_ms=(
                        None
                        if started is None
                        else round((time.monotonic() - started) * 1000)
                    ),
                    detail=_terminal_detail(event.new_messages),
                )
            )


def make_retry_recorder(
    get_session: Callable[[], Session],
) -> Callable[[AssistantMessage, int, int, float], None]:
    """``on_retry`` observer persisting one ``retry`` record per swallowed attempt."""

    def on_retry(message: AssistantMessage, attempt: int, max_retries: int, delay_s: float) -> None:
        get_session().append_meta(
            records.retry(
                attempt=attempt,
                max_retries=max_retries,
                delay_ms=round(delay_s * 1000),
                error=message.error_message or "",
                model=message.model,
            )
        )

    return on_retry
