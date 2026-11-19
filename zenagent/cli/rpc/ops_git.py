"""git_* wire handlers (mixin of RpcServer): worktree overview, changes,
log, show and per-file diff — the review rail's backend.

Pure relocation from server.py's dispatch branches (which keep thin calls):
zero behavior change. Request shapes come from the wire registry
(``zenagent.cli.rpc.protocol``); the handlers keep the semantics — the
worktree-scope check, the commit regex, the exact refusal strings.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.app import git_review
from zenagent.app.workspace import worktree_overview, worktree_paths
from zenagent.cli.rpc import protocol
from zenagent.cli.rpc.errors import failure_frame

if TYPE_CHECKING:
    from typing import Protocol


    class _GitHost(Protocol):
        """The slice of RpcServer the git handlers use; the real class is
        composed at import time, so a structural alias keeps the mixin
        decoupled (and mypy-clean) without a runtime import cycle."""

        cwd: Path

        def _send(self, frame: dict) -> None: ...

        def _needs_worktree(self, request_id: object, kind: str) -> bool: ...

#: What `git_show` accepts: a hex object name or a symbolic ref the
#: graph can produce (`HEAD`). Anything else is the caller's mistake.
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{4,40}|HEAD)$")


def _git_scope(cwd: Path, requested: object) -> Path | None:
    """Resolve the review scope: the project root by default, or one of its
    known worktrees for a read-only preview. A forged/unknown path is refused
    — the panel may only read a worktree git itself listed, never an arbitrary
    directory the caller names."""
    if requested is None:
        return cwd
    if not isinstance(requested, str) or requested == "":
        return None
    try:
        root = Path(requested).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return root if root in worktree_paths(cwd) else None


def _git_failed(request_id, op: str, exc: BaseException) -> dict:
    return failure_frame(
        request_id,
        code="git_failed",
        message="git op failed",
        exc=exc,
        op=op,
    )


class GitOps:
    """Mixin: see server.py for the composed RpcServer."""

    def _needs_worktree(self: _GitHost, request_id, kind: str) -> bool:
        self._send(
            {
                "type": "error",
                "id": request_id,
                "message": f"{kind} needs a known worktree",
            }
        )
        return True

    def _git_worktrees(self: _GitHost, request_id) -> bool:
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "worktree": worktree_overview(self.cwd),
            }
        )
        return True

    async def _git_changes(self: _GitHost, request: dict, request_id, kind: str) -> bool:
        # The panel polls this while a run streams; the shell-out must
        # leave the event loop free (see test_git_ops_run_off_loop). The
        # optional worktree is a read-only preview of a linked checkout.
        try:
            req = protocol.GitChangesRequest.from_payload(request)
        except protocol.WireError:
            scope = None
        else:
            scope = _git_scope(self.cwd, req.worktree)
        if scope is None:
            return self._needs_worktree(request_id, kind)
        try:
            result = await asyncio.to_thread(git_review.changes, scope)
        except Exception as exc:  # noqa: BLE001 - the frame is the failure surface
            self._send(_git_failed(request_id, kind, exc))
        else:
            self._send(
                {"type": "result", "id": request_id, "ok": True, "changes": result}
            )
        return True

    async def _git_log(self: _GitHost, request: dict, request_id, kind: str) -> bool:
        try:
            req = protocol.GitLogRequest.from_payload(request)
            limit = int(req.limit or 100)
            skip = int(req.skip or 0)
        except (protocol.WireError, TypeError, ValueError):
            self._send(
                {"type": "error", "id": request_id, "message": "git_log needs numbers"}
            )
            return True
        try:
            result = await asyncio.to_thread(
                git_review.log, self.cwd, limit=limit, skip=skip
            )
        except Exception as exc:  # noqa: BLE001 - the frame is the failure surface
            self._send(_git_failed(request_id, kind, exc))
        else:
            self._send(
                {"type": "result", "id": request_id, "ok": True, "log": result}
            )
        return True

    async def _git_show(self: _GitHost, request: dict, request_id, kind: str) -> bool:
        try:
            req = protocol.GitShowRequest.from_payload(request)
        except protocol.WireError:
            commit = None
        else:
            commit = req.commit
        if not isinstance(commit, str) or not _GIT_COMMIT_RE.match(commit):
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": "git_show needs a commit hash",
                }
            )
            return True
        try:
            result = await asyncio.to_thread(git_review.show, self.cwd, commit)
        except Exception as exc:  # noqa: BLE001 - the frame is the failure surface
            self._send(_git_failed(request_id, kind, exc))
        else:
            self._send(
                {"type": "result", "id": request_id, "ok": True, "show": result}
            )
        return True

    async def _git_diff(self: _GitHost, request: dict, request_id, kind: str) -> bool:
        try:
            req = protocol.GitDiffRequest.from_payload(request)
        except protocol.WireError:
            path, staged, scope = "", False, None
        else:
            path = req.path
            staged = req.staged is True
            scope = _git_scope(self.cwd, req.worktree)
        if not isinstance(path, str) or not path:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": "git_diff needs a path",
                }
            )
            return True
        if scope is None:
            return self._needs_worktree(request_id, kind)
        try:
            result = await asyncio.to_thread(
                git_review.diff, scope, path, staged=staged
            )
        except Exception as exc:  # noqa: BLE001 - the frame is the failure surface
            self._send(_git_failed(request_id, kind, exc))
        else:
            self._send(
                {"type": "result", "id": request_id, "ok": True, "diff": result}
            )
        return True
