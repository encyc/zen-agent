"""session / transcript wire handlers (mixin of RpcServer)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path


class SessionOps:
    """Mixin: see server.py for the composed RpcServer."""

    def _transcript_result(self, request_id) -> dict:
        """The current transcript as view models (the TUI reducer's output).

        A GUI that resumes or switches sessions has no event journal to replay:
        this snapshot is its baseline, and live events then apply on top of it.
        """

        from zenagent.app import records

        views: list = []
        notices: list = []
        compactions: list[dict] = []
        if self.chat is not None:
            session = getattr(self.runtime, "session", None)
            if session is not None:
                session.ensure_loaded()
                compactions = [
                    record
                    for record in session.records
                    if records.is_compaction(record)
                ]
            views = [asdict(view) for view in self.chat.transcript]
            notices = [
                asdict(notice)
                for notice in self.chat.notices_snapshot
                if notice.source != "compaction"
            ]
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "transcript": views,
            "notices": notices,
            "compactions": compactions,
        }


    def _subagent_transcript_result(self, request_id, call_id: str) -> dict:
        """One ``task`` child's conversation, live buffer or persisted sidecar.

        ``available`` is false when neither exists (an old session whose
        sidecar is gone), so the client can say so instead of showing an
        empty conversation.
        """

        transcript = (
            self.chat.subagent_transcript(call_id) if self.chat is not None else None
        )
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "available": transcript is not None,
            "transcript": [asdict(view) for view in transcript or []],
        }


    async def _session(self, request: dict, request_id) -> None:
        op = request.get("op")
        try:
            result = await self._session_op(op, request)
        except Exception as exc:  # noqa: BLE001 - a bad session op is a result, not a crash
            self._send({"type": "error", "id": request_id, "message": str(exc)})
            return
        self._send({"type": "result", "id": request_id, "ok": True, **(result or {})})


    async def _session_op(self, op: str, request: dict) -> dict:
        if self.runtime is None or self.sessions is None:
            raise RuntimeError("session requests need a runtime")
        if op == "current":
            path = self.runtime.session.path
            return {"path": str(path), "name": path.stem}
        if op == "list":
            scope = "all" if request.get("scope") == "all" else "project"
            return {
                "scope": scope,
                "sessions": [
                    {
                        "path": str(entry.path),
                        "name": entry.path.stem,
                        "title": entry.title,
                        "project": entry.project,
                        "project_path": entry.project_path,
                        "messages": entry.messages,
                        "current": entry.current,
                        "pinned": entry.pinned,
                        "created_at": entry.created_at,
                        "updated_at": entry.updated_at,
                        "model": entry.model,
                        "derived_from": entry.derived_from,
                    }
                    for entry in self.sessions.entries(scope=scope)
                ],
            }
        if op == "new":
            path = await self.sessions.new()
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "fork":
            path = await self.sessions.fork()
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "resume":
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("resume needs path")
            await self.sessions.resume(Path(target))
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(self.runtime.session.path)}
        if op == "import":
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("import needs path")
            path = Path(target).expanduser()
            if not path.is_file():
                raise ValueError(f"no such session: {path}")
            await self.runtime.switch_session(path)
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "undo":
            if self.busy:
                raise RuntimeError("busy: cancel the run before undoing")
            result = self.sessions.undo()
            if result is None:
                return {"dropped": 0, "path": None}
            dropped, path = result
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"dropped": dropped, "path": str(path)}
        if op == "delete":
            if self.busy:
                raise RuntimeError("busy: cancel the run before deleting a session")
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("delete needs path")
            trashed = self.sessions.delete(Path(target))
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(trashed)}
        if op == "compact":
            if self.chat is None:
                raise RuntimeError("compaction needs a runtime")
            notice = await self.chat.compact_now()
            if notice is None:
                return {"compacted": False}
            return {"compacted": True, "path": str(self.runtime.session.path)}
        if op == "retry":
            if self.chat is None:
                raise RuntimeError("retry needs a runtime")
            text = self.chat.retry()
            if text is None:
                return {"retried": False}
            return {"retried": True, "text": text}
        if op == "export":
            fmt = request.get("format") or "md"
            target = request.get("path") or self.runtime.session.path
            exported = self.sessions.export(Path(target), fmt)
            return {"path": str(exported)}
        if op == "promote":
            if self.chat is None:
                raise RuntimeError("promote needs a runtime")
            pending_id = request.get("pending_id")
            if not isinstance(pending_id, int):
                raise ValueError("promote needs pending_id")
            return {"outcome": self.chat.promote(pending_id)}
        if op == "dequeue":
            if self.chat is None:
                raise RuntimeError("dequeue needs a runtime")
            text = self.chat.dequeue_follow_up()
            return {"dequeued": text is not None, "text": text}
        if op == "clear_pending":
            if self.chat is None:
                raise RuntimeError("clear_pending needs a runtime")
            return {"cleared": self.chat.clear_pending()}
        raise ValueError(f"unknown session op: {op}")
