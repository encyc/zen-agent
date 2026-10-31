"""ChatController: one run at a time, view-model state, follow-up queue."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from zenagent.agent.subagents import carry_task_dir
from zenagent.app.compact import compact, should_auto_compact
from zenagent.app.retry import prepare_retry
from zenagent.app.runtime import Runtime
from zenagent.app.support import cache_hit_ratio
from zenagent.app.views import (
    JobView,
    RunStatus,
    SubagentView,
    ToolCallView,
    TranscriptBuilder,
    TranscriptNotice,
    job_views,
    sort_jobs,
    usage_total,
)
from zenagent.events import AgentEvent
from zenagent.tools.jobs import get_job_manager
from zenagent.tools.todo import coerce_todo_phases, load_todo_phases
from zenagent.types import ImageContent

__all__ = ["ChatController"]


class ChatController:
    """Drives the agent and exposes snapshots a frontend can render."""

    def __init__(
        self, runtime: Runtime, *, on_change: Callable[[], None] = lambda: None
    ) -> None:
        self.runtime = runtime
        self._on_change = on_change
        self._builder = TranscriptBuilder()
        #: (text, images) pairs; images are captured when the turn is queued.
        self._follow_ups: list[tuple[str, list[ImageContent]]] = []
        self._task: asyncio.Task | None = None
        self._running = False
        #: True while an automatic compaction runs: the controller stays busy
        #: so a submit queues instead of racing the summarizer (C2).
        self._compacting = False
        #: Last finished turn: wall time and output tokens (status segments).
        self._turn_ms = 0
        self._turn_output = 0
        #: Provider failure from the last run, shown by the TUI's error banner.
        self._last_error = ""
        #: Out-of-band lines (compaction dividers, auto-compaction failures) the
        #: frontend drains on refresh.
        self._notices: list[TranscriptNotice] = []
        #: Images attached with /image that will ride along with the next prompt.
        self._attachments: list[ImageContent] = []
        #: Subagent spend/tokens accumulated from ``task`` tool results.
        self._subagent_cost = 0.0
        self._subagent_tokens = 0
        #: Latest canonical todo phases (``details["phases"]`` shape) for the
        #: HUD. Seeded from the current session's sidecar so resume/start shows
        #: the list before the first tool call of the restored transcript.
        self._todos: list[dict] = load_todo_phases(self._session_path())
        runtime.agent.subscribe(self._on_event)

    # -- snapshots ----------------------------------------------------------

    @property
    def transcript(self) -> list[ToolCallView]:
        return list(self._builder.views)

    @property
    def todos(self) -> list[dict]:
        """Current todo phases for the HUD (``[]`` while there is no list)."""
        return list(self._todos)

    def _session_path(self) -> Path | None:
        session = getattr(self.runtime, "session", None)
        return Path(session.path) if session is not None else None

    @property
    def status(self) -> RunStatus:
        agent = self.runtime.agent
        usage = usage_total(agent.messages)
        last = self.runtime.guard.last_usage
        context_used = (
            last.input + last.output if last is not None else usage.input + usage.output
        )
        subagents = self._subagent_views()
        return RunStatus(
            model_id=agent.model.id,
            thinking=agent.thinking,
            running=self._running or self._compacting,
            input_tokens=usage.input,
            output_tokens=usage.output,
            cache_read=usage.cache_read,
            cache_write=usage.cache_write,
            cache_hit_ratio=cache_hit_ratio(usage, agent.model.api),
            turn_ms=self._turn_ms,
            turn_output_tokens=self._turn_output,
            last_error=self._last_error,
            cost=usage.cost.total,
            context_used=context_used,
            context_window=agent.model.context_window,
            follow_ups=len(self._follow_ups),
            steering=agent.pending_steering,
            session_name=Path(self.runtime.session.path).stem,
            cwd=str(Path.cwd()),
            mode=self.runtime.mode,
            todos=self.todos,
            subagent_cost=self._subagent_cost,
            subagent_tokens=self._subagent_tokens,
            jobs=self._job_views(),
            subagents=subagents,
            active_subagent=next(
                (view.call_id for view in subagents if view.status == "running"), None
            ),
        )

    # -- work-in-progress snapshots -----------------------------------------

    def _job_views(self) -> list[JobView]:
        """Background jobs for the sidebar; empty when no loop owns a manager."""
        try:
            manager = get_job_manager()
        except RuntimeError:  # no running loop (headless, sync tests)
            return []
        return sort_jobs(job_views(manager.list()))

    def _subagent_views(self) -> list[SubagentView]:
        """The live ``task`` call plus recent ones, newest first."""
        running: list[SubagentView] = []
        finished: list[SubagentView] = []
        for card in reversed(list(self._builder.cards.values())):
            if card.name != "task":
                continue
            view = _subagent_view(card)
            (running if view.status == "running" else finished).append(view)
        return running + finished

    # -- commands -----------------------------------------------------------

    async def submit(
        self, text: str, *, images: list[ImageContent] | None = None
    ) -> None:
        """Start a run, or queue the text when one is already running.

        ``images`` overrides the pending attachment list; otherwise whatever was
        attached with ``/image`` rides along and is cleared once the run starts.
        """
        if self._running or self._compacting:
            pending = list(images if images is not None else self._attachments)
            if images is None:
                self._attachments.clear()
            self.queue_follow_up(text, pending)
            return
        pending = list(images if images is not None else self._attachments)
        if images is None:
            self._attachments.clear()
        self._task = asyncio.create_task(self._run(text, pending))

    @property
    def attachments(self) -> list[ImageContent]:
        """Pending images attached but not yet sent."""
        return list(self._attachments)

    def attach_image(self, block: ImageContent) -> None:
        self._attachments.append(block)
        self._notify()

    def remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._attachments):
            del self._attachments[index]
            self._notify()

    def clear_attachments(self) -> None:
        if self._attachments:
            self._attachments.clear()
            self._notify()

    def queue_follow_up(
        self, text: str, images: list[ImageContent] | None = None
    ) -> None:
        """Queue a follow-up; ``images=None`` takes the pending attachments."""
        if not text.strip():
            return
        pending = list(images if images is not None else self._attachments)
        if images is None:
            self._attachments.clear()
        self._follow_ups.append((text, pending))
        self._notify()

    def clear_follow_ups(self) -> None:
        self._follow_ups.clear()
        self._notify()

    def dequeue_follow_up(self) -> str | None:
        """Take the most recently queued follow-up back out of the queue.

        omp's dequeue semantics: the last thing you queued is the one you most
        likely want to edit, so pop from the end. Returns ``None`` when empty.
        """
        if not self._follow_ups:
            return None
        text, images = self._follow_ups.pop()
        if images:
            self._attachments[:0] = images  # put the images back in the composer
        self._notify()
        return text

    def steer(self, text: str) -> None:
        if text.strip():
            self.runtime.agent.steer(text)
            self._notify()

    def abort(self) -> None:
        self.runtime.agent.abort()
        self._notify()

    def set_last_error(self, message: str) -> None:
        """Show a frontend-initiated problem in the dock banner.

        Used for setup states that block a run before the agent starts (a first
        launch without an API key); a provider failure still overwrites it on
        the next run.
        """
        message = (message or "").strip()
        if message == self._last_error:
            return
        self._last_error = message
        self._notify()

    def clear_last_error(self) -> None:
        """Dismiss the provider-error banner (click on the dock row)."""
        if self._last_error:
            self._last_error = ""
            self._notify()

    def take_notices(self) -> list[TranscriptNotice]:
        """Drain out-of-band lines the frontend should render (dividers, info)."""
        notices = self._notices
        self._notices = []
        return notices

    def _before_tokens(self) -> int:
        last = self.runtime.guard.last_usage
        if last is not None:
            return last.input + last.cache_read
        usage = usage_total(self.runtime.agent.messages)
        return usage.input + usage.output

    async def compact_now(self, *, force: bool = False) -> str | None:
        """Summarize the transcript into a new session file; returns the notice.

        Refuses mid-run (nested model calls would corrupt the transcript) and on
        an empty transcript. ``force`` is the auto path: the controller already
        owns the busy flag, so only the agent streaming check applies.
        """
        if not force and (self._running or self._compacting):
            return None
        if self.runtime.agent.is_streaming:
            return None
        if not self.runtime.agent.messages:
            return None
        before = self._before_tokens()
        old_path = self.runtime.session.path
        path = await compact(self.runtime.agent)
        # Compaction continues the same work: keep its subagent transcripts.
        carry_task_dir(old_path, path)
        # Compaction continues the same work: carry the todo checklist over.
        await self.runtime.switch_session(path, carry_todos=True)
        self.reload_transcript()
        text = f"compacted · {before:,} prompt tokens → summary session"
        self._notices.append(TranscriptNotice("divider", text))
        self._notify()
        return text

    def reload_transcript(self) -> None:
        """Rebuild views from the current agent messages (after a session switch)."""
        self._builder.load(self.runtime.agent.messages)
        # Switching sessions/compaction changes the sidecar the HUD reads and
        # starts a fresh subagent-cost accounting bucket.
        self._subagent_cost = 0.0
        self._subagent_tokens = 0
        self._todos = load_todo_phases(self._session_path())
        self._notify()

    def clear(self) -> None:
        self.runtime.agent.reset()
        self._builder.reset()
        self._attachments.clear()
        todo_tool = getattr(self.runtime, "todo_tool", None)
        if todo_tool is not None:
            todo_tool.clear()
        self._todos = []
        self._subagent_cost = 0.0
        self._subagent_tokens = 0
        self._notify()

    def retry(self) -> str | None:
        """Drop everything after the last user message and run that turn again.

        The user message is dropped too and re-sent, so the transcript never
        ends up with two copies of the same prompt. Shares the truncation logic
        with the REPL through :func:`zenagent.app.retry.prepare_retry`.
        """
        if self._running:
            return None
        old_path = self.runtime.session.path
        prepared = prepare_retry(self.runtime.agent, self.runtime.session)
        if prepared is None:
            return None
        text, new_session = prepared
        carry_task_dir(old_path, new_session.path)
        self.runtime.session = new_session
        todo_tool = getattr(self.runtime, "todo_tool", None)
        if todo_tool is not None:
            todo_tool.bind_session(new_session.path)
        self._todos = load_todo_phases(self._session_path())
        self._builder.load(self.runtime.agent.messages)
        self._task = asyncio.create_task(self._run(text))
        self._notify()
        return text

    async def wait_for_idle(self) -> None:
        while self._task is not None:
            task = self._task
            await asyncio.gather(task, return_exceptions=True)

    # -- run loop -----------------------------------------------------------

    async def _run(self, text: str, images: list[ImageContent] | None = None) -> None:
        self._running = True
        self._last_error = ""
        self._notify()
        started = time.monotonic()
        before_output = usage_total(self.runtime.agent.messages).output
        try:
            await self.runtime.agent.prompt(text, images=images or None)
        finally:
            # Aborts and provider errors skip message_end; do not leave a row
            # ticking after the run is over.
            self._builder.finalize()
            self._turn_ms = int((time.monotonic() - started) * 1000)
            self._turn_output = (
                usage_total(self.runtime.agent.messages).output - before_output
            )
            if self.runtime.agent.termination == "error":
                detail = self.runtime.agent.error_message
                self._last_error = (
                    detail if detail and detail != "error" else "the run failed"
                )
            self._notify()
            # Stay busy (running=True, task set) through the summarizer call so
            # a submit during compaction queues instead of racing it. Queued
            # turns go first: compress only between bursts (Review Focus #1).
            if self.runtime.agent.termination != "aborted" and not self._follow_ups:
                await self._maybe_auto_compact()
            self._running = False
            self._task = None
            self._notify()
            if self._follow_ups and self.runtime.agent.termination != "aborted":
                text, images = self._follow_ups.pop(0)
                self._task = asyncio.create_task(self._run(text, images))
            elif self._follow_ups:
                self._notify()

    async def _maybe_auto_compact(self) -> None:
        """One automatic compaction; failures become a transcript notice."""
        if not should_auto_compact(
            self.runtime.guard.last_usage,
            self.runtime.agent.model.context_window,
        ):
            return
        self._compacting = True
        self._notify()
        try:
            await self.compact_now(force=True)
        except Exception as exc:  # noqa: BLE001 - never break the session
            self._notices.append(
                TranscriptNotice("info", f"auto-compaction failed: {exc}")
            )
        finally:
            self._compacting = False
            self._notify()

    # -- event → view models ------------------------------------------------

    def _on_event(self, event: AgentEvent) -> None:
        changed = self._builder.apply(event)
        if event.type == "tool_execution_end":
            details = dict(getattr(event.result, "details", None) or {})
            if event.tool_name == "task" and "subagent_cost" in details:
                self._subagent_cost += float(details.get("subagent_cost") or 0.0)
                self._subagent_tokens += int(details.get("subagent_tokens") or 0)
            if event.tool_name == "todo" and "phases" in details:
                # The tool returns the canonical snapshot even for a rejected
                # call, so the HUD always reflects the stored list.
                self._todos = coerce_todo_phases(details.get("phases"))
        if changed:
            self._notify()

    def _notify(self) -> None:
        self._on_change()


_TASK_SUBAGENT_STATUSES = ("ok", "error", "aborted", "truncated", "interrupted")


def _subagent_view(card: ToolCallView) -> SubagentView:
    """One ``task`` card as a sidebar row (live card or finished envelope)."""
    details = card.details or {}
    if card.status == "running":
        started = card.started_at
        duration_ms = int((time.monotonic() - started) * 1000) if started else 0
        status = "running"
    else:
        raw = str(details.get("status") or ("error" if card.is_error else "ok"))
        status = (
            raw if raw in _TASK_SUBAGENT_STATUSES else ("error" if card.is_error else "ok")
        )
        duration_ms = int(details.get("duration_ms") or card.duration_ms or 0)
    return SubagentView(
        call_id=card.call_id,
        name=str(card.args.get("name") or "task"),
        status=status,
        duration_ms=duration_ms,
        cost=float(details.get("cost") or 0.0),
        tokens=int(details.get("tokens") or 0),
        has_transcript=False,
    )


