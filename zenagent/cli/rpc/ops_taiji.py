"""taiji dispatch-board wire handlers (mixin of RpcServer)."""

from __future__ import annotations

import asyncio

from zenagent.app.taiji import (
    board as taiji_board,
)
from zenagent.app.taiji.transcript import peek_tail
from zenagent.cli.rpc.errors import failure_frame


class TaijiOps:
    """Mixin: see server.py for the composed RpcServer."""

    async def _taiji_request(self, request: dict, request_id) -> bool:
        """The dispatch board: one read op plus the four supervisor actions.

        The panel talks to this host's live taiji supervisor directly (D5:
        surfaces consume the taiji layer). A host that never started taiji —
        a worker subprocess, a linked worktree — reads ``active: false``;
        it must never silently construct a bare fleet. Ops are awaited
        inline: the board's answers keep the request's reply order.
        """
        from zenagent.app.taiji.supervisor import existing_taiji

        supervisor = existing_taiji()
        op = request.get("op")

        if op == "board":
            if supervisor is None:
                self._send(
                    {
                        "type": "result",
                        "id": request_id,
                        "ok": True,
                        "board": {
                            "active": False,
                            "router_busy": False,
                            "slots": None,
                            "workers": [],
                        },
                    }
                )
                return True
            chat = getattr(self.runtime, "taiji_chat", None)
            # diffstat shells out to git per acceptance worker; keep the read
            # pump responsive by projecting the board off the event loop.
            board = await asyncio.to_thread(
                taiji_board.project_board, supervisor, bool(getattr(chat, "busy", False))
            )
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "board": board,
                }
            )
            return True

        if op == "message":
            return await self._taiji_action(request, request_id, supervisor)

        if op == "merge":
            if supervisor is None:
                return self._taiji_missing(request_id)
            name = str(request.get("name", ""))
            if await supervisor.merge(name, auto=False):
                self._send({"type": "result", "id": request_id, "ok": True})
            else:
                reason = await supervisor.merge_block_reason(name) or "合并未执行"
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": reason,
                        "code": "taiji.merge_blocked",
                        "params": {"reason": reason},
                    }
                )
            return True

        if op == "kill":
            if supervisor is None:
                return self._taiji_missing(request_id)
            name = str(request.get("name", ""))
            try:
                await supervisor.kill(name)
            except KeyError:
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"worker 不存在: {name}",
                        "code": "taiji.worker_missing",
                        "params": {"name": name},
                    }
                )
                return True
            self._send({"type": "result", "id": request_id, "ok": True})
            return True

        if op == "peek":
            if supervisor is None:
                return self._taiji_missing(request_id)
            name = str(request.get("name", ""))
            try:
                limit = int(request.get("limit", 12))
            except (TypeError, ValueError):
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"peek limit 不是数字: {request.get('limit')}",
                        "code": "taiji.bad_limit",
                        "params": {"value": str(request.get("limit"))},
                    }
                )
                return True
            record = supervisor.find_record(name)
            items = (
                peek_tail(record.session, limit=limit)
                if record and record.session
                else []
            )
            self._send({"type": "result", "id": request_id, "ok": True, "items": items})
            return True

        self._send(
            {
                "type": "error",
                "id": request_id,
                "message": f"unknown taiji op: {op}",
            }
        )
        return True


    def _taiji_missing(self, request_id) -> bool:
        self._send(
            {
                "type": "error",
                "id": request_id,
                "message": "taiji 未在此宿主启用",
                "code": "taiji.inactive",
            }
        )
        return True


    async def _taiji_action(self, request: dict, request_id, supervisor) -> bool:
        if supervisor is None:
            return self._taiji_missing(request_id)
        name = str(request.get("name", ""))
        text = str(request.get("text", ""))
        # Snapshot before the await (no yield between: atomic in the loop) —
        # ``queued`` must mean "the text rides the current turn's tail", not
        # "a fresh turn we just started is running".
        was_running = supervisor.has_running(name)
        try:
            record = await supervisor.message(name, text)
        except KeyError as exc:
            self._send(
                failure_frame(
                    request_id,
                    code="taiji.worker_missing",
                    message="no such worker",
                    exc=exc,
                    op="message",
                    params={"name": name},
                )
            )
            return True
        except RuntimeError as exc:
            # A worker turn error may quote a transcript path; log it, and send
            # the name the board already knows.
            self._send(
                failure_frame(
                    request_id,
                    code="taiji.message_failed",
                    message="message failed",
                    exc=exc,
                    op="message",
                    params={"name": name},
                )
            )
            return True
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "queued": was_running,
                "status": record.status,
            }
        )
        return True
