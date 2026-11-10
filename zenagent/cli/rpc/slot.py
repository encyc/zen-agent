"""One resident runtime and its RPC server.

A slot is the unit the host opens and evicts. Everything that used to be
process-scoped in ``zen rpc`` — the in-flight run, text buffer, approval and
ask futures — belongs to a slot now. A project may own several slots, one per
live session.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.cli.rpc.server import RpcServer
from zenagent.project import project_key

if TYPE_CHECKING:
    from zenagent.app.bootstrap import PreparedFrontend
    from zenagent.app.runtime import Runtime

#: Matches ``RpcServer``'s default approval timeout.
DEFAULT_APPROVAL_TIMEOUT_S = 120.0


class RuntimeSlot:
    """A resident runtime and everything its server owns."""

    def __init__(
        self,
        *,
        root: Path,
        server: RpcServer,
        key: str | None = None,
        opened_at: float | None = None,
    ) -> None:
        self.root = Path(root)
        #: Registry key: the project key for a project's first slot, then
        #: ``<project key>#2``, ``#3``… for extra sessions of the same project.
        self.key = key or project_key(self.root)
        self.server = server
        self.opened_at = time.monotonic() if opened_at is None else opened_at

    @property
    def project_key(self) -> str:
        return project_key(self.root)

    @classmethod
    def adopt(
        cls,
        prepared: PreparedFrontend,
        *,
        write_line,
        approval_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
        key: str | None = None,
    ) -> RuntimeSlot:
        """Wire one prepared frontend into a slot."""
        runtime = prepared.runtime
        root = Path(runtime.root).resolve()
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
            identity=key,
        )
        prepared.broker.set_confirm(server.confirm)
        runtime.ask_handler = server.ask
        return cls(root=root, server=server, key=key)

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
        return self.busy

    async def close(self) -> None:
        """Deny pending prompts and close the runtime."""
        await self.server._shutdown()
        self.runtime.ask_handler = None
        await self.runtime.close()
