"""Agent identity and the project's live-agent registry.

An ``AgentProfile`` is the role-side half of runtime assembly: who the agent
is (id, kind, label) and which capabilities it gets (tool policy, persona
fragment, plugins/skills/MCP/templates, session ownership). ``RuntimeOptions``
keeps the generic run knobs; the profile carries the orchestration policy that
used to leak into ``Runtime`` as a pile of role flags.

``Supervisor`` is the project-scoped registry of live handles. Zen workers and
swarm role instances register here, so one query answers "which agents are
alive in this project" without knowing which orchestrator built them. The
schedulers stay where they are for now; the registry is the seam Phase 5 can
build on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zenagent.agent.session import Session
    from zenagent.app.runtime import Runtime

__all__ = [
    "AgentHandle",
    "AgentProfile",
    "Supervisor",
    "register_swarm_agent",
    "swarm_handle_key",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentProfile:
    """Who an agent is and what its runtime may assemble.

    ``id`` is stable within a project (``main``, ``router``, a worker stem, a
    swarm role id); ``kind`` is the ledger/orchestration vocabulary. The
    ``work_item``/``task`` correlation stays in the run scope because it
    changes per run, not per profile.
    """

    id: str = "main"
    kind: str = "coding"
    label: str = ""
    tools_allow: tuple[str, ...] | None = None
    extra_tools: tuple[Any, ...] = ()
    extra_prompt: str = ""
    plugins: bool = True
    skills: bool = True
    templates: bool = True
    mcp: bool = True
    context_files: bool = True
    project_sessions: bool = True

    def run_scope(self, **extra) -> dict:
        """The scope a run recorder stamps on this agent's runs."""
        scope: dict = {"kind": self.kind, "agentId": self.id}
        if self.label:
            scope["label"] = self.label
        scope.update(extra)
        return scope


@dataclass
class AgentHandle:
    """One live runtime plus the identity the supervisor knows it by."""

    key: str
    profile: AgentProfile
    runtime: Runtime
    session: Session
    scope: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "key": self.key,
            "agent": self.profile.id,
            "kind": self.profile.kind,
            "label": self.profile.label,
            "state": "running" if getattr(self.runtime.agent, "is_streaming", False) else "idle",
            "session": str(self.session.path),
            "runId": getattr(self.runtime, "current_run_id", None),
            **self.scope,
        }


class Supervisor:
    """Project-scoped registry of live agent runtimes.

    It does not schedule or prompt: the owning orchestrator still does that.
    It owns the one shared answer to "what is alive in this project", and the
    close path that releases every runtime and its session lease.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._agents: dict[str, AgentHandle] = {}

    def register(
        self,
        key: str,
        *,
        profile: AgentProfile,
        runtime: Runtime,
        session: Session,
        **scope,
    ) -> AgentHandle:
        handle = AgentHandle(
            key=key,
            profile=profile,
            runtime=runtime,
            session=session,
            scope=dict(scope),
        )
        runtime.run_scope = profile.run_scope(**scope)
        self._agents[key] = handle
        return handle

    def unregister(self, key: str) -> AgentHandle | None:
        return self._agents.pop(key, None)

    def get(self, key: str) -> AgentHandle | None:
        return self._agents.get(key)

    def handles(self) -> list[AgentHandle]:
        return list(self._agents.values())

    def snapshots(self) -> list[dict]:
        return [handle.snapshot() for handle in self._agents.values()]

    async def close(self) -> None:
        handles = list(self._agents.values())
        self._agents.clear()
        for handle in handles:
            try:
                await handle.runtime.close()
            except Exception:  # noqa: BLE001 - teardown must not raise
                _log.debug("closing %s failed", handle.key, exc_info=True)


def swarm_handle_key(role_id: str, work_item_id: str | None) -> str:
    """Stable supervisor key for one swarm run unit."""
    return f"{role_id}@{work_item_id or 'adhoc'}"


def register_swarm_agent(
    supervisor: Supervisor,
    profile: AgentProfile,
    runtime: Runtime,
    work_item_id: str | None,
) -> AgentHandle:
    """Register a role runtime under its ``(role, work item)`` key."""
    return supervisor.register(
        swarm_handle_key(profile.id, work_item_id),
        profile=profile,
        runtime=runtime,
        session=runtime.session,
        workItemId=work_item_id,
    )
