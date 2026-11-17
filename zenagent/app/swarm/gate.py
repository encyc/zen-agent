"""How many tasks one message may create, and how many one chain may hold.

This is the swarm's control plane, kept separate from the router so the rules
can be read (and tested) without a manager, a runtime or a model. It is a pure
function of the room's task table: no counters to corrupt, nothing to rebuild
on restart, and the same answer whether it runs now or replays a week later.

The rules exist because ``@`` is both addressing and delegation in this room,
so a polite reply is also a fresh round of work. The model cannot be relied on
to stop — it has no notion of the conversation being finished — so the bound
has to live here. See ``docs/superpowers/specs/2026-11-17-swarm-message-budget-design.md``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from zenagent.app.swarm.room import RoomMessage, SwarmStore
from zenagent.swarm_settings import SwarmLimits

__all__ = [
    "ChainFacts",
    "GateDecision",
    "Intent",
    "MessageGate",
    "SwarmLimits",
]

#: A message that asks for work: a user post, or an agent's ``swarm_post``.
#: Up to ``max_request_fanout`` roles may be handed work.
#: A task's result. It answers; it may return to one role, not broadcast.
#: No mentions: nothing to admit.
Intent = Literal["request", "reply", "note"]

#: ``SwarmLimits`` is re-exported: the knobs live in
#: :mod:`zenagent.swarm_settings` so the config layer can build one without
#: importing the app layer.
@dataclass
class ChainFacts:
    """What the task table already says about one chain."""

    tasks: int = 0
    recent: int = 0
    #: ``(sender id, assignee) -> admitted tasks``. Two rounds between the same
    #: pair is an ask plus a receipt; the third is a ping-pong.
    edges: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass
class GateDecision:
    intent: Intent = "request"
    accepted: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    #: Set when a chain-level gate refused the whole message; every mention is
    #: then in ``rejected`` with the same reason.
    chain_reason: str | None = None
    facts: ChainFacts = field(default_factory=ChainFacts)


class MessageGate:
    def __init__(self, limits: SwarmLimits | None = None) -> None:
        self.limits = limits or SwarmLimits()

    def classify(self, message: RoomMessage, store: SwarmStore) -> Intent:
        """Decide what kind of act this message is, structurally.

        A result is recognised by the room's own bookkeeping — the runner
        records ``result_message_id`` before it admits the message — never by
        what the text claims to be.
        """
        if not message.mentions:
            return "note"
        if message.author_kind != "agent":
            return "request"
        if message.task_id:
            task = store.tasks.get(message.task_id)
            if task is not None and task.result_message_id == message.id:
                return "reply"
        return "request"

    def facts(
        self, store: SwarmStore, chain_id: str, *, now: float | None = None
    ) -> ChainFacts:
        moment = time.time() if now is None else now
        horizon = moment - self.limits.storm_window_s
        facts = ChainFacts()
        for task in store.tasks.values():
            if task.chain_id != chain_id:
                continue
            facts.tasks += 1
            if task.created_at >= horizon:
                facts.recent += 1
            edge = (task.created_by_id, task.assignee)
            facts.edges[edge] = facts.edges.get(edge, 0) + 1
        return facts

    def select(
        self,
        message: RoomMessage,
        store: SwarmStore,
        chain_id: str,
        *,
        role_ids: set[str],
        now: float | None = None,
    ) -> GateDecision:
        """Split a message's mentions into admitted work and refusals."""
        decision = GateDecision(intent=self.classify(message, store))
        known = [role for role in dict.fromkeys(message.mentions) if role in role_ids]
        if not known:
            return decision

        facts = self.facts(store, chain_id, now=now)
        decision.facts = facts
        # The chain-level stops are checked before breadth: once a chain is
        # spent or bursting, no mention from this message is worth admitting.
        if facts.recent >= self.limits.storm_burst:
            decision.chain_reason = "chain_burst"
        elif facts.tasks >= self.limits.max_chain_tasks:
            decision.chain_reason = "chain_tasks"
        if decision.chain_reason is not None:
            decision.rejected = [(role, decision.chain_reason) for role in known]
            return decision

        room = self._fanout(decision.intent)
        denied = "reply_cap" if decision.intent == "reply" else "fanout_cap"
        for index, role in enumerate(known):
            if index >= room:
                decision.rejected.append((role, denied))
            else:
                decision.accepted.append(role)
        return decision

    def _fanout(self, intent: Intent) -> int:
        if intent == "reply":
            return self.limits.max_reply_fanout
        if intent == "request":
            return self.limits.max_request_fanout
        return 0
