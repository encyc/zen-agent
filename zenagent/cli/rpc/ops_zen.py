"""The zen_fleet ops: enable/disable/state/transcript for the zen router.

Enable turns the slot's current session over to the project's router session
(the one interactive session of zen mode) and builds the slot's
:class:`~zenagent.app.zen.fleet.ZenFleet`; disable closes the fleet and
restores the session zen replaced. Roster changes are pushed as server-initiated
``zen_fleet`` frames (``_send_fleet_roster``) so a GUI's worker list stays live
without polling.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from zenagent.agent.session import Session
from zenagent.app.views import TranscriptBuilder
from zenagent.app.zen.fleet import ZenFleet
from zenagent.app.zen.roster import latest_router, set_role
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.cli.rpc.ops_session import _optional_count, _page_views
from zenagent.project import project_sessions_dir

__all__ = ["ZenOps"]


class ZenOps:
    """Mixin: see server.py for the composed RpcServer."""

    #: The session zen replaced, restored on disable (one zen toggle per slot).
    _pre_zen_session: str | None = None

    async def _zen(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            if op == "enable":
                await self._zen_enable(request_id)
            elif op == "disable":
                await self._zen_disable(request_id)
            elif op == "state":
                self._zen_state(request_id)
            elif op == "transcript":
                self._zen_transcript(request, request_id)
            else:
                raise Refusal(f"unknown zen_fleet op: {op}")
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "zen_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="zen_failed",
                    message="zen_fleet failed",
                    exc=exc,
                    op=label,
                )
            )

    def _zen_fleet(self) -> ZenFleet | None:
        return getattr(self.runtime, "zen_fleet", None)

    def _require_idle(self) -> None:
        """Zen toggling must not interrupt a run or drop a queued follow-up."""
        if self.busy:
            raise Refusal("zen needs an idle session", code="zen_busy")
        chat = self.chat
        if chat is not None and chat.busy:
            raise Refusal("zen needs an idle session", code="zen_busy")

    async def _zen_enable(self, request_id) -> None:
        self._require_idle()
        runtime = self.runtime
        sessions_dir = project_sessions_dir(runtime.root)
        # A fresh project may never have had a session; the roster sidecar
        # (and the router transcript) need the directory to exist.
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._pre_zen_session = str(runtime.session.path)
        router_stem = latest_router(sessions_dir)
        if router_stem is None:
            runtime.use_new_session()
        else:
            await runtime.switch_session(sessions_dir / f"{router_stem}.jsonl")
        set_role(sessions_dir, Path(runtime.session.path).stem, "router")
        fleet = ZenFleet(
            chat=self.chat,
            root=runtime.root,
            sessions_dir=sessions_dir,
            router_path=str(runtime.session.path),
            on_change=self._send_fleet_roster,
        )
        runtime.zen_fleet = fleet
        runtime._recompose()
        self._send_fleet_roster()
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "enabled": True,
                "router": str(runtime.session.path),
                "session": str(runtime.session.path),
                "roster": fleet.roster(),
            }
        )

    async def _zen_disable(self, request_id) -> None:
        self._require_idle()
        runtime = self.runtime
        fleet = self._zen_fleet()
        pre = self._pre_zen_session
        self._pre_zen_session = None
        if fleet is None and pre is None:
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "enabled": False,
                    "roster": [],
                }
            )
            return
        if fleet is not None:
            await fleet.close()
        runtime.zen_fleet = None
        if pre is not None and Path(pre).exists():
            await runtime.switch_session(pre)
        else:
            runtime.use_new_session()
        runtime._recompose()
        self._send_fleet_roster()
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "enabled": False,
                "session": str(runtime.session.path),
                "roster": [],
            }
        )

    def _zen_state(self, request_id) -> None:
        fleet = self._zen_fleet()
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "enabled": fleet is not None,
                "router": str(self.runtime.session.path) if fleet else "",
                "roster": fleet.roster() if fleet is not None else [],
            }
        )

    def _zen_transcript(self, request: dict, request_id) -> None:
        """A read-only view of one session file (a worker's transcript)."""
        raw = request.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise Refusal("zen transcript needs path", code="zen_invalid")
        path = Path(raw).expanduser()
        if not path.exists():
            raise Refusal(f"no such session: {path.name}", code="zen_invalid")
        session = Session(path)
        messages = session.load()
        builder = TranscriptBuilder()
        builder.load(messages, list(session.records))
        views = [asdict(view) for view in builder.views]
        total = len(views)
        paged, has_more_before = _page_views(
            views,
            limit=_optional_count(request.get("limit"), minimum=1),
            before=_optional_count(request.get("before"), minimum=0),
        )
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "transcript": paged,
                "notices": [],
                "compactions": [],
                "hasMoreBefore": has_more_before,
                "total": total,
            }
        )

    def _send_fleet_roster(self) -> None:
        """Push the current roster as a server-initiated frame."""
        fleet = self._zen_fleet()
        self._send(
            {
                "type": "zen_fleet",
                "enabled": fleet is not None,
                "roster": fleet.roster() if fleet is not None else [],
            }
        )
