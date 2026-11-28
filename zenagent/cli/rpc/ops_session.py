"""session / transcript wire handlers (mixin of RpcServer)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from zenagent.app.session_lease import SessionLeaseBusy
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame


async def session_list_frame(
    sessions, *, scope: str, live: Iterable[Path] = (), archived: str = "exclude"
) -> dict:
    """The ``session list`` answer, shared by a slot's server and the host.

    ``live`` names sessions other runtimes hold right now: a run streaming in
    a sibling slot has no transcript yet, so a listing that reads only files
    would drop it the moment the person switches away. The host knows its
    slots; a lone runtime passes nothing. ``archived`` filters by the archive
    sidecar — ``"exclude"`` is the sidebar, ``"only"`` the settings view.
    """
    # Listing parses every transcript in every project; off the loop it
    # cannot stall streaming events or the next request behind it.
    entries = await asyncio.to_thread(
        sessions.entries, scope=scope, live=live, archived=archived
    )
    return {"scope": scope, "sessions": [entry.as_wire() for entry in entries]}


class MissingSessionFile(Refusal):
    """``session op=import`` named a file that is not there (path kept here so
    only its basename crosses the wire)."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"no such session: {path}")
        self.path = path


_ARCHIVE_MODES = ("exclude", "include", "only")


def _archived_mode(request: dict) -> str:
    """The listing's archive filter; junk keeps the sidebar default."""
    mode = request.get("archived")
    return mode if mode in _ARCHIVE_MODES else "exclude"


