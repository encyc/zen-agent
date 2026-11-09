"""fs_* / memory wire handlers (mixin of RpcServer)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path

from zenagent.agent.memory_store import MemoryStore
from zenagent.app.workspace import (
    list_directory,
    read_file,
    workspace_files,
)
from zenagent.project import (
    find_project_root,
)

#: Server-side diagnostics; raw OSError detail never crosses the wire.
logger = logging.getLogger(__name__)


def _basename(value: object) -> str:
    """The last path component of a wire/log value ('' when unavailable)."""
    if not isinstance(value, str) or not value:
        return ""
    return Path(value).name


def _fs_error(request_id, op: str, exc: BaseException, path: str) -> dict:
    """One sanitized fs error frame (I3).

    ``message`` is a short, path-free fallback; the client localizes by
    ``code``. ``params`` carries only the op, the errno and the file's
    basename — an absolute path must never cross the wire. The raw exception
    goes to the server log, where a developer can still see what happened.
    """
    logger.warning("fs %s failed for %r: %s", op, path, exc)
    params: dict[str, str] = {"op": op}
    errno = getattr(exc, "errno", None)
    if isinstance(errno, int):
        params["errno"] = str(errno)
    name = _basename(getattr(exc, "filename", None)) or _basename(path)
    if name:
        params["file"] = name
    return {
        "type": "error",
        "id": request_id,
        "message": f"{op} failed",
        "code": "fs_error",
        "params": params,
    }


class FilesOps:
    """Mixin: see server.py for the composed RpcServer."""

    def _fs_request(self, request: dict, request_id, op: str) -> bool:
        path = request.get("path", "")
        if not isinstance(path, str):
            # Same refusal shape as an OS error, so the client can localize
            # it (`fs_error`) instead of printing a one-off server string.
            self._send(
                _fs_error(request_id, op, ValueError("path must be a string"), "")
            )
            return True
        try:
            if op == "list":
                payload = list_directory(Path.cwd(), path)
            else:
                payload = {"file": read_file(Path.cwd(), path)}
        except (OSError, ValueError) as exc:
            self._send(_fs_error(request_id, op, exc, path))
            return True
        self._send({"type": "result", "id": request_id, "ok": True, **payload})
        return True


    async def _fs_index(self, request_id) -> None:
        try:
            payload = await asyncio.to_thread(workspace_files, Path.cwd())
        except (OSError, ValueError) as exc:
            self._send(_fs_error(request_id, "index", exc, ""))
            return
        self._send({"type": "result", "id": request_id, "ok": True, **payload})


    def _memory_request(self, request: dict, request_id) -> bool:
        """Project memory CRUD for the desktop panel, journaled like the TUI."""
        store = (
            self.runtime.memory_store
            if self.runtime is not None
            else MemoryStore(find_project_root(Path.cwd()))
        )
        op = request.get("op")
        if op == "list":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "entries": [asdict(entry) for entry in store.list_entries()],
                }
            )
            return True
        if op == "create":
            title = request.get("title")
            body = request.get("body")
            if not isinstance(title, str) or not isinstance(body, str):
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": "memory create needs title and body",
                    }
                )
                return True
            entry = store.write_entry(
                title=title, body=body, source="ui", reason="ui add"
            )
            self._send(
                {"type": "result", "id": request_id, "ok": True, "entry": asdict(entry)}
            )
            return True
        if op == "update":
            entry_id = request.get("entry_id")
            title = request.get("title")
            body = request.get("body")
            if (
                not isinstance(entry_id, str)
                or not isinstance(title, str)
                or not isinstance(body, str)
            ):
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": "memory update needs entry_id, title and body",
                    }
                )
                return True
            entry = store.update_entry(
                entry_id, title=title, body=body, reason="ui edit"
            )
            if entry is None:
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"memory entry not found: {entry_id}",
                    }
                )
                return True
            self._send(
                {"type": "result", "id": request_id, "ok": True, "entry": asdict(entry)}
            )
            return True
        if op == "delete":
            entry_id = request.get("entry_id")
            if not isinstance(entry_id, str):
                self._send(
                    {"type": "error", "id": request_id, "message": "memory delete needs entry_id"}
                )
                return True
            deleted = store.delete_entry(entry_id, reason="ui delete")
            self._send(
                {"type": "result", "id": request_id, "ok": True, "deleted": deleted}
            )
            return True
        self._send(
            {
                "type": "error",
                "id": request_id,
                "message": f"unknown memory op: {op}",
            }
        )
        return True
