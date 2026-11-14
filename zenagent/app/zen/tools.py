"""The router's fleet tools: zen dispatch/status surface for the model.

Built once and appended to the runtime's tool set while zen mode is active
(see ``Runtime._recompose``); the fleet resolves lazily at call time so a
disable-mid-session leaves the tools speaking their degradation text instead
of holding a stale object.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.app.zen.fleet import worker_statuses
from zenagent.app.zen.identity import router_fleet
from zenagent.tools._result import text_result

if TYPE_CHECKING:
    from zenagent.app.zen.fleet import RosterRow, ZenFleet

__all__ = ["fleet_tools"]

#: The model sees this when zen mode is off but the tools survived in the
#: registry (a disable without a recompose, or a race).
_NOT_ACTIVE = "Zen mode is not active on this session."

REPORT_PREVIEW_LIMIT = 240


def _fleet(get_fleet: Callable[[], ZenFleet | None]) -> ZenFleet | None:
    try:
        return get_fleet()
    except Exception:  # noqa: BLE001 - a broken resolver degrades, not crashes
        return None


def _resolve_session(fleet: ZenFleet, session: str) -> Path:
    """A bare session stem becomes a file in the project's sessions dir."""
    candidate = Path(session).expanduser()
    if candidate.suffix or str(session).endswith("/"):
        return candidate
    return fleet.sessions_dir / f"{candidate.name}.jsonl"


def _rows_preview(rows: list[RosterRow]) -> list[dict]:
    preview = []
    for row in rows:
        trimmed = dict(row)
        report = str(trimmed.get("last_report") or "")
        if len(report) > REPORT_PREVIEW_LIMIT:
            trimmed["last_report"] = report[:REPORT_PREVIEW_LIMIT] + "…"
        preview.append(trimmed)
    return preview


async def _dispatch(get_fleet, args: dict) -> ToolResultAgent:
    fleet = _fleet(get_fleet)
    if fleet is None:
        return text_result(_NOT_ACTIVE, is_error=True)
    session = str(args.get("session") or "").strip()
    task = str(args.get("task") or "").strip()
    if not session or not task:
        return text_result("fleet_dispatch needs session and task", is_error=True)
    try:
        path = str(_resolve_session(fleet, session))
        status = await fleet.dispatch(path, task)
        queue_note = (
            " The concurrency cap is full; it will start when a slot frees."
            if status == "queued"
            else ""
        )
        return text_result(
            f"Dispatched to {Path(path).name}; status {status}.{queue_note}",
            details={"path": path, "status": status},
        )
    except ValueError as exc:
        return text_result(str(exc), is_error=True)


async def _status(get_fleet, args: dict) -> ToolResultAgent:
    fleet = _fleet(get_fleet)
    if fleet is None:
        return text_result(_NOT_ACTIVE, is_error=True)
    session = str(args.get("session") or "").strip()
    rows = {row["path"]: row for row in fleet.roster()}
    if not session:
        wanted = [row for row in rows.values() if row["role"] == "worker"]
        return text_result(json.dumps(_rows_preview(wanted), ensure_ascii=False))
    try:
        path = str(_resolve_session(fleet, session))
    except Exception:  # noqa: BLE001 - odd input degrades to a miss
        path = session
    row = rows.get(path) or rows.get(session)
    if row is None:
        return text_result(f"no roster row for {session}", is_error=True)
    return text_result(json.dumps(_rows_preview([row])[0], ensure_ascii=False))


