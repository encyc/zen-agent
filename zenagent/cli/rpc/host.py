"""The RPC host: one stdio loop over a registry of resident slots.

A request may name its project root. Without one, the active slot serves it,
so a v2 client that never learns about slots keeps working exactly as before.
A project may hold several slots while different sessions run in parallel:
the registry is keyed per slot, and each project remembers which of its
slots its requests should land on. The host owns routing, the project ops
and slot lifetime; every other request is forwarded verbatim to the target
slot's server.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.app.bootstrap import prepare
from zenagent.cli.rpc.codec import PROTOCOL_VERSION
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.cli.rpc.ops_swarm import SwarmOps, _is_swarm_request_id
from zenagent.cli.rpc.ops_zen import ZenOps
from zenagent.cli.rpc.slot import RuntimeSlot
from zenagent.cli.rpc.terminal import TerminalError, TerminalSession, clamp_dim
from zenagent.project import find_project_root, project_key

if TYPE_CHECKING:
    from zenagent.app.runtime import RuntimeOptions
    from zenagent.app.swarm.manager import SwarmManager

#: Idle projects beyond this are evicted LRU (audit: cap, never unbounded).
MAX_RESIDENT_SLOTS = 4


class RpcHost(SwarmOps, ZenOps):
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
        #: The slot plain requests land on (the last activated one).
        self._active: RuntimeSlot | None = None
        #: Per project, the slot its own requests land on.
        self._active_by_project: dict[str, RuntimeSlot] = {}
        self._closed = False
        #: One swarm room per project key, created on that project's first
        #: swarm request: a session that never enters swarm mode never pays
        #: for resident role runtimes.
        self._swarm: OrderedDict[str, SwarmManager] = OrderedDict()
        #: Per project key, the session zen took the foreground from.
        self._zen_pre: dict[str, str] = {}
        self._terminal = TerminalSession(self._send)
        if initial is not None:
            self._slots[initial.key] = initial
            self._active = initial
            self._active_by_project[initial.project_key] = initial

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
        if kind == "session" and request.get("op") == "resume":
            await self._session_resume(request, request_id)
            return True
        if kind == "swarm":
            await self._swarm_op(request, request_id)
            return True
        if kind == "zen_fleet":
            await self._zen_op(request, request_id)
            return True
        if kind in ("approval", "ask") and _is_swarm_request_id(
            str(request.get("request_id") or "")
        ):
            self._resolve_swarm_prompt(request, kind)
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
        approval/ask id prefix (which names one slot, not just its project)
        outranks the project field so a pending run in a background slot still
        resolves after the person switched away.
        """
        id_key = self._id_key(request)
        if prefer_id and id_key is not None:
            return self._slots.get(id_key)
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            key = project_key(find_project_root(project))
            slot = self._active_by_project.get(key)
            if slot is not None and slot.key in self._slots:
                self._slots.move_to_end(slot.key)
                return slot
            return None
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

    @staticmethod
    def _session_path(slot: RuntimeSlot) -> str:
        return str(Path(slot.runtime.session.path).expanduser().resolve())

    def _holder(self, root: Path, session: str) -> RuntimeSlot | None:
        """The resident slot already sitting on ``session`` in ``root``."""
        for slot in self._slots.values():
            if Path(slot.runtime.root).resolve() != root:
                continue
            if self._session_path(slot) == session:
                return slot
        return None

    async def _open_project_slot(
        self, root: str, *, resume=None, background: bool = False
    ) -> RuntimeSlot:
        """The slot a project open should land on.

        An exact holder of the requested session is activated untouched (its
        run keeps going), a busy slot on another session gets a sibling slot,
        and an idle slot is reused in place — that last case is what the
        desktop follows up with an explicit ``session resume``.
        """
        target = find_project_root(root)
        base = project_key(target)
        resume_path = (
            str(Path(resume).expanduser().resolve()) if resume is not None else None
        )
        if resume_path is not None:
            holder = self._holder(target, resume_path)
            if holder is not None:
                self._activate(holder, background=background)
                return holder
        current = self._active_by_project.get(base)
        if current is not None and current.key in self._slots:
            same = resume_path is None or resume_path == self._session_path(current)
            if same or not current.busy:
                self._activate(current, background=background)
                return current
        slot = await self._spawn(target, resume=resume_path, base=base)
        self._activate(slot, background=background)
        return slot

    async def _session_resume(self, request: dict, request_id) -> None:
        """Resume without ever aborting a run that belongs to another session.

        A resume into the session a slot already holds is a no-op; one into an
        idle slot loads in place; one into a busy slot opens a sibling slot.
        A zen router slot never loads in place either: its session is the
        fleet's identity, so browsing away from the router opens a sibling.
        Only the in-place case reaches the server, where ``switch_session``
        would abort — and there it has nothing to abort.
        """
        slot = self._slot_for(request)
        target = request.get("path")
        if slot is None:
            self._send(
                error_frame(request_id, "no project: open one first", "no_project")
            )
            return
        if not isinstance(target, str) or not target.strip():
            # Malformed: let the slot answer with its usual refusal.
            await slot.server._handle_line(json.dumps(request))
            return
        root = Path(slot.runtime.root).resolve()
        resume_path = str(Path(target).expanduser().resolve())
        try:
            holder = self._holder(root, resume_path)
            if holder is not None:
                if holder is not slot:
                    self._activate(holder)
                self._send(
                    {
                        "type": "result",
                        "id": request_id,
                        "ok": True,
                        "path": resume_path,
                    }
                )
                return
            if slot.busy or getattr(slot.runtime, "zen_fleet", None) is not None:
                slot = await self._spawn(
                    root, resume=resume_path, base=slot.project_key
                )
                self._activate(slot)
                self._send(
                    {
                        "type": "result",
                        "id": request_id,
                        "ok": True,
                        "path": resume_path,
                    }
                )
                return
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "session_invalid",
                    {"op": "resume"},
                )
            )
            return
        except Exception as exc:  # noqa: BLE001 - a bad session op is a result
            self._send(
                failure_frame(
                    request_id,
                    code="session_failed",
                    message="session failed",
                    exc=exc,
                    op="resume",
                )
            )
            return
        await slot.server._handle_line(json.dumps(request))

    async def _spawn(self, root: Path, *, resume, base: str) -> RuntimeSlot:
        """Prepare and register one runtime slot for ``root``."""
        if len(self._slots) >= self._max_slots:
            await self._make_room()
        key = self._free_key(base)
        options = replace(self._options, root=root, resume=resume)
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
            key=key,
        )
        self._slots[key] = slot
        return slot

    def _free_key(self, base: str) -> str:
        if base not in self._slots:
            return base
        number = 2
        while f"{base}#{number}" in self._slots:
            number += 1
        return f"{base}#{number}"

    def _activate(self, slot: RuntimeSlot, *, background: bool = False) -> None:
        self._slots.move_to_end(slot.key)
        self._active_by_project[slot.project_key] = slot
        if not background:
            self._active = slot

    async def _make_room(self) -> None:
        if len(self._slots) < self._max_slots:
            return
        for key, slot in list(self._slots.items()):
            if slot.pinned:
                continue
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
            self._active = next(reversed(self._slots.values())) if self._slots else None
        if self._active_by_project.get(slot.project_key) is slot:
            sibling = next(
                (
                    other
                    for other in reversed(self._slots.values())
                    if other.project_key == slot.project_key
                ),
                None,
            )
            if sibling is not None:
                self._active_by_project[slot.project_key] = sibling
            else:
                self._active_by_project.pop(slot.project_key, None)
        was_zen = getattr(slot.runtime, "zen_fleet", None) is not None
        await slot.close()
        if was_zen:
            # An evicted router slot takes the project's zen with it; say so,
            # or the GUI would keep showing a fleet that no longer exists.
            self._zen_pre.pop(slot.project_key, None)
            self._send_fleet_roster(Path(slot.runtime.root), None)

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
        slot = await self._open_project_slot(
            root,
            resume=request.get("resume"),
            background=bool(request.get("background")),
        )
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
        targets = [slot for slot in self._slots.values() if slot.project_key == key]
        if any(slot.pinned for slot in targets):
            raise Refusal("project is busy", code="project_busy")
        for slot in targets:
            await self._drop(slot.key)
        # The room is independent of interactive slots, so it must be torn
        # down explicitly: otherwise its role runtimes outlive the project.
        manager = self._swarm.pop(project_key(find_project_root(root)), None)
        if manager is not None:
            await manager.close()
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "closed": bool(targets),
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
        if any(
            slot.project_key == project_key(old_root) for slot in self._slots.values()
        ):
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
        managers = list(self._swarm.values())
        self._swarm.clear()
        for manager in managers:
            await manager.close()
        slots = list(self._slots.values())
        self._slots.clear()
        self._active = None
        self._active_by_project.clear()
        for slot in slots:
            await slot.close()

    def _send(self, payload: dict) -> None:
        if self._write_line is not None:
            self._write_line(json.dumps(payload, ensure_ascii=False, default=str))
