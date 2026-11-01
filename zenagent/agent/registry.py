"""Active-set bookkeeping for tools: hidden, default_inactive and hot reload.

The kernel only *declares* intent on :class:`~zenagent.agent.loop.AgentTool`
(``hidden`` / ``default_inactive``); deciding what the model sees is an
assembly-layer concern. This registry is that policy object:

* it owns the current desired set (built-ins + MCP + skills + extensions);
* it remembers per-name activation overrides, so a hot reload does not reset a
  user's choice and a vanishing tool does not leave a stale override;
* it reports whether a :meth:`ToolRegistry.reconcile` changed the model-visible
  set, which is what lets the runtime rebuild the prompt exactly once.

Nothing here talks to the model or the UI; it is a pure data structure over
``AgentTool`` objects.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from zenagent.agent.loop import AgentTool

__all__ = ["ToolRegistry", "tool_fingerprint"]


def tool_fingerprint(tool: AgentTool) -> tuple[str, str, str, str]:
    """What makes two registrations of a tool "the same" for the model.

    The execute callable is deliberately excluded: MCP refresh and extension
    reload produce new objects every time, and rebuilding the prompt just
    because the closure identity changed would defeat the one-rebuild rule.
    Name, description, schema and approval tier are what the model sees.
    """
    return (
        tool.name,
        tool.description,
        json.dumps(tool.parameters, sort_keys=True, default=str),
        tool.approval,
    )


class ToolRegistry:
    """The desired tool set plus the active subset the model may call."""

    def __init__(self, tools: Iterable[AgentTool] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        self._overrides: dict[str, bool] = {}
        self._listeners: list[Callable[[ToolRegistry], None]] = []
        self._version = 0
        self.register_all(tools)

    # -- introspection ------------------------------------------------------

    @property
    def version(self) -> int:
        """Bumped once per change of the model-visible tool set."""
        return self._version

    def all_tools(self) -> list[AgentTool]:
        return list(self._tools.values())

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def active(self) -> list[AgentTool]:
        """Tools the model may call, in registration order."""
        return [tool for tool in self._tools.values() if self.is_active(tool)]

    def active_names(self) -> list[str]:
        return [tool.name for tool in self.active()]

    def is_active(self, tool: AgentTool) -> bool:
        """Whether ``tool`` is visible to the model right now.

        ``hidden`` always wins; then an explicit override; otherwise the tool's
        own ``default_inactive`` declaration decides.
        """
        if tool.hidden:
            return False
        override = self._overrides.get(tool.name)
        if override is not None:
            return override
        return not tool.default_inactive

    # -- mutation -----------------------------------------------------------

    def register(self, tool: AgentTool) -> None:
        """Add or replace one tool (replacing keeps the activation override)."""
        self._tools[tool.name] = tool

    def register_all(self, tools: Iterable[AgentTool]) -> None:
        for tool in tools:
            self.register(tool)

    def set_active(self, name: str, active: bool) -> bool:
        """Activate/deactivate a tool by name; returns whether it changed.

        Hidden tools cannot be activated -- that is the whole point of
        ``hidden`` -- and asking for it raises instead of silently doing
        nothing, so a caller cannot believe it enabled something it did not.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown tool: {name}")
        if tool.hidden and active:
            raise ValueError(f"tool {name!r} is hidden and cannot be activated")
        before = self.is_active(tool)
        self._overrides[name] = active
        after = self.is_active(tool)
        if before == after:
            return False
        self._version += 1
        self._notify()
        return True

    def reconcile(self, tools: Iterable[AgentTool]) -> bool:
        """Replace the desired set; return whether the model-visible set moved.

        Overrides for tools that disappeared are dropped (so a re-added tool
        starts from its declaration again), and listeners fire once when the
        active fingerprints changed -- never per tool.
        """
        before = [tool_fingerprint(tool) for tool in self.active()]
        desired = list(tools)
        self._tools = {tool.name: tool for tool in desired}
        self._overrides = {
            name: value for name, value in self._overrides.items() if name in self._tools
        }
        after = [tool_fingerprint(tool) for tool in self.active()]
        if before == after:
            return False
        self._version += 1
        self._notify()
        return True

    # -- change notification ------------------------------------------------

    def subscribe(self, listener: Callable[[ToolRegistry], None]) -> Callable[[], None]:
        """Register ``listener`` for change events; returns the unsubscribe fn."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self)
