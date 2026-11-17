"""The ``[swarm]`` section: room policy, its defaults and its parser.

One room-wide policy governs how much work a swarm may create: how many role
runs go at once, how deep and how expensive a delegation chain may get, and —
the message budget — how much breadth one message and one chain may produce.

The section is global-only, like ``[safety]`` and ``[backup]``: a committed
``<repo>/.zenagent/config.toml`` must not be able to widen the budgets of the
machine it is cloned onto.

This module lives outside :mod:`zenagent.config` so the parser can validate its
own table without the two importing each other; ``UserConfig`` inherits
:class:`SwarmPolicy`, so every knob stays a flat ``swarm_*`` attribute and the
limits registry keeps its one-``attr``-per-knob model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zenagent.config_fields import (
    non_negative_float,
    positive_float,
    positive_int,
    unknown_section_keys,
)

__all__ = [
    "DEFAULT_SWARM_MAX_CHAIN_COST",
    "DEFAULT_SWARM_MAX_CHAIN_DEPTH",
    "DEFAULT_SWARM_MAX_CHAIN_TASKS",
    "DEFAULT_SWARM_MAX_CONCURRENT",
    "DEFAULT_SWARM_MAX_EDGE_REPEATS",
    "DEFAULT_SWARM_MAX_REPLY_FANOUT",
    "DEFAULT_SWARM_MAX_REQUEST_FANOUT",
    "DEFAULT_SWARM_RUNTIME_IDLE_S",
    "DEFAULT_SWARM_STORM_BURST",
    "DEFAULT_SWARM_STORM_WINDOW_S",
    "SWARM_FIELDS",
    "SWARM_KEYS",
    "SwarmLimits",
    "SwarmPolicy",
    "parse_swarm_table",
]

#: Keys accepted inside ``[swarm]`` (room policy, global-only).
SWARM_KEYS = frozenset(
    {
        "max_concurrent",
        "max_chain_depth",
        "max_chain_cost",
        "runtime_idle_s",
        "max_request_fanout",
        "max_reply_fanout",
        "max_edge_repeats",
        "max_chain_tasks",
        "storm_window_s",
        "storm_burst",
    }
)

#: Default number of swarm role runs in flight per room.
DEFAULT_SWARM_MAX_CONCURRENT = 3
#: Default cap on how deep a delegation chain may grow.
DEFAULT_SWARM_MAX_CHAIN_DEPTH = 8
#: Default spend cap for one delegation chain, in USD.
DEFAULT_SWARM_MAX_CHAIN_COST = 5.0
#: Default idle time before a (role, work item) runtime is closed; 0 disables.
DEFAULT_SWARM_RUNTIME_IDLE_S = 600.0
#: Roles one request (a user post or ``swarm_post``) may hand work to.
DEFAULT_SWARM_MAX_REQUEST_FANOUT = 4
#: Roles one result may hand work to. A result answers; it does not broadcast.
DEFAULT_SWARM_MAX_REPLY_FANOUT = 1
#: Rounds one (sender, target) pair may have inside one chain: ask + receipt.
DEFAULT_SWARM_MAX_EDGE_REPEATS = 2
#: Tasks one chain may create before it is spent. Counted at admission, not
#: settled from spend afterwards, so parallel branches cannot overshoot.
DEFAULT_SWARM_MAX_CHAIN_TASKS = 24
#: Window for the burst breaker; 0 disables it.
DEFAULT_SWARM_STORM_WINDOW_S = 60.0
#: Tasks one chain may create inside that window before it is frozen.
DEFAULT_SWARM_STORM_BURST = 12


@dataclass
class SwarmPolicy:
    """The ``[swarm]`` fields ``UserConfig`` inherits.

    Flat ``swarm_*`` attributes on purpose: ``zenagent.limits`` addresses every
    knob by one ``attr`` name, and the settings page round-trips those names.
    """

    #: ``[swarm]``: role runs in flight per room (global-only)
    swarm_max_concurrent: int = DEFAULT_SWARM_MAX_CONCURRENT
    #: ``[swarm]``: delegation chain depth cap
    swarm_max_chain_depth: int = DEFAULT_SWARM_MAX_CHAIN_DEPTH
    #: ``[swarm]``: delegation chain spend cap in USD
    swarm_max_chain_cost: float = DEFAULT_SWARM_MAX_CHAIN_COST
    #: ``[swarm]``: idle seconds before a run unit's runtime is reclaimed
    swarm_runtime_idle_s: float = DEFAULT_SWARM_RUNTIME_IDLE_S
    #: ``[swarm]``: roles one request may hand work to
    swarm_max_request_fanout: int = DEFAULT_SWARM_MAX_REQUEST_FANOUT
    #: ``[swarm]``: roles one result may hand work to
    swarm_max_reply_fanout: int = DEFAULT_SWARM_MAX_REPLY_FANOUT
    #: ``[swarm]``: rounds one pair may have inside one chain
    swarm_max_edge_repeats: int = DEFAULT_SWARM_MAX_EDGE_REPEATS
    #: ``[swarm]``: tasks one chain may create
    swarm_max_chain_tasks: int = DEFAULT_SWARM_MAX_CHAIN_TASKS
    #: ``[swarm]``: burst-breaker window in seconds; 0 disables the breaker
    swarm_storm_window_s: float = DEFAULT_SWARM_STORM_WINDOW_S
    #: ``[swarm]``: tasks per chain inside the window before it is frozen
    swarm_storm_burst: int = DEFAULT_SWARM_STORM_BURST


#: The ten ``swarm_*`` attribute names, in registry order.
SWARM_FIELDS: tuple[str, ...] = (
    "swarm_max_concurrent",
    "swarm_max_chain_depth",
    "swarm_max_chain_cost",
    "swarm_runtime_idle_s",
    "swarm_max_request_fanout",
    "swarm_max_reply_fanout",
    "swarm_max_edge_repeats",
    "swarm_max_chain_tasks",
    "swarm_storm_window_s",
    "swarm_storm_burst",
)


def parse_swarm_table(table: dict, target: Path) -> dict[str, int | float]:
    """Validate ``[swarm]`` and return its ten keyword arguments.

    Ranges come from the caller, not from clamping: a value out of range is an
    error naming ``config <file>: swarm.<key>``.
    """
    unknown_section_keys(table, SWARM_KEYS, "swarm", target)
    section = "swarm"
    return {
        "swarm_max_concurrent": positive_int(
            table, "max_concurrent", DEFAULT_SWARM_MAX_CONCURRENT, section, target
        ),
        "swarm_max_chain_depth": positive_int(
            table, "max_chain_depth", DEFAULT_SWARM_MAX_CHAIN_DEPTH, section, target
        ),
        "swarm_max_chain_cost": positive_float(
            table, "max_chain_cost", DEFAULT_SWARM_MAX_CHAIN_COST, section, target
        ),
        "swarm_runtime_idle_s": non_negative_float(
            table, "runtime_idle_s", DEFAULT_SWARM_RUNTIME_IDLE_S, section, target
        ),
        "swarm_max_request_fanout": positive_int(
            table,
            "max_request_fanout",
            DEFAULT_SWARM_MAX_REQUEST_FANOUT,
            section,
            target,
        ),
        "swarm_max_reply_fanout": positive_int(
            table,
            "max_reply_fanout",
            DEFAULT_SWARM_MAX_REPLY_FANOUT,
            section,
            target,
        ),
        "swarm_max_edge_repeats": positive_int(
            table, "max_edge_repeats", DEFAULT_SWARM_MAX_EDGE_REPEATS, section, target
        ),
        "swarm_max_chain_tasks": positive_int(
            table, "max_chain_tasks", DEFAULT_SWARM_MAX_CHAIN_TASKS, section, target
        ),
        "swarm_storm_window_s": non_negative_float(
            table, "storm_window_s", DEFAULT_SWARM_STORM_WINDOW_S, section, target
        ),
        "swarm_storm_burst": positive_int(
            table, "storm_burst", DEFAULT_SWARM_STORM_BURST, section, target
        ),
    }


@dataclass(frozen=True)
class SwarmLimits:
    """The six message-budget knobs, as the router consumes them.

    Kept here rather than in the gate module so the config layer can build one
    without importing the app layer.
    """

    max_request_fanout: int = DEFAULT_SWARM_MAX_REQUEST_FANOUT
    max_reply_fanout: int = DEFAULT_SWARM_MAX_REPLY_FANOUT
    max_edge_repeats: int = DEFAULT_SWARM_MAX_EDGE_REPEATS
    max_chain_tasks: int = DEFAULT_SWARM_MAX_CHAIN_TASKS
    storm_window_s: float = DEFAULT_SWARM_STORM_WINDOW_S
    storm_burst: int = DEFAULT_SWARM_STORM_BURST

    @classmethod
    def from_policy(cls, policy: SwarmPolicy) -> SwarmLimits:
        """Build the gate's limits from a parsed ``[swarm]`` section.

        ``UserConfig`` inherits :class:`SwarmPolicy`, so a loaded config can be
        passed straight in.
        """
        return cls(
            max_request_fanout=policy.swarm_max_request_fanout,
            max_reply_fanout=policy.swarm_max_reply_fanout,
            max_edge_repeats=policy.swarm_max_edge_repeats,
            max_chain_tasks=policy.swarm_max_chain_tasks,
            storm_window_s=policy.swarm_storm_window_s,
            storm_burst=policy.swarm_storm_burst,
        )