def _optional_count(value: object, *, minimum: int) -> int | None:
    """One optional wire count; a malformed value is ignored, not an error.

    Paging is an optimization: a client that sends a bad cursor still gets
    the full snapshot instead of a broken page. Bools are rejected on
    purpose — ``True`` is an ``int`` in Python but never a cursor.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= minimum else None


def _page_views(
    views: list, *, limit: int | None, before: int | None
) -> tuple[list, bool]:
    """Slice one page of views; ``(page, has_more_before)``.

    ``before`` is the exclusive end cursor, so a client that already holds
    the tail asks for ``before=<first loaded index>``. Appends at the tail
    (the live turn) never shift an older page; a cursor past the end clamps
    to the snapshot.
    """
    end = len(views) if before is None else min(before, len(views))
    start = 0 if limit is None else max(0, end - limit)
    return views[start:end], start > 0


def disk_transcript(
    path: Path, *, limit: int | None, before: int | None
) -> dict:
    """One session file's transcript, read straight from disk.

    A session nobody holds a slot for still has a file, and a GUI showing it in
    a side pane must not switch the foreground conversation just to read it.
    Same view models and paging as the resident snapshot; ``resident`` is false
    so the client knows frames will not stream and only a resume makes it live.
    """

    from zenagent.agent.session import Session
    from zenagent.app.views import TranscriptBuilder

    session = Session(path)
    messages = session.load()
    builder = TranscriptBuilder()
    builder.load(messages, list(session.records))
    views = [asdict(view) for view in builder.views]
    page, has_more_before = _page_views(views, limit=limit, before=before)
    return {
        "transcript": page,
        "notices": [],
        "compactions": [],
        "hasMoreBefore": has_more_before,
        "total": len(views),
        "resident": False,
    }


class HostSessionOps:
    """Host-side session ops: the ones that need no resident runtime.

    ``list`` answers with every slot's live session merged in; ``archive``
    writes the archive sidecar straight to disk, so the settings page can
    put away (and restore) a project's history without opening it.
    """

    async def _session_list(self, slot, request: dict, request_id) -> None:
        """Answer the list with every resident slot's live session.

        A transcript lands only when the turn ends, so a run streaming in a
        sibling slot is in no file the slot's own listing reads: switch away
        and the sidebar loses it. The host owns the slots; merge them here.
        """
        scope = "all" if request.get("scope") == "all" else "project"
        archived = (
            request.get("archived")
            if request.get("archived") in ("exclude", "include", "only")
            else "exclude"
        )
        live = [
            other.runtime.session.path
            for other in self._slots.values()
            if other is not slot and (scope == "all" or other.root == slot.root)
        ]
        try:
            frame = await session_list_frame(
                slot.server.sessions, scope=scope, live=live, archived=archived
            )
        except Exception as exc:  # noqa: BLE001 - a bad listing is a result
            self._send(
                failure_frame(
                    request_id,
                    code="session_failed",
                    message="session failed",
                    exc=exc,
                    op="list",
                )
            )
            return
        self._send({"type": "result", "id": request_id, "ok": True, **frame})


    async def _session_archive(self, request: dict, request_id) -> None:
        """Archive/restore a session straight on the sidecar — no slot needed.

        A session nobody holds is exactly the settings-page case (its project
        may not even be open). Only *archiving* a session a busy slot holds as
        current is refused: the run owns that conversation. Restoring touches
        nothing a run could own, so it is never refused.
        """
        try:
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise Refusal("archive needs path")
            archived = request.get("archived")
            if not isinstance(archived, bool):
                raise Refusal("archive needs archived: true or false")
            path = Path(target).expanduser()
            holder = self._session_holder(str(path), self._project_tag(request))
            if archived and holder is not None and holder.busy:
                raise Refusal(
                    "busy: cancel the run before archiving a session",
                    code="session_busy",
                )
            from zenagent.agent.session import set_session_archived

            stamp = await asyncio.to_thread(set_session_archived, path, archived)
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "session_invalid",
                    {"op": "archive"},
                )
            )
            return
        except Exception as exc:  # noqa: BLE001 - a bad session op is a result
            self._send(
                failure_frame(
                    request_id,
                    code="session_invalid",
                    message="session archive failed",
                    exc=exc,
                    op="archive",
                )
            )
            return
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "path": str(path),
                "archived": archived,
                "archived_at": stamp,
            }
        )


class SessionOps:
    """Mixin: see server.py for the composed RpcServer."""

    def _transcript_result(
        self, request_id, request: dict | None = None
    ) -> dict:
        """The current transcript as view models (the TUI reducer's output).

        A GUI that resumes or switches sessions has no event journal to replay:
        this snapshot is its baseline, and live events then apply on top of it.
        Optional ``limit``/``before`` page it for long sessions.
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
        payload = request or {}
        total = len(views)
        views, has_more_before = _page_views(
            views,
            limit=_optional_count(payload.get("limit"), minimum=1),
            before=_optional_count(payload.get("before"), minimum=0),
        )
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "transcript": views,
            "notices": notices,
            "compactions": compactions,
            "hasMoreBefore": has_more_before,
            # The absolute row count: a tail page starts at ``total - len(page)``
            # and that index — not the page length — is the next ``before``.
            "total": total,
            # This slot holds the session, so frames stream and submits go
            # direct; the disk snapshot in ``disk_transcript`` says false.
            "resident": True,
        }


    def _subagent_transcript_result(
        self, request_id, call_id: str, request: dict | None = None
    ) -> dict:
        """One ``task`` child's conversation, live buffer or persisted sidecar.

        ``available`` is false when neither exists (an old session whose
        sidecar is gone), so the client can say so instead of showing an
        empty conversation. Optional ``after`` returns only the views the
        client does not hold yet; ``total`` is the full count, so a poller
        can prove it did not skip a row.
        """

        transcript = (
            self.chat.subagent_transcript(call_id) if self.chat is not None else None
        )
        views = [asdict(view) for view in transcript or []]
        after = _optional_count((request or {}).get("after"), minimum=0)
        if after is not None:
            # The increment carries non-tail mutations: an earlier card can
            # settle (or keep streaming progress) at its original index long
            # after the cursor moved past it. Re-send those still running;
            # the stale ones are dropped by ``after`` itself, so a client that
            # never sends ``after`` still gets the full snapshot.
            running = [
                view for view in views[:after] if view.get("status") == "running"
            ]
            views = running + views[after:]
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "available": transcript is not None,
            "transcript": views,
            "total": len(transcript or []),
        }


    async def _session(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = op if isinstance(op, str) else ""
        try:
            result = await self._session_op(op, request)
        except SessionLeaseBusy as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    "session_lease_busy",
                    {"op": label},
                )
            )
            return
        except MissingSessionFile as exc:
            self._send(
                failure_frame(
                    request_id,
                    code="session_import_missing",
                    message="session import failed",
                    exc=exc,
                    op="import",
                    path=exc.path,
                )
            )
            return
        except Refusal as exc:
            # Authored validation text ("resume needs path") is already safe;
            # the code tells the busy refusals (cancel the run first) apart
            # from malformed requests.
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "session_invalid",
                    {"op": label},
                )
            )
            return
        except Exception as exc:  # noqa: BLE001 - a bad session op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="session_failed",
                    message="session failed",
                    exc=exc,
                    op=label,
                )
            )
            return
        self._send({"type": "result", "id": request_id, "ok": True, **(result or {})})


    async def _session_op(self, op: str, request: dict) -> dict:
        if self.runtime is None or self.sessions is None:
            raise Refusal("session requests need a runtime")
        if op == "current":
            path = self.runtime.session.path
            return {"path": str(path), "name": path.stem}
        if op == "list":
            scope = "all" if request.get("scope") == "all" else "project"
            return await session_list_frame(
                self.sessions, scope=scope, archived=_archived_mode(request)
            )
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
                raise Refusal("resume needs path")
            await self.sessions.resume(Path(target))
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(self.runtime.session.path)}
        if op == "import":
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise Refusal("import needs path")
            path = Path(target).expanduser()
            if not path.is_file():
                raise MissingSessionFile(path)
            await self.runtime.switch_session(path)
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "undo":
            if self.busy:
                raise Refusal(
                    "busy: cancel the run before undoing", code="session_busy"
                )
            result = self.sessions.undo()
            if result is None:
                return {"dropped": 0, "path": None}
            dropped, path = result
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"dropped": dropped, "path": str(path)}
        if op == "delete":
            if self.busy:
                raise Refusal(
                    "busy: cancel the run before deleting a session",
                    code="session_busy",
                )
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise Refusal("delete needs path")
            trashed = self.sessions.delete(Path(target))
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(trashed)}
        if op == "compact":
            if self.chat is None:
                raise Refusal("compaction needs a runtime")
            notice = await self.chat.compact_now()
            if notice is None:
                return {"compacted": False}
            return {"compacted": True, "path": str(self.runtime.session.path)}
        if op == "retry":
            if self.chat is None:
                raise Refusal("retry needs a runtime")
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
                raise Refusal("promote needs a runtime")
            pending_id = request.get("pending_id")
            if not isinstance(pending_id, int):
                raise Refusal("promote needs pending_id")
            return {"outcome": self.chat.promote(pending_id)}
        if op == "dequeue":
            if self.chat is None:
                raise Refusal("dequeue needs a runtime")
            pending_id = request.get("pending_id")
            if isinstance(pending_id, int):
                # One named row, either lane (the dock's per-row 取回).
                text = self.chat.dequeue_pending(pending_id)
            else:
                text = self.chat.dequeue_follow_up()
            return {"dequeued": text is not None, "text": text}
        if op == "interrupt_pending":
            if self.chat is None:
                raise Refusal("interrupt_pending needs a runtime")
            pending_id = request.get("pending_id")
            if not isinstance(pending_id, int):
                raise Refusal("interrupt_pending needs pending_id")
            sent = await self.chat.interrupt_pending(pending_id)
            return {"sent": sent}
        if op == "clear_continuations":
            if self.chat is None:
                raise Refusal("clear_continuations needs a runtime")
            return {"cleared": self.chat.clear_continuations()}
        if op == "clear_pending":
            if self.chat is None:
                raise Refusal("clear_pending needs a runtime")
            return {"cleared": self.chat.clear_pending()}
        if op == "clear_todos":
            if self.chat is None:
                raise Refusal("clear_todos needs a runtime")
            return {"cleared": self.chat.clear_todos()}
        raise Refusal(f"unknown session op: {op}")
