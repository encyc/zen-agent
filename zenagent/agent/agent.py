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

from zenagent.agent.loop import (
    AgentConfig,
    AgentState,
    AgentTool,
    agent_loop,
)
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
        retries: int = 0,
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
        self.retries = retries

        self._state = AgentState(messages=list(messages or []))
        self._listeners: list[AgentListener] = []
        self._steering: list[str] = []
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

    async def prompt(
        self, text: str, images: list[ImageContent] | None = None
    ) -> None:
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

    def reset(self) -> None:
        """Clear the transcript and pending steering input (config is kept)."""
        if self._run is not None:
            raise RuntimeError("cannot reset while a run is active")
        self._state.messages = []
        self._steering.clear()
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
            transform_context=self.transform_context,
            stream_fn=self.stream_fn,
            max_turns=self.max_turns,
            max_cost=self.max_cost,
            retries=self.retries,
        )

    async def _drain_steering(self) -> list[Message]:
        """Aggregate queued lines into one user message and clear the queue."""
        if not self._steering:
            return []
        lines = self._steering
        self._steering = []
        return [UserMessage(content="\n".join(lines), timestamp=int(time.time()))]

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
            async for event in agent_loop(prompt, self._state, self._config(), run.abort):
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
