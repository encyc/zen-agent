"""ChatController: one run at a time, view-model state, pending queue."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from zenagent.agent.session import Session
from zenagent.agent.subagents import carry_task_dir
from zenagent.app import records
from zenagent.app.attachments_state import Attachments
from zenagent.app.compact import compact, should_auto_compact
from zenagent.app.context import build_context_report
from zenagent.app.git import git_status
from zenagent.app.pending import PendingQueue
from zenagent.app.retry import prepare_retry
from zenagent.app.runtime import Runtime
from zenagent.app.subagent_tracker import SubagentTracker
from zenagent.app.support import cache_hit_ratio, context_occupancy
from zenagent.app.views import (
    JobLogView,
    JobView,
    MessageView,
    PendingLane,
    PendingMessage,
    RunStatus,
    SubagentView,
    ToolCallView,
    TranscriptBuilder,
    TranscriptNotice,
    job_views,
    last_request_usage,
    sort_jobs,
    usage_total,
)
from zenagent.events import AgentEvent
from zenagent.i18n import t
from zenagent.tools.jobs import get_job_manager
from zenagent.tools.todo import coerce_todo_phases, load_todo_phases
from zenagent.types import ImageContent

__all__ = ["ChatController"]


def _inflight_task_ids() -> set[str]:
    """Call ids of background children still running (they pin their store)."""
    from zenagent.agent.subagents_runtime import get_task_manager

    return get_task_manager().running_call_ids()


class ChatController:
    """Drives the agent and exposes snapshots a frontend can render."""

    def __init__(
        self, runtime: Runtime, *, on_change: Callable[[], None] = lambda: None
    ) -> None:
        self.runtime = runtime
        self._on_change = on_change
        self._builder = TranscriptBuilder()
        #: Subagent transcripts, sidebar rows and spend (see SubagentTracker).
        self._subagents = SubagentTracker(self._notify)
        #: Pending user input, in creation order: ``queue`` entries wait for the
        #: run to end, ``steer`` entries are injected at the next step boundary.
        self._pending_queue = PendingQueue()
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
        self._attachments = Attachments()
        #: Latest canonical todo phases (``details["phases"]`` shape) for the
        #: HUD. Seeded from the current session's sidecar so resume/start shows
        #: the list before the first tool call of the restored transcript.
        self._todos: list[dict] = load_todo_phases(self._session_path())
        runtime.agent.subscribe(self._on_event)

    # -- snapshots ----------------------------------------------------------

    @property
    def transcript(self) -> list[ToolCallView]:
        return list(self._builder.views)

    def reload_todos(self) -> None:
        """Re-read the session todo sidecar (after the /todo editor wrote it)."""
        self._todos = load_todo_phases(self._session_path())
        self._notify()

    def last_assistant_text(self) -> str:
        """The most recent assistant prose, or ``''`` when there is none.

        Plan review reads this: plan mode is still "read-only + a prompt note",
        so the plan itself is just the model's last answer.
        """
        for view in reversed(self.transcript):
            if getattr(view, "kind", None) == "assistant" and view.text.strip():
                return view.text.strip()
        return ""

    @property
    def todos(self) -> list[dict]:
        """Current todo phases for the HUD (``[]`` while there is no list)."""
        return list(self._todos)

    def _session_path(self) -> Path | None:
        session = getattr(self.runtime, "session", None)
        return Path(session.path) if session is not None else None

    @property
    def busy(self) -> bool:
        """True while a run or an automatic compaction owns the agent.

        ``submit`` sets the task synchronously, so a frontend can tell "busy"
        apart from "the run has not started its first await yet".
        """
        return self._task is not None or self._compacting

    @property
    def status(self) -> RunStatus:
        agent = self.runtime.agent
        usage = usage_total(agent.messages)
        # Occupancy is the last request, not the session total: the newest
        # assistant turn is already inside the prompt the next request sends.
        last = last_request_usage(agent.messages)
        context_used = (
            context_occupancy(last, agent.model.api) if last is not None else 0
        )
        subagents = self._subagent_views()
        git = git_status(Path.cwd())
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
            follow_ups=self._pending_queue.count("queue"),
            steering=agent.pending_steering,
            pending=self._pending_queue.items,
            session_name=Path(self.runtime.session.path).stem,
            cwd=str(Path.cwd()),
            mode=self.runtime.mode,
            git_branch=git.branch if git else "",
            git_staged=git.staged if git else 0,
            git_dirty=git.dirty if git else 0,
            git_untracked=git.untracked if git else 0,
            todos=self.todos,
            subagent_cost=self._subagents.cost,
            subagent_tokens=self._subagents.tokens,
            jobs=self._job_views(),
            subagents=subagents,
            active_subagent=next(
                (view.call_id for view in subagents if view.status == "running"), None
            ),
            context=build_context_report(
                getattr(self.runtime, "prompt_report", None),
                agent.tools,
                agent.messages,
                self.runtime.guard.last_usage,
                context_used,
                agent.model.context_window,
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
        return self._subagents.views(
            list(self._builder.cards.values()), session_path=self._session_path()
        )

    def job_log(self, job_id: str) -> JobLogView | None:
        """One background job's bounded output; None when it is already retired."""
        try:
            job = get_job_manager().snapshot(job_id)
        except RuntimeError:  # no running loop
            return None
        if job is None:
            return None
        return JobLogView(
            id=str(job.id),
            command=str(job.command),
            status=str(job.status),
            duration_ms=job.duration_ms,
            exit_code=job.exit_code,
            output=job.output,
            truncated=bool(job.truncated),
        )

    # -- subagent transcripts -----------------------------------------------

    def on_subagent_event(self, call_id: str, kind: str, payload: dict) -> None:
        """Live hook from ``tools.task`` (the module-level observer)."""
        self._subagents.on_event(call_id, kind, payload)

    def subagent_transcript(
        self, call_id: str
    ) -> list[MessageView | ToolCallView] | None:
        """Live buffer first, persisted sidecar otherwise (after resume)."""
        return self._subagents.transcript(call_id, session_path=self._session_path())

    # -- commands -----------------------------------------------------------

    async def submit(
        self, text: str, *, images: list[ImageContent] | None = None
    ) -> None:
        """Start a run, or queue the text when one is already running.

        ``images`` overrides the pending attachment list; otherwise whatever was
        attached with ``/image`` rides along and is cleared once the run starts.
        """
        pending = self._attachments.take(images)
        if self._running or self._compacting:
            self.queue_follow_up(text, pending)
            return
        self._task = asyncio.create_task(self._run(text, pending))

    @property
    def attachments(self) -> list[ImageContent]:
        """Pending images attached but not yet sent."""
        return self._attachments.all()

    def attach_image(self, block: ImageContent) -> None:
        self._attachments.add(block)
        self._notify()

    def remove_attachment(self, index: int) -> None:
        if self._attachments.remove(index):
            self._notify()

    def clear_attachments(self) -> None:
        if self._attachments.clear():
            self._notify()

    def queue_follow_up(
        self, text: str, images: list[ImageContent] | None = None
    ) -> None:
        """Queue a follow-up; ``images=None`` takes the pending attachments."""
        if not text.strip():
            return
        blocks = self._attachments.take(images)
        self._pending_queue.queue(text, blocks)
        self._notify()

    def clear_pending(self) -> int:
        """Drop every pending message (both lanes); returns how many went."""
        cleared = self._pending_queue.clear()
        if not cleared:
            return 0
        # Undelivered steering also lives on the agent; reclaim it or it would
        # be injected into the next run's first request after the user cleared.
        self.runtime.agent.take_steering()
        self._notify()
        return cleared

    def drop_pending(self) -> int:
        """Discard pending messages, leaving one notice when anything went.

        Used when the conversation itself changes (session switch, undo, fork,
        retry): queued text written for the old transcript must not be sent into
        the new one.
        """
        cleared = self.clear_pending()
        if cleared:
            self._notices.append(
                TranscriptNotice("info", t("notice.pending_dropped", n=cleared))
            )
            self._notify()
        return cleared

    def clear_follow_ups(self) -> None:
        """Backwards-compatible alias for :meth:`clear_pending`."""
        self.clear_pending()

    def dequeue_follow_up(self) -> str | None:
        """Take the most recently queued follow-up back out of the queue.

        omp's dequeue semantics: the last thing you queued is the one you most
        likely want to edit, so pop from the end. Returns ``None`` when empty.
        """
        taken = self._pending_queue.dequeue_latest()
        if taken is None:
            return None
        text, images = taken
        if images:
            self._attachments.prepend(images)  # back into the composer
        self._notify()
        return text

    def _add_pending(
        self,
        text: str,
        images: list[ImageContent] | None,
        lane: PendingLane,
    ) -> PendingMessage:
        """Append one pending entry and return its view-model snapshot."""
        return self._pending_queue.add(text, images, lane)

    def steer(self, text: str) -> None:
        """Deliver ``text`` at the running turn's next step boundary.

        Falls back to the follow-up queue when no run is streaming (compaction,
        or a run that just finished), so nothing is silently stranded.
        """
        if not text.strip():
            return
        if not self.runtime.agent.is_streaming:
            self.queue_follow_up(text)
            return
        self._add_pending(text, [], "steer")
        self.runtime.agent.steer(text)
        self._notify()

    def promote(self, pending_id: int) -> str:
        """Move one queued entry into the steering lane.

        Returns ``"ok"``, ``"blocked_images"`` (the steering channel is
        text-only), ``"not_running"`` (no step boundary to inject at), or
        ``"missing"`` (unknown id, or already steering).
        """
        item = self._pending_queue.find(pending_id)
        if item is None or item.message.lane != "queue":
            return "missing"
        if item.images:
            return "blocked_images"
        if not self.runtime.agent.is_streaming:
            return "not_running"
        self._pending_queue.remove(item)
        self._add_pending(item.message.text, [], "steer")
        self.runtime.agent.steer(item.message.text)
        self._notify()
        return "ok"

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

    @property
    def notices_snapshot(self) -> list[TranscriptNotice]:
        """Read-only copy of the pending notices; draining is the caller's call."""
        return list(self._notices)

    def _before_tokens(self) -> int:
        last = last_request_usage(self.runtime.agent.messages)
        if last is not None:
            return context_occupancy(last, self.runtime.agent.model.api)
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
        path = await compact(
            self.runtime.agent,
            derived_from=old_path.stem,
            reason="auto" if force else "manual",
            tokens_before=before,
            carry_from=Session(old_path),
        )
        # Compaction continues the same work: keep its subagent transcripts.
        carry_task_dir(old_path, path, skip=_inflight_task_ids())
        # Compaction continues the same work: carry the todo checklist over.
        await self.runtime.switch_session(path, carry_todos=True)
        self.reload_transcript()
        text = t("notice.compacted", tokens=before)
        self._notices.append(
            TranscriptNotice("divider", text, source=records.COMPACTION)
        )
        self._notify()
        return text

    def reload_transcript(self, *, drop_pending: bool = False) -> None:
        """Rebuild views from the current agent messages (after a session switch).

        ``drop_pending`` also discards anything queued for the old conversation:
        callers that replace or rewind the transcript pass it, compaction does
        not (queued turns still belong to the same session).
        """
        if drop_pending:
            self.drop_pending()
        session = getattr(self.runtime, "session", None)
        if session is not None:
            session.ensure_loaded()
        records = list(getattr(session, "records", []) or [])
        self._builder.load(self.runtime.agent.messages, records)
        # Switching sessions/compaction changes the sidecar the HUD reads and
        # starts a fresh subagent-cost accounting bucket.
        self._subagents.reset()
        self._todos = load_todo_phases(self._session_path())
        self._notify()

    def clear(self) -> None:
        self.drop_pending()
        self.runtime.agent.reset()
        self._builder.reset()
        self._subagents.reset()
        self._attachments.clear()  # always drops, notify fires below
        todo_tool = getattr(self.runtime, "todo_tool", None)
        if todo_tool is not None:
            todo_tool.clear()
        self._todos = []
        self._notify()

    def retry(self) -> str | None:
        """Drop everything after the last user message and run that turn again.

        The user message is dropped too and re-sent, so the transcript never
        ends up with two copies of the same prompt. Shares the truncation logic
        with the REPL through :func:`zenagent.app.retry.prepare_retry`.
        """
        if self._running:
            return None
        self.drop_pending()
        old_path = self.runtime.session.path
        prepared = prepare_retry(self.runtime.agent, self.runtime.session)
        if prepared is None:
            return None
        text, new_session = prepared
        carry_task_dir(old_path, new_session.path, skip=_inflight_task_ids())
        self.runtime.session = new_session
        todo_tool = getattr(self.runtime, "todo_tool", None)
        if todo_tool is not None:
            todo_tool.bind_session(new_session.path)
        self._todos = load_todo_phases(self._session_path())
        new_session.ensure_loaded()
        self._builder.load(self.runtime.agent.messages, list(new_session.records))
        self._subagents.clear_transcripts()
        self._task = asyncio.create_task(self._run(text))
        self._notify()
        return text

    async def wait_for_idle(self) -> None:
        while self._task is not None:
            task = self._task
            await asyncio.gather(task, return_exceptions=True)

    # -- run loop -----------------------------------------------------------

    async def _run(
        self, text: str | None, images: list[ImageContent] | None = None
    ) -> None:
        self._running = True
        self._last_error = ""
        self._notify()
        started = time.monotonic()
        before_output = usage_total(self.runtime.agent.messages).output
        try:
            if text is None:
                try:
                    await self.runtime.agent.continue_()
                except ValueError:
                    # Nothing to continue: keep the steering text visible
                    # instead of raising out of the task.
                    self._demote_steering()
            else:
                await self.runtime.agent.prompt(text, images=images or None)
        finally:
            termination = self.runtime.agent.termination
            # Aborts and provider errors skip message_end; do not leave a row
            # ticking after the run is over.
            self._builder.finalize()
            self._turn_ms = int((time.monotonic() - started) * 1000)
            self._turn_output = (
                usage_total(self.runtime.agent.messages).output - before_output
            )
            if termination == "error":
                detail = self.runtime.agent.error_message
                self._last_error = (
                    detail
                    if detail and detail != "error"
                    else t("notice.run_failed")
                )
            self._notify()
            # A steer typed after the last step boundary never reached the
            # model. A normal finish continues the run so the model answers it
            # at the next request; any other ending demotes it to a visible
            # follow-up (never silently stranded, never auto-retried).
            continuation = False
            if self.runtime.agent.pending_steering:
                if termination == "stop":
                    continuation = True
                else:
                    self._demote_steering()
            # Stay busy (running=True, task set) through the summarizer call so
            # a submit during compaction queues instead of racing it. Queued
            # turns go first: compress only between bursts (Review Focus #1).
            if termination != "aborted" and not self._pending_queue.items:
                await self._maybe_auto_compact()
            self._running = False
            self._task = None
            self._notify()
            if continuation:
                self._task = asyncio.create_task(self._run(None))
            elif termination == "stop":
                # Only a normal finish auto-drains the queue. An error/cap
                # ending leaves the pending rows visible and the banner up;
                # the user decides when to run again (Review Focus #4).
                item = self._pending_queue.take_next_queue_item()
                if item is not None:
                    self._task = asyncio.create_task(
                        self._run(item.message.text, item.images)
                    )

    def _demote_steering(self) -> None:
        """Turn undelivered steering into visible queue entries (nothing lost)."""
        texts = self.runtime.agent.take_steering()
        if not texts:
            return
        self._pending_queue.remove_lane("steer")
        for text in texts:
            self._add_pending(text, [], "queue")
        self._notify()

    async def _maybe_auto_compact(self) -> None:
        """One automatic compaction; failures become a transcript notice."""
        if not getattr(self.runtime, "auto_compact", True):
            return
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
                TranscriptNotice(
                    "info", t("notice.auto_compaction_failed", error=exc)
                )
            )
        finally:
            self._compacting = False
            self._notify()

    # -- event → view models ------------------------------------------------

    def _on_event(self, event: AgentEvent) -> None:
        changed = self._builder.apply(event)
        if (
            event.type in ("tool_execution_start", "tool_execution_end")
            and event.tool_name == "task"
        ):
            self._subagents.invalidate_disk()
        if event.type == "tool_execution_end":
            # The ``tool_end`` record is the runtime's (so headless sessions
            # carry it too); here we only fold the same event into view state.
            details = dict(getattr(event.result, "details", None) or {})
            if event.tool_name in ("task", "wait") and "subagent_cost" in details:
                self._subagents.add_spend(
                    float(details.get("subagent_cost") or 0.0),
                    int(details.get("subagent_tokens") or 0),
                )
            if event.tool_name == "todo" and "phases" in details:
                # The tool returns the canonical snapshot even for a rejected
                # call, so the HUD always reflects the stored list.
                self._todos = coerce_todo_phases(details.get("phases"))
        # The loop drains steering before yielding the injected user message, so
        # an empty agent queue means every mirror entry has been delivered.
        if (
            self.runtime.agent.pending_steering == 0
            and self._pending_queue.count("steer")
        ):
            self._pending_queue.remove_lane("steer")
            changed = True
        if changed:
            self._notify()

    def _notify(self) -> None:
        self._on_change()



