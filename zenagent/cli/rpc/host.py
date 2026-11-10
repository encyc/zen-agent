"""The RPC host: one stdio loop over a registry of resident project slots.

A request may name its project root. Without one, the active slot serves it,
so a v2 client that never learns about slots keeps working exactly as before.
The host owns only routing, the project ops and slot lifetime; every other
request is forwarded verbatim to the target slot's server.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from zenagent.app.bootstrap import prepare
from zenagent.cli.rpc.codec import PROTOCOL_VERSION
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.cli.rpc.terminal import TerminalError, TerminalSession, clamp_dim
from zenagent.project import find_project_root, project_key

if TYPE_CHECKING:
    from zenagent.app.runtime import RuntimeOptions

#: Idle projects beyond this are evicted LRU (audit: cap, never unbounded).
MAX_RESIDENT_SLOTS = 4


class RpcHost:
    """Reads requests, routes them to a project slot, and owns slot lifetime."""

    def __init__(
        self,
        *,
        initial: RuntimeSlot | None,
        options: RuntimeOptions,
        read_line,
        write_line,
        assume_yes: bool = False,
        stream_fn=None,
        max_slots: int = MAX_RESIDENT_SLOTS,
        approval_timeout_s: float = 120.0,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._options = options
        self._read_line = read_line
        self._write_line = write_line
        self._assume_yes = assume_yes
        self._stream_fn = stream_fn
        self._max_slots = max(1, int(max_slots))
        self._approval_timeout_s = approval_timeout_s
        self._warn = warn
        self._slots: OrderedDict[str, RuntimeSlot] = OrderedDict()
        self._active: RuntimeSlot | None = None
        self._closed = False
        self._terminal = TerminalSession(self._send)
        if initial is not None:
            self._slots[initial.key] = initial
            self._active = initial

    # -- introspection ------------------------------------------------------

    @property
    def active(self) -> RuntimeSlot | None:
        return self._active

    @property
    def resident(self) -> list[dict]:
        return [
            {
                "key": key,
                "root": slot.server.project,
                "session": str(slot.runtime.session.path),
            }
            for key, slot in self._slots.items()
        ]

    # -- loop ---------------------------------------------------------------

    async def run(self) -> int:
        while True:
            line = await self._read_line()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if not await self._handle_line(line):
                break
        await self.close()
        return 0

    async def _handle_line(self, line: str) -> bool:
        """Handle one request line; False means shutdown."""
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError(  # noqa: TRY004 - bad wire data is an error
                    "request must be a JSON object"
                )
        except ValueError as exc:
            self._send({"type": "error", "id": None, "message": f"bad request: {exc}"})
            return True

        kind = request.get("type")
        request_id = request.get("id")
        if kind == "hello":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "protocol_version": PROTOCOL_VERSION,
                    "active": self._active.server.project if self._active else "",
                    "projects": self.resident,
                }
            )
            return True
        if kind == "shutdown":
            self._send({"type": "result", "id": request_id, "ok": True})
            return False
        if kind == "project":
            await self._project(request, request_id)
            return True
        if kind == "terminal":
            self._terminal_op(request, request_id)
            return True

        slot = self._slot_for(request, prefer_id=kind in ("approval", "ask"))
        if slot is None:
            self._send(
                error_frame(
                    request_id,
                    "no project: open one first",
                    "no_project",
                )
            )
            return True
        if kind == "approval":
            return slot.server._resolve_approval(request)
        if kind == "ask":
            return slot.server._resolve_ask(request)
        return await slot.server._handle_line(line)

    # -- terminal -----------------------------------------------------------

    def _terminal_op(self, request: dict, request_id) -> None:
        """Serve one terminal op. The shell lives at host level: opening a
        project neither creates nor stops it, and the client token keeps a
        replaced shell's late frames out of its successor's pane."""
        op = request.get("op")
        label = str(op or "")
        token = request.get("session")
        try:
            if not isinstance(token, str) or token == "":
                raise Refusal("terminal needs a session token")
            if op == "start":
                cwd = request.get("cwd")
                if cwd is not None and not isinstance(cwd, str):
                    raise Refusal("terminal cwd must be a string")
                info = self._terminal.start(
                    token,
                    cwd or None,
                    clamp_dim(request.get("cols"), 80),
                    clamp_dim(request.get("rows"), 24),
                )
                self._send({"type": "result", "id": request_id, "ok": True, **info})
                return
            if op == "write":
                data = request.get("data")
                if not isinstance(data, str):
                    raise Refusal("terminal write needs data")
                self._terminal.write(token, data)
            elif op == "resize":
                self._terminal.resize(
                    token,
                    clamp_dim(request.get("cols"), 80),
                    clamp_dim(request.get("rows"), 24),
                )
            elif op == "close":
                self._terminal.close(token)
            else:
                raise Refusal(f"unknown terminal op: {op}")
            self._send({"type": "result", "id": request_id, "ok": True})
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "terminal_invalid",
                    {"op": label},
                )
            )
        except TerminalError as exc:
            self._send(
                error_frame(request_id, str(exc), "terminal_failed", {"op": label})
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="terminal_failed",
                    message="terminal failed",
                    exc=exc,
                    op=label,
                )
            )

    # -- slot registry ------------------------------------------------------

    def _slot_for(
        self, request: dict, *, prefer_id: bool = False
    ) -> RuntimeSlot | None:
        """The slot a request targets.

        A named project never falls back to the active slot: running a prompt
        in the wrong project is worse than a ``no_project`` refusal. An
        approval/ask id prefix outranks the project field so a pending run in
        a background project still resolves after the person switched away.
        """
        id_key = self._id_key(request)
        if prefer_id and id_key is not None:
            return self._slots.get(id_key)
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            key = project_key(find_project_root(project))
            slot = self._slots.get(key)
            if slot is not None:
                self._slots.move_to_end(key)
            return slot
        if id_key is not None:
            slot = self._slots.get(id_key)
            if slot is not None:
                return slot
        return self._active

    @staticmethod
    def _id_key(request: dict) -> str | None:
        for field in ("request_id", "id"):
            value = request.get(field)
            if isinstance(value, str) and ":" in value:
                return value.split(":", 1)[0]
        return None

    async def _ensure(self, root: str, *, resume=None) -> RuntimeSlot:
        target = find_project_root(root)
        key = project_key(target)
        slot = self._slots.get(key)
        if slot is not None:
            self._slots.move_to_end(key)
            return slot
        if len(self._slots) >= self._max_slots:
            await self._make_room()
        options = replace(self._options, root=target, resume=resume)
        prepared = await prepare(
            options,
            stream_fn=self._stream_fn,
            assume_yes=self._assume_yes,
        )
        if self._warn is not None:
            for warning in prepared.runtime.warnings:
                self._warn(warning)
        slot = RuntimeSlot.adopt(
            prepared,
            write_line=self._write_line,
            approval_timeout_s=self._approval_timeout_s,
        )
        self._slots[slot.key] = slot
        return slot

    async def _make_room(self) -> None:
        if len(self._slots) < self._max_slots:
            return
        for key, slot in list(self._slots.items()):
            if not slot.pinned:
                await self._drop(key)
                return
        raise Refusal(
            f"all {self._max_slots} project slots are busy", code="project_busy"
        )

    async def _drop(self, key: str) -> None:
        slot = self._slots.pop(key, None)
        if slot is None:
            return
        if self._active is slot:
            self._active = (
                next(reversed(self._slots.values())) if self._slots else None
            )
        await slot.close()

    # -- project ops --------------------------------------------------------

    async def _project(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            if op in ("open", "switch"):
                await self._project_open(request, request_id)
                return
            if op == "close":
                await self._project_close(request, request_id)
                return
            if op == "relocate":
                await self._project_relocate(request, request_id)
                return
            raise Refusal(f"unknown project op: {op}")
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "project_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="project_failed",
                    message="project failed",
                    exc=exc,
                    op=label,
                )
            )

    async def _project_open(self, request: dict, request_id) -> None:
        root = request.get("root")
        if not isinstance(root, str) or not root.strip():
            raise Refusal("open needs root")
        slot = await self._ensure(root, resume=request.get("resume"))
        if not request.get("background"):
            self._active = slot
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "root": slot.server.project,
                "key": slot.key,
                "session": str(slot.runtime.session.path),
                "active": self._active.server.project if self._active else "",
                "resident": self.resident,
            }
        )

    async def _project_close(self, request: dict, request_id) -> None:
        root = request.get("root")
        if not isinstance(root, str) or not root.strip():
            raise Refusal("close needs root")
        key = project_key(root)
        slot = self._slots.get(key)
        closed = slot is not None
        if slot is not None:
            if slot.pinned:
                raise Refusal("project is busy", code="project_busy")
            await self._drop(key)
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "closed": closed,
                "active": self._active.server.project if self._active else "",
                "resident": self.resident,
            }
        )

    async def _project_relocate(self, request: dict, request_id) -> None:
        old_root = request.get("from")
        new_root = request.get("to")
        if not isinstance(old_root, str) or not old_root.strip():
            raise Refusal("relocate needs from")
        if not isinstance(new_root, str) or not new_root.strip():
            raise Refusal("relocate needs to")
        if project_key(old_root) in self._slots:
            raise Refusal(
                "cannot relocate an open project; close it first",
                code="project_busy",
            )
        slot = self._active
        if slot is None:
            raise Refusal("no project to serve the relocate", code="no_project")
        await slot.server._project(request, request_id)

    # -- teardown -----------------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._terminal.close()
        slots = list(self._slots.values())
        self._slots.clear()
        self._active = None
        for slot in slots:
            await slot.close()

    def _send(self, payload: dict) -> None:
        if self._write_line is not None:
            self._write_line(json.dumps(payload, ensure_ascii=False, default=str))