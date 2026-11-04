"""Subagent view-model state: live transcripts, sidebar rows, spend totals.

Split out of ``ChatController`` — everything keyed by a ``task`` call id lives
here; the controller keeps only one-line delegates so the ``tools.task``
observer contract and the tests keep addressing it the same way.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from zenagent.agent.subagents import SubagentStore
from zenagent.app.views import SubagentView, ToolCallView, TranscriptBuilder

__all__ = ["SubagentTracker"]

_TASK_SUBAGENT_STATUSES = ("ok", "error", "aborted", "truncated", "interrupted")


def _subagent_view(card: ToolCallView, *, meta=None, has_transcript=False) -> SubagentView:
    """One ``task`` card as a sidebar row (live card, sidecar meta, envelope)."""
    details = card.details or {}
    name = str(card.args.get("name") or "task")
    if card.status == "running":
        started = card.started_at
        duration_ms = int((time.monotonic() - started) * 1000) if started else 0
        status, cost, tokens = "running", 0.0, 0
    elif meta is not None:
        status = meta.status if meta.status in _TASK_SUBAGENT_STATUSES else "ok"
        duration_ms = meta.duration_ms
        cost, tokens = meta.cost, meta.tokens
        name = meta.name or name
    else:
        raw = str(details.get("status") or ("error" if card.is_error else "ok"))
        status = (
            raw if raw in _TASK_SUBAGENT_STATUSES else ("error" if card.is_error else "ok")
        )
        duration_ms = int(details.get("duration_ms") or card.duration_ms or 0)
        cost = float(details.get("cost") or 0.0)
        tokens = int(details.get("tokens") or 0)
    return SubagentView(
        call_id=card.call_id,
        name=name,
        status=status,
        duration_ms=duration_ms,
        cost=cost,
        tokens=tokens,
        has_transcript=has_transcript,
        transcript_truncated=bool(meta is not None and meta.truncated),
    )


class SubagentTracker:
    """Live ``task`` transcripts and the sidebar's subagent rows."""

    def __init__(self, notify: Callable[[], None]) -> None:
        self._notify = notify
        #: Live subagent transcripts keyed by the parent ``task`` call id.
        self._builders: dict[str, TranscriptBuilder] = {}
        self._meta: dict[str, dict] = {}
        #: Cached read of the session's transcript sidecars (disk scan only).
        self._disk_metas: dict | None = None
        #: Subagent spend/tokens accumulated from ``task`` tool results.
        self.cost = 0.0
        self.tokens = 0

    def on_event(self, call_id: str, kind: str, payload: dict) -> None:
        """Live hook from ``tools.task`` (the module-level observer)."""
        if kind == "start":
            self._builders[call_id] = TranscriptBuilder()
            self._meta[call_id] = {
                "name": str(payload.get("name") or "task"),
                "status": "running",
                "duration_ms": 0,
                "cost": 0.0,
                "tokens": 0,
            }
            self._disk_metas = None
            self._notify()
            return
        if kind == "event":
            builder = self._builders.get(call_id)
            event = payload.get("event")
            if builder is not None and event is not None and builder.apply(event):
                self._disk_metas = None
                self._notify()
            return
        if kind == "end":
            meta = self._meta.setdefault(
                call_id, {"name": str(payload.get("name") or "task")}
            )
            meta.update(
                status=str(payload.get("status") or "ok"),
                duration_ms=int(payload.get("duration_ms") or 0),
                cost=float(payload.get("cost") or 0.0),
                tokens=int(payload.get("tokens") or 0),
            )
            self._disk_metas = None
            self._notify()

    def transcript(
        self, call_id: str, *, session_path: Path | None
    ) -> list | None:
        """Live buffer first, persisted sidecar otherwise (after resume)."""
        builder = self._builders.get(call_id)
        if builder is not None:
            return list(builder.views)
        messages = SubagentStore(session_path).load(call_id)
        if messages is None:
            return None
        persisted = TranscriptBuilder()
        persisted.load(messages)
        return list(persisted.views)

    def views(
        self, cards: list[ToolCallView], *, session_path: Path | None
    ) -> list[SubagentView]:
        """The live ``task`` call plus recent ones, newest first.

        Only transcripts the current transcript still references are listed:
        ``/undo`` and ``/fork`` truncation leaves sidecar files behind, and
        resurrecting their rows would invent work that is no longer in history.
        """
        if self._disk_metas is None:
            self._disk_metas = {
                meta.call_id: meta
                for meta in SubagentStore(session_path).list()
            }
        persisted = self._disk_metas
        running: list[SubagentView] = []
        finished: list[SubagentView] = []
        # A dispatched-but-unfinished background child confuses the disk meta:
        # its transcript has a header but no end line, which reads as
        # "interrupted". The TaskManager's in-flight set is the truth. No
        # running loop means no manager and no background children.
        from zenagent.tools.task import get_task_manager

        try:
            inflight = get_task_manager().running_call_ids()
        except RuntimeError:  # pragma: no cover - sync callers, no loop
            inflight = set()
        for card in reversed(cards):
            if card.name != "task":
                continue
            meta = persisted.get(card.call_id)
            view = _subagent_view(
                card,
                meta=meta,
                has_transcript=card.call_id in self._builders or meta is not None,
            )
            if card.call_id in inflight and view.status != "running":
                view = replace(view, status="running")
            (running if view.status == "running" else finished).append(view)
        # The live elapsed is computed per call: caching the views would freeze
        # a running subagent's clock between tool events.
        return running + finished

    def clear_transcripts(self) -> None:
        """Drop live transcripts/caches but keep the spend bucket (retry)."""
        self._builders.clear()
        self._meta.clear()
        self._disk_metas = None

    def add_spend(self, cost: float, tokens: int) -> None:
        self.cost += cost
        self.tokens += tokens

    def invalidate_disk(self) -> None:
        self._disk_metas = None

    def reset(self) -> None:
        """Session switch/``clear``: transcripts AND a fresh spend bucket."""
        self.clear_transcripts()
        self.cost = 0.0
        self.tokens = 0
