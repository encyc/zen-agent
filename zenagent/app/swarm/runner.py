"""Run one queued task with a role's agent and record the terminal outcome.

Message creation, mention admission and approval routing live in the manager
(which owns inbox sizes and the gate broker); the runner contributes the
injected prompt, the termination mapping and the chain-cost accounting.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from zenagent.agent.agent import Agent
from zenagent.agent.session import Session
from zenagent.app.compact import maybe_auto_compact
from zenagent.app.swarm.protocol import injected_prompt
from zenagent.app.swarm.roles import RoleManifest
from zenagent.app.swarm.room import RoomMessage, TaskRecord
from zenagent.attachments import read_image_argument
from zenagent.types import AssistantMessage, TextContent

__all__ = ["TaskRunner"]

SUCCESS_TERMINATIONS = {"stop", "tool_terminate"}


def read_message_images(message: RoomMessage) -> list | None:
    """Load a message's attachment paths once per task; ``None`` keeps the
    text-only prompt path byte-identical to before images existed."""
    if not message.images:
        return None
    return [read_image_argument(Path(path)) for path in message.images]


class TaskRunner:
    def __init__(
        self,
        *,
        record_task: Callable[[TaskRecord], None],
        agent: Agent,
        session: Session,
        role: RoleManifest,
        post_message: Callable[[str, str], RoomMessage],
        admit_message: Callable[[RoomMessage], object],
        run_timeout_s: float = 0,
    ) -> None:
        #: Records a task transition; the manager owns it so every state the
        #: room sees (queued → running → terminal) also reaches the UI as a
        #: ``swarm_task`` frame.
        self.record_task = record_task
        self.agent = agent
        self.session = session
        self.role = role
        self.post_message = post_message
        self.admit_message = admit_message
        #: Terminal outcome the manager pins before aborting a run it owns
        #: (role removal, approval timeout, shutdown). ``None`` = user cancel.
        self.abort_outcome: tuple[str, str] | None = None
        #: Wall-clock budget for one run; ``0`` disables the watchdog.
        self.run_timeout_s = max(0.0, float(run_timeout_s))

    async def run(self, task: TaskRecord, message: RoomMessage) -> TaskRecord:
        # A cancel issued before this run started: the manager sets abort_outcome
        # while the agent is idle, where abort() is a no-op and _drive would mint
        # a fresh AbortToken. Capture it so the run honors it instead of running
        # to done; clear it in finally so a later run (e.g. retry) starts clean.
        pre_cancel = self.abort_outcome
        self.abort_outcome = None
        running = replace(task, state="running", updated_at=int(time.time()))
        self.record_task(running)

        before = len(self.agent.messages)

        async def drive() -> None:
            await self.agent.prompt(
                injected_prompt(message, running),
                images=read_message_images(message),
            )
            await self.agent.wait_for_idle()

        try:
            timed_out = False
            if pre_cancel is not None:
                self.abort_outcome = pre_cancel
                termination = "aborted"
            else:
                if self.run_timeout_s > 0:
                    try:
                        await asyncio.wait_for(drive(), self.run_timeout_s)
                    except TimeoutError:
                        timed_out = True
                        self.abort_outcome = ("failed", "timeout")
                        self.agent.abort()
                        await self.agent.wait_for_idle()
                else:
                    await drive()
                termination = self.agent.termination or "stop"

            new_messages = self.agent.messages[before:]
            spent = _run_cost(new_messages)
            updated = replace(
                running,
                chain_cost=round(running.chain_cost + spent, 6),
                run_cost=round(spent, 6),
                updated_at=int(time.time()),
            )
            if timed_out:
                self.post_message(f"Task {task.id} failed: timeout", "system")
                updated = replace(updated, state="failed", reason="timeout")
                self.record_task(updated)
            elif termination in SUCCESS_TERMINATIONS:
                text = _final_text(new_messages) or "(no output)"
                posted = self.post_message(text, "agent")
                updated = replace(updated, state="done", result_message_id=posted.id)
                # Record this run's cost before admitting its mentions: the router
                # gates on the chain aggregate, which the terminal append updates.
                self.record_task(updated)
                self.admit_message(posted)
            elif termination == "aborted":
                state, reason = self.abort_outcome or ("cancelled", "aborted")
                if state == "cancelled":
                    self.post_message(f"Task {task.id} cancelled", "system")
                else:
                    self.post_message(f"Task {task.id} failed: {reason}", "system")
                updated = replace(updated, state=state, reason=reason)
                self.record_task(updated)
            else:
                self.post_message(f"Task {task.id} failed: {termination}", "system")
                updated = replace(updated, state="failed", reason=termination)
                self.record_task(updated)
            await maybe_auto_compact(
                self.agent, self.session, print_fn=lambda _message: None
            )
            return updated
        finally:
            self.abort_outcome = None


def _run_cost(messages: list) -> float:
    return sum(
        message.usage.cost.total
        for message in messages
        if isinstance(message, AssistantMessage)
    )


def _final_text(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return "".join(
                block.text
                for block in message.content
                if isinstance(block, TextContent)
            )
    return ""
