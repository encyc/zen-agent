"""The limits knobs: what this session overrides and how the file lands live.

Moved off Runtime (audit finding 9, slice 2): every function takes the
:class:`~zenagent.app.runtime.Runtime` and the class keeps one-line delegates,
so ``ops_secrets`` and the settings surfaces are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zenagent.app.runtime import Runtime

__all__ = ["apply", "overrides"]


def overrides(runtime: Runtime) -> dict[str, str]:
    """Knobs this session overrides, mapped to the switch that did it.

    The settings page shows effective values, so it has to know that
    ``--max-turns 20`` (or ``--no-skills``) beats the file for this run.
    Keys are :class:`~zenagent.limits.Limit` attrs.
    """
    active: dict[str, str] = {}
    options = runtime._options
    if options is not None and options.max_turns is not None:
        active["max_turns"] = f"--max-turns {options.max_turns}"
    if not runtime.skills_enabled:
        active["max_index_bytes"] = "--no-skills"
        active["max_body_bytes"] = "--no-skills"
    if not runtime.profile.context_files:
        active["max_context_bytes"] = "--no-context-files"
    if not runtime.auto_compact:
        active["compact_threshold"] = "--no-auto-compact"
    return active


def apply(runtime: Runtime) -> None:
    """Push the *immediate* ``[limits]`` knobs into the live agent.

    The rest (turn cap, context injection, compaction threshold, per-result
    budget) are bound when the session starts; the settings page labels them
    "new sessions" rather than pretending otherwise.
    """
    agent = getattr(runtime, "agent", None)
    if agent is not None:
        agent.loop_guard_threshold = runtime.limits.loop_guard_threshold
    # ``_recompose`` (not ``rediscover``) rebuilds the tool list and prompt
    # from the already-loaded extensions: a budget change must not re-run
    # extension modules.
    runtime._recompose()
