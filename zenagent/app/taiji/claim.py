"""One project's taiji host claim: session, lock and fleet, in that order.

Claiming is what makes a slot the project's Router host. Releasing is the
slot's teardown path: close this root's fleet, then drop its lock. A slot
that did not own the lock releases nothing, so a live foreign host survives.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zenagent import project
from zenagent.app.taiji.session import (
    ensure_taiji_session,
    own_taiji_lock,
    release_taiji_lock,
    should_init_taiji,
)


@dataclass(frozen=True)
class TaijiClaim:
    """What one slot claimed: the project root, and whether it owns the host."""

    root: Path
    owned: bool


def claim_taiji_host(runtime: Any) -> TaijiClaim:
    """Materialize this project's taiji session, lock and fleet (best effort).

    Only the main checkout hosts taiji. A foreign live host keeps the lock:
    this runtime reports ``owned=False`` and still runs as an ordinary
    project, exactly like the single-project CLI did.
    """
    root = project.find_project_root(runtime.root)
    if not should_init_taiji(root):
        return TaijiClaim(root=root, owned=False)
    ensure_taiji_session(project.project_sessions_dir(root=root), cwd=root)
    if not own_taiji_lock(project.project_dir(root=root)):
        runtime._note("taiji: 另一个宿主已持有本项目的太极锁")
        return TaijiClaim(root=root, owned=False)
    from zenagent.app.taiji.tools import taiji_supervisor_for

    taiji_supervisor_for(runtime)
    return TaijiClaim(root=root, owned=True)


async def release_taiji_host(claim: TaijiClaim) -> None:
    """Close the fleet this claim started and drop its lock."""
    if not claim.owned:
        return
    from zenagent.app.taiji.supervisor import close_taiji

    await close_taiji(claim.root)
    release_taiji_lock(project.project_dir(root=claim.root))
