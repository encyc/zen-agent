"""Agent identity for runtime assembly.

An ``AgentProfile`` is the role-side half of runtime assembly: who the agent
is (id, kind, label) and which capabilities it gets (tool policy, persona
fragment, plugins/skills/MCP/templates, session ownership). ``RuntimeOptions``
keeps the generic run knobs; the profile carries the assembly policy that used
to leak into ``Runtime`` as a pile of flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["AgentProfile"]


@dataclass(frozen=True)
class AgentProfile:
    """Who an agent is and what its runtime may assemble.

    ``id`` is stable within a project (``main``, a task child); ``kind`` is the
    ledger vocabulary. The ``work_item``/``task`` correlation stays in the run
    scope because it changes per run, not per profile.
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

    def run_scope(self, **extra) -> dict:
        """The scope a run recorder stamps on this agent's runs."""
        scope: dict = {"kind": self.kind, "agentId": self.id}
        if self.label:
            scope["label"] = self.label
        scope.update(extra)
        return scope
