"""Which session a fleet's router surface belongs to.

The fleet itself is slot-scoped; tools, the router prompt and worker reports
are session-scoped. This tiny predicate is shared by the runtime (tool and
prompt assembly) and kept out of ``runtime.py`` so that file's line budget does
not grow with zen's identity rule.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["router_fleet"]


def router_fleet(fleet, session_path) -> object | None:
    """The fleet only while ``session_path`` is its router.

    Fleet tools, the router prompt note and worker reports all belong to the
    project's one Router session. The fleet is slot-scoped, so without this
    check a session switch (or a new session) would inherit the router surface
    and turn an ordinary coding chat into a router.
    """
    if fleet is None:
        return None
    try:
        current = Path(session_path).resolve()
        router = Path(fleet.router_path).resolve()
    except OSError:
        return None
    return fleet if current == router else None
