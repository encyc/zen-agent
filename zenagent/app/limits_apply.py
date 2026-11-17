"""The limits knobs: what this session overrides and how the file lands live.

Moved off Runtime (audit finding 9, slice 2): every function takes the
:class:`~zenagent.app.runtime.Runtime` and the class keeps one-line delegates,
so ``ops_secrets`` and the settings surfaces are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zenagent.app.runtime import Runtime

__all__ = ["apply", "copy_onto", "overrides", "reload_into"]


def copy_onto(runtime: Runtime, fresh) -> None:
    """Copy a loaded config onto the live session's config.

    The field list is the whole adopt contract shared by the wire ``limits``
    op and the TUI ``/limits`` panel, so every settings surface lands a saved
    file identically. ``[task]``/``[zen]``/``[swarm]`` budgets are read when a
    subagent, fleet or room starts — copying them onto ``user_config`` only
    fixes what a *new* one sees; :func:`apply` carries the immediate knobs.
    Callers own the load (and its error mapping): the wire resolves it through
    its own module so a mangled file reports ``config_failed``.
    """
    from zenagent.swarm_settings import SWARM_FIELDS

    live = runtime.user_config
    live.limits = fresh.limits
    for name in (
        "task_max_turns",
        "task_timeout_s",
        "task_max_cost",
        "task_keep_transcripts",
        "task_max_transcript_bytes",
        "task_max_transcript_file_bytes",
        "zen_max_concurrent",
        "zen_batch_window_s",
        "zen_report_limit",
        "zen_worker_mode",
        *SWARM_FIELDS,
    ):
        setattr(live, name, getattr(fresh, name))


def reload_into(runtime: Runtime) -> None:
    """Load the config file and :func:`copy_onto` the live session."""
    from zenagent.config import config_path, load_user_config

    copy_onto(runtime, load_user_config(config_path()))


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
