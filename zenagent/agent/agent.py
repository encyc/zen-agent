"""Stateful wrapper around :func:`~zenagent.agent.loop.agent_loop`.

The loop is a pure function: it takes a transcript, streams one run, and
appends messages to the state it was handed. Harnesses (headless CLI, REPL)
need something with a longer life than one run: they hold the transcript, want
to observe events, inject input mid-run, cancel, and know when things are idle.

``Agent`` is that shell — it mirrors what pi-agent-core's ``Agent`` class does
on top of its ``agentLoop()`` function:

* owns the transcript (``messages``) and the run configuration;
* ``subscribe`` for ``AgentEvent`` consumers, delivered serially;
* ``steer`` queues user input that the loop drains after a tool round;
* ``follow_up`` queues input that the loop drains only when the run would
  otherwise stop (a plain end of turn) — steering interrupts, follow-up
  extends;
* ``abort`` cancels the current run cooperatively (the run still ends with an
  ``aborted`` terminal state, never by raising);
* ``wait_for_idle`` resolves once the run finished *and* its ``agent_end``
  listeners settled;
* ``prompt`` / ``continue_`` / ``reset`` / ``replace_messages`` for driving it.

Only one run may be active at a time.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from zenagent.agent.guard import LOOP_GUARD_THRESHOLD
from zenagent.agent.loop import (
    AgentConfig,
    AgentState,
    AgentTool,
    agent_loop,
)
from zenagent.agent.result_budget import MAX_RESULT_BYTES
from zenagent.events import AbortToken, AgentEvent
from zenagent.providers.catalog import Model
from zenagent.providers.registry import StreamFn
from zenagent.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    UserMessage,
)

__all__ = ["Agent"]

#: Listener signature: receives every event of a run, may be sync or async.
AgentListener = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass
class _Run:
    """Bookkeeping for one in-flight run."""

    abort: AbortToken
    #: Resolved when the run (including its listeners) has settled.
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error_message: str | None = None


class Agent:
    """Transcript + configuration, driven one run at a time.

    Run-visible attributes (``model``, ``thinking``, ``tools``) are read when a
    run starts, so changing them takes effect on the next turn — that is how
    the REPL's ``/model`` and ``/thinking`` commands work.
    """

    def __init__(
        self,
        model: Model,
        system_prompt: str,
        tools: list[AgentTool] | None = None,
        api_key: str = "",
        *,
        messages: list[Message] | None = None,
        thinking: str | None = None,
        session_id: str | None = None,
        stream_fn: StreamFn | None = None,
        before_tool_call: Callable | None = None,
        after_tool_call: Callable | None = None,
        transform_context: Callable | None = None,
        max_turns: int | None = None,
        max_cost: float | None = None,
        loop_guard_threshold: int = LOOP_GUARD_THRESHOLD,
        max_result_bytes: int = MAX_RESULT_BYTES,
        retries: int = 0,
        artifact_sink: Callable[[str, str], Path | None] | None = None,
        on_retry: Callable[[AssistantMessage, int, int, float], None] | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.tools: list[AgentTool] = list(tools or [])
        self.api_key = api_key
        self.thinking = thinking
        self.session_id = session_id
        self.stream_fn = stream_fn
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.transform_context = transform_context
        self.max_turns = max_turns
        self.max_cost = max_cost
        #: ``[limits.agent]`` knobs, read when the next run starts.
        self.loop_guard_threshold = loop_guard_threshold
        self.max_result_bytes = max_result_bytes
        self.retries = retries
        #: Retry observer (``(message, attempt, max_retries, delay_s)``), wired
        #: by the app layer to the session's side-channel recorder.
        self.on_retry = on_retry
        #: Oversized tool results spill here (``(tool_name, full_text) -> path``).
        self.artifact_sink = artifact_sink

        self._state = AgentState(messages=list(messages or []))
        self._listeners: list[AgentListener] = []
        self._steering: list[str] = []
        self._follow_up: list[str] = []
        self._run: _Run | None = None
        self._error_message: str | None = None

    # -- observability ------------------------------------------------------

    @property
    def messages(self) -> list[Message]:
        """A shallow copy of the transcript."""
        return list(self._state.messages)

    @property
    def is_streaming(self) -> bool:
        return self._run is not None

    @property
    def is_idle(self) -> bool:
        return self._run is None

    @property
    def termination(self) -> str | None:
        """Why the most recent run ended (``"stop"``, ``"max_cost"``, ...)."""
        return self._state.termination

    @property
    def error_message(self) -> str | None:
        """Error/abort detail from the most recent run, if any."""
        return self._error_message

    def subscribe(self, listener: AgentListener) -> Callable[[], None]:
        """Register an event listener; returns an unsubscribe callable."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    # -- queueing -----------------------------------------------------------

    @property
    def pending_steering(self) -> int:
        """Steering messages queued but not yet consumed by the loop."""
        return len(self._steering)

    def steer(self, text: str) -> None:
        """Queue input to be injected after the current tool round."""
        self._steering.append(text)

    def take_steering(self) -> list[str]:
        """Remove and return queued steering input (abort / clear cleanup)."""
        texts = self._steering
        self._steering = []
        return texts

    @property
    def pending_follow_up(self) -> int:
        """Follow-up messages queued but not yet consumed by the loop."""
        return len(self._follow_up)

    def follow_up(self, text: str) -> None:
        """Queue input to be injected when the run would otherwise stop.

        Unlike :meth:`steer` (which interrupts after the current tool round),
        a follow-up never interrupts: it only extends a run that reached a
        plain end of turn. Error, abort, cost and terminate stops leave the
        queue untouched for the next run.
        """
        self._follow_up.append(text)

    def drop_steering(self, index: int) -> bool:
        """Remove one queued steering entry by its delivery position.

        The controller keeps a visible row per steering message; taking that
        row back must take the queued text with it or the model still sees it.
        """
        if 0 <= index < len(self._steering):
            del self._steering[index]
            return True
        return False

    def take_follow_up(self) -> list[str]:
        """Remove and return queued follow-up input (abort / clear cleanup)."""
        texts = self._follow_up
        self._follow_up = []
        return texts

    # -- control ------------------------------------------------------------

    def abort(self) -> None:
        """Cooperatively cancel the current run (no-op when idle)."""
        if self._run is not None:
            self._run.abort.abort()

    async def wait_for_idle(self) -> None:
        """Wait until no run is active and its listeners have settled."""
        run = self._run
        if run is not None:
            await run.done.wait()

    async def prompt(self, text: str, images: list[ImageContent] | None = None) -> None:
        """Start a run with a new user message and return when it settles."""
        if images:
            await self._drive([*images, TextContent(text=text)])
        else:
            await self._drive(text)

    async def continue_(self) -> None:
        """Start a run without adding a message (retry/replay), e.g. after a resume."""
        if not self._state.messages:
            raise ValueError("cannot continue: the transcript is empty")
        await self._drive(None)

    def replace_messages(self, messages: list[Message]) -> None:
        """Replace the whole transcript (used by ``--resume`` and ``/compact``)."""
        if self._run is not None:
            raise RuntimeError("cannot replace messages while a run is active")
        self._state.messages = list(messages)

    def project_transcript(self, view: list[Message]) -> None:
        """Swap the transcript in place for ``view`` (no run-state check).

        The runtime uses this to align the live view with a persisted rewind
        branch. Safe in the moments when the loop is suspended — it awaits
        ``transform_context`` — because the loop reads ``state.messages``
        fresh each iteration and later appends land on the swapped list.
        """
        self._state.messages[:] = view

    def reset(self) -> None:
        """Clear the transcript and pending queued input (config is kept)."""
        if self._run is not None:
            raise RuntimeError("cannot reset while a run is active")
        self._state.messages = []
        self._steering.clear()
        self._follow_up.clear()
        self._error_message = None

    # -- internals ----------------------------------------------------------

    def _config(self) -> AgentConfig:
        return AgentConfig(
            model=self.model,
            system_prompt=self.system_prompt,
            tools=list(self.tools),
            thinking=self.thinking,
            api_key=self.api_key,
            session_id=self.session_id,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
            get_steering_messages=self._drain_steering,
            get_followup_messages=self._drain_follow_up,
            transform_context=self.transform_context,
            stream_fn=self.stream_fn,
            max_turns=self.max_turns,
            max_cost=self.max_cost,
            loop_guard_threshold=self.loop_guard_threshold,
            max_result_bytes=self.max_result_bytes,
            retries=self.retries,
            artifact_sink=self.artifact_sink,
            on_retry=self.on_retry,
        )

    async def _drain_steering(self) -> list[Message]:
        """Aggregate queued lines into one user message and clear the queue."""
        if not self._steering:
            return []
        lines = self._steering
        self._steering = []
        return [
            UserMessage(
                content="\n".join(lines),
                timestamp=int(time.time()),
                steering=True,
            )
        ]

    async def _drain_follow_up(self) -> list[Message]:
        """Aggregate queued follow-up lines into one plain user message."""
        if not self._follow_up:
            return []
        lines = self._follow_up
        self._follow_up = []
        return [
            UserMessage(
                content="\n".join(lines),
                timestamp=int(time.time()),
            )
        ]

    async def _dispatch(self, event: AgentEvent) -> None:
        """Deliver one event to every listener, serially, isolating failures."""
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - a bad listener must not kill the run
                print(
                    f"[zenagent] agent listener failed on {event.type}: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

    async def _drive(
        self, prompt: str | list[TextContent | ImageContent] | None
    ) -> None:
        if self._run is not None:
            raise RuntimeError("Agent is already running; await wait_for_idle() first")
        run = _Run(abort=AbortToken())
        self._run = run
        self._error_message = None
        try:
            async for event in agent_loop(
                prompt, self._state, self._config(), run.abort
            ):
                await self._dispatch(event)
                if event.type == "agent_end":
                    run.error_message = _terminal_error(self._state.messages)
        except BaseException:
            # Task cancellation propagates, but the run must still become idle.
            self._error_message = run.error_message or "run cancelled"
            raise
        finally:
            self._error_message = run.error_message
            self._run = None
            run.done.set()


def _terminal_error(messages: list[Message]) -> str | None:
    """Error/abort text from the run's last assistant message, if it failed."""
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            if message.stop_reason in ("error", "aborted"):
                return message.error_message or message.stop_reason
            return None
    return None
