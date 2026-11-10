"""One resident project: its runtime, RPC server and taiji claim.

A slot is the unit the host opens and evicts. Everything that used to be
process-scoped in ``zen rpc`` — the in-flight run, text buffer, approval and
ask futures, taiji host lock — belongs to a slot now.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.app.taiji import (
    TaijiClaim,
    claim_taiji_host,
    release_taiji_host,
)
from zenagent.cli.rpc.server import RpcServer
from zenagent.project import project_key

if TYPE_CHECKING:
    from zenagent.app.bootstrap import PreparedFrontend
    from zenagent.app.runtime import Runtime

#: Matches ``RpcServer``'s default approval timeout.
DEFAULT_APPROVAL_TIMEOUT_S = 120.0


class RuntimeSlot:
    """A resident project and everything its server owns."""

    def __init__(
        self,
        *,
        root: Path,
        server: RpcServer,
        claim: TaijiClaim,
        opened_at: float | None = None,
    ) -> None:
        self.root = Path(root)
        self.key = project_key(self.root)
        self.server = server
        self.claim = claim
        self.opened_at = time.monotonic() if opened_at is None else opened_at

    @classmethod
    def adopt(
        cls,
        prepared: PreparedFrontend,
        *,
        write_line,
        approval_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
    ) -> RuntimeSlot:
        """Wire one prepared frontend into a slot and claim its taiji host."""
        runtime = prepared.runtime
        chat = prepared.chat
        services = prepared.services
        server = RpcServer(
            runtime=runtime,
            read_line=None,
            write_line=write_line,
            chat=chat,
            models=services.models if services is not None else None,
            sessions=services.sessions if services is not None else None,
            commands=prepared.commands,
            approval_timeout_s=approval_timeout_s,
            project=str(runtime.root),
        )
        prepared.broker.set_confirm(server.confirm)
        runtime.ask_handler = server.ask
        runtime.taiji_chat = chat
        claim = claim_taiji_host(runtime)
        return cls(root=Path(runtime.root).resolve(), server=server, claim=claim)

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime

    @property
    def chat(self):
        return self.server.chat

    @property
    def busy(self) -> bool:
        """A run, a queued follow-up or a compaction owns this slot."""
        return self.server.busy or bool(self.chat is not None and self.chat.busy)

    @property
    def pinned(self) -> bool:
        """True while the slot must not be evicted."""
        if self.busy:
            return True
        from zenagent.app.taiji.supervisor import existing_taiji

        supervisor = existing_taiji(self.root)
        if supervisor is None:
            return False
        return any(
            record.status in ("running", "queued")
            for record in supervisor.list_workers()
        )

    async def close(self) -> None:
        """Deny pending prompts, release the taiji claim and close the runtime."""
        await self.server._shutdown()
        await release_taiji_host(self.claim)
        self.runtime.ask_handler = None
        await self.runtime.close()