def fleet_tools(
    get_fleet: Callable[[], ZenFleet | None],
) -> list[AgentTool]:
    """The six fleet management tools; safe to build before a fleet exists."""
    vocab = " / ".join(worker_statuses)

    async def _list(args: dict) -> ToolResultAgent:
        fleet = _fleet(get_fleet)
        if fleet is None:
            return text_result(_NOT_ACTIVE, is_error=True)
        return text_result(json.dumps(_rows_preview(fleet.roster()), ensure_ascii=False))

    async def _stop(args: dict) -> ToolResultAgent:
        fleet = _fleet(get_fleet)
        if fleet is None:
            return text_result(_NOT_ACTIVE, is_error=True)
        session = str(args.get("session") or "").strip()
        if not session:
            return text_result("fleet_stop needs session", is_error=True)
        stopped = await fleet.stop(str(_resolve_session(fleet, session)))
        if not stopped:
            return text_result(f"no running worker at {session}", is_error=True)
        return text_result(f"Stopped {Path(session).name}; it reports blocked.")

    async def _focus(args: dict) -> ToolResultAgent:
        fleet = _fleet(get_fleet)
        if fleet is None:
            return text_result(_NOT_ACTIVE, is_error=True)
        session = str(args.get("session") or "").strip()
        focus = str(args.get("focus") or "").strip()
        if not session or not focus:
            return text_result("fleet_focus needs session and focus", is_error=True)
        fleet.focus(str(_resolve_session(fleet, session)), focus)
        return text_result(f"Focus of {Path(session).name} is now: {focus}")

    async def _spawn(args: dict) -> ToolResultAgent:
        fleet = _fleet(get_fleet)
        if fleet is None:
            return text_result(_NOT_ACTIVE, is_error=True)
        focus = str(args.get("focus") or "").strip()
        if not focus:
            return text_result("fleet_spawn needs focus", is_error=True)
        path = fleet.spawn(focus)
        task = str(args.get("task") or "").strip()
        if not task:
            return text_result(f"Spawned worker {Path(path).name} (idle, focus: {focus}).")
        status = await fleet.dispatch(path, task)
        return text_result(
            f"Spawned worker {Path(path).name} (focus: {focus}); dispatched, status {status}.",
            details={"path": path, "status": status},
        )

    session_schema = {
        "type": "object",
        "properties": {
            "session": {
                "type": "string",
                "description": "Worker session: a bare stem or the session file path.",
            }
        },
    }

    return [
        AgentTool(
            name="fleet_list",
            description=(
                "List the project's zen fleet: one router row and every worker "
                "session with its focus and status. Call this before dispatching."
            ),
            parameters={"type": "object", "properties": {}},
            approval="read",
            execute=lambda call_id, args, abort, on_update: _list(args),
        ),
        AgentTool(
            name="fleet_dispatch",
            description=(
                "Assign a task to one worker session and return immediately; its "
                "report arrives as a [zen:worker] message later. Never wait for it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "session": session_schema["properties"]["session"],
                    "task": {"type": "string", "description": "Concrete, verifiable task."},
                },
                "required": ["session", "task"],
            },
            approval="write",
            execute=lambda call_id, args, abort, on_update: _dispatch(get_fleet, args),
        ),
        AgentTool(
            name="fleet_status",
            description=(
                f"One worker's status and last report (no session: every worker). "
                f"Statuses: {vocab}."
            ),
            parameters=session_schema,
            approval="read",
            execute=lambda call_id, args, abort, on_update: _status(get_fleet, args),
        ),
        AgentTool(
            name="fleet_stop",
            description="Abort one worker's running task; it reports blocked.",
            parameters=session_schema,
            approval="write",
            execute=lambda call_id, args, abort, on_update: _stop(args),
        ),
        AgentTool(
            name="fleet_focus",
            description="Set what one worker session concentrates on.",
            parameters={
                "type": "object",
                "properties": {
                    "session": session_schema["properties"]["session"],
                    "focus": {"type": "string", "description": "Domain or event to focus on."},
                },
                "required": ["session", "focus"],
            },
            approval="write",
            execute=lambda call_id, args, abort, on_update: _focus(args),
        ),
        AgentTool(
            name="fleet_spawn",
            description=(
                "Create a new worker session with a focus; with task, dispatch to "
                "it right away. Use when no existing worker's focus fits."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "description": "Domain or event to focus on."},
                    "task": {"type": "string", "description": "Optional first task to dispatch."},
                },
                "required": ["focus"],
            },
            approval="write",
            execute=lambda call_id, args, abort, on_update: _spawn(args),
        ),
    ]


def _router_tool_provider(rt) -> list:
    """The fleet tools while ``rt``'s session is the fleet's router session."""
    if router_fleet(rt.zen_fleet, rt.session.path) is None:
        return []
    return fleet_tools(lambda: router_fleet(rt.zen_fleet, rt.session.path))


def register_router_surface(runtime) -> None:
    """Attach zen's router surface (tools + prompt note) to a runtime.

    The kernel no longer names the zen package (audit P0-3): the wiring that
    attaches a fleet registers these providers once; they read the live
    ``runtime.zen_fleet`` on every recompose, so detach and re-attach need no
    bookkeeping here. Idempotent.
    """
    def _note():
        from zenagent.app.zen.prompts import ROUTER_PROMPT_NOTE

        if router_fleet(runtime.zen_fleet, runtime.session.path) is None:
            return None
        return ROUTER_PROMPT_NOTE

    if _router_tool_provider not in runtime.tool_providers:
        runtime.tool_providers.append(_router_tool_provider)
    if _note not in runtime.prompt_note_providers:
        runtime.prompt_note_providers.append(_note)
