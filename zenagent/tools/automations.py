"""The two automations tools: let the agent manage this project's schedules.

The common shape of the request is a conversation — "watch the daily report,
tell me when approval drops" — and then "do that every morning at nine". Making
the user leave that conversation to retype it as CLI arguments loses the context
the conversation just built, so the agent creates the automation instead.

Two tools, not one, because the approval tier is declared per tool:

* :func:`build_automation_tools` returns ``automations`` (``read``: list/show)
  and ``automation_edit`` (``write``: create/update/delete/run). A single tool
  would either make ``list`` need a confirmation or let a write skip one.
* The project is closed over from ``root``, exactly like the session tools: the
  state lives under ``~/.zenagent``, which the permission gate treats as
  ``protected``, so a ``path`` argument would be refused before the tool ran —
  and would let the model write a schedule into somebody else's project.

Nothing here runs a round directly. ``run`` drops a request in the daemon's
mailbox, because ``automations.state.json`` has exactly one writer: the daemon.
With no daemon running the tool says so instead of pretending.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from zenagent.agent.protocol import AgentTool
from zenagent.app.automations import (
    AutomationStore,
    Job,
    Schedule,
    StateStore,
    _atomic_write_json,
    daemon_running,
    state_for,
)
from zenagent.app.runs import RunLedger
from zenagent.project import project_dir
from zenagent.prompts import tool_description
from zenagent.tools._result import text_result

__all__ = ["build_automation_tools"]

_EDIT_ACTIONS = frozenset({"create", "update", "delete", "run"})
_READ_ACTIONS = frozenset({"list", "show"})

#: The unattended boundary, in the same words the CLI prints. A model that
#: paraphrases this as "you have full permission" would be lying to the user.
PERMISSION_NOTICE = (
    "unattended rounds run in auto mode: writes anywhere on this machine "
    "(including credential paths) and destructive shell commands run without "
    "asking; catastrophic commands, anything in [tools] deny, and reads of "
    "protected credential paths are still refused."
)

_LIST_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "show"]},
        "name": {"type": "string", "description": "required for show"},
    },
    "required": ["action"],
    "additionalProperties": False,
}

_EDIT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["create", "update", "delete", "run"]},
        "name": {"type": "string", "description": "required except for create"},
        "prompt": {"type": "string", "description": "what to do each round"},
        "schedule": {
            "type": "string",
            "description": '"daily HH:MM" or "every N<m|h>" (local time)',
        },
        "timeout_s": {"type": "integer", "description": "per-round budget, default 1800"},
        "enabled": {"type": "boolean", "description": "for update: pause or resume"},
    },
    "required": ["action"],
    "additionalProperties": False,
}


def _schedule_text(schedule: Schedule) -> str:
    if schedule.kind == "every":
        if schedule.seconds % 3600 == 0:
            return f"every {schedule.seconds // 3600}h"
        return f"every {schedule.seconds // 60}m"
    return f"daily {schedule.at}"


def _row(root: Path, job: Job, states, now: float) -> dict:
    """One automation as the model and the reader both need it."""
    state = state_for(states, job)
    ledger = RunLedger(project_dir(root=root) / "runs.jsonl")
    runs = [row for row in ledger.list(kind="automation") if row.label == job.name]
    return {
        "name": job.name,
        "schedule": _schedule_text(job.schedule),
        "enabled": job.enabled,
        "prompt": job.prompt,
        "session": state.session or job.session,
        "next_due": job.schedule.next_due(now, state.last_run_at),
        "last_status": state.last_status,
        "last_skip": state.last_skip,
        "last_error": state.last_error,
        "run_count": state.run_count,
        "missed": state.missed,
        # `list()` is newest-first: the most recent run is the first row.
        "cost": runs[0].cost if runs else 0.0,
        "daemon_running": daemon_running(root),
    }


def _format_row(row: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["next_due"]))
    status = row["last_skip"] or row["last_status"] or "never run"
    return (
        f"- {row['name']} · {row['schedule']} · "
        f"{'enabled' if row['enabled'] else 'paused'} · "
        f"{status} · next {when} · ${row['cost']:.4f}"
    )


def _list(root: Path, now: float) -> str:
    store = AutomationStore(root)
    jobs = store.load()
    if not jobs:
        text = (
            "No automations in this project yet. Use action=create with a prompt "
            "and a schedule to add one."
        )
        if not daemon_running(root):
            text += " No scheduler is running here either: starting one needs `zen daemon`."
        return text
    states = StateStore(root).load()
    rows = [_row(root, job, states, now) for job in jobs]
    lines = [f"{len(rows)} automation(s) in {root}:"]
    lines.extend(_format_row(row) for row in rows)
    if not daemon_running(root):
        lines.append(
            "No scheduler is running for this project: these are saved but will "
            "not run until the user starts one with `zen daemon`."
        )
    return "\n".join(lines)


def _show(root: Path, name: str, now: float) -> str:
    store = AutomationStore(root)
    store.load()
    job = store.get(name)
    if job is None:
        return f"No automation named {name!r} in {root}."
    row = _row(root, job, StateStore(root).load(), now)
    lines = [_format_row(row), f"  prompt: {row['prompt']}", f"  session: {row['session']}"]
    if row["last_error"]:
        lines.append(f"  last error: {row['last_error']}")
    return "\n".join(lines)


def _create(root: Path, args: dict, now: float) -> str:
    prompt = str(args.get("prompt") or "").strip()
    raw_schedule = str(args.get("schedule") or "").strip()
    name = str(args.get("name") or "").strip()
    if not prompt or not raw_schedule:
        return "create needs both prompt and schedule."
    try:
        schedule = Schedule.parse(raw_schedule)
    except (TypeError, ValueError) as exc:
        return f"{exc}; use \"daily HH:MM\" or \"every N<m|h>\"."
    timeout = args.get("timeout_s", 1800)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        return "timeout_s must be a positive integer."
    if not name:
        name = time.strftime("automation-%H%M%S")

    store = AutomationStore(root)
    store.load()
    try:
        saved = store.add(
            Job(
                id="",
                name=name,
                prompt=prompt,
                schedule=schedule,
                # Mint the name only: a session file appears when it first runs.
                session=_mint_session_name(root),
                timeout_s=timeout,
            )
        )
    except ValueError as exc:
        return str(exc)

    lines = [
        f"Created {saved.name} ({_schedule_text(saved.schedule)}).",
        f"Results will land in the session {saved.session} once it first runs.",
        f"Permission boundary: {PERMISSION_NOTICE}",
    ]
    if not daemon_running(root):
        lines.append(
            "No scheduler is running for this project, so it will not run on its "
            "own: the user needs to start one with `zen daemon` (or install it "
            "with `zen daemon install`)."
        )
    return "\n".join(lines)


def _mint_session_name(root: Path) -> str:
    from zenagent.agent.session import unique_session_path

    return unique_session_path(root=root).name


def _update(root: Path, args: dict, now: float) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "update needs a name."
    changes: dict[str, Any] = {}
    if "prompt" in args:
        changes["prompt"] = str(args["prompt"])
    if "schedule" in args:
        try:
            changes["schedule"] = Schedule.parse(str(args["schedule"]))
        except (TypeError, ValueError) as exc:
            return f"{exc}; use \"daily HH:MM\" or \"every N<m|h>\"."
    if "timeout_s" in args:
        timeout = args["timeout_s"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            return "timeout_s must be a positive integer."
        changes["timeout_s"] = timeout
    if "enabled" in args:
        changes["enabled"] = bool(args["enabled"])
    if not changes:
        return "update needs at least one of prompt, schedule, timeout_s or enabled."

    store = AutomationStore(root)
    store.load()
    try:
        updated = store.update(name, **changes)
    except KeyError:
        return f"No automation named {name!r} in {root}."
    except ValueError as exc:
        return str(exc)
    return f"Updated {updated.name} ({_schedule_text(updated.schedule)}, " + (
        "enabled" if updated.enabled else "paused"
    ) + ")."


def _delete(root: Path, args: dict) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "delete needs a name."
    store = AutomationStore(root)
    store.load()
    job = store.get(name)
    if job is None or not store.remove(name):
        return f"No automation named {name!r} in {root}."
    return (
        f"Deleted {name}. Its transcript ({job.session}) is kept, so the history "
        "of past runs is still readable."
    )


def _run(root: Path, args: dict) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "run needs a name."
    store = AutomationStore(root)
    store.load()
    if store.get(name) is None:
        return f"No automation named {name!r} in {root}."
    if not daemon_running(root):
        # Never claim a run that will not happen: state has one writer and it
        # is not this process.
        return (
            f"Nothing was run: no scheduler is running for {root}. The automation "
            "is saved; the user needs `zen daemon` for it to execute."
        )
    request = project_dir(root=root) / "automations.request.json"
    _atomic_write_json(request, {"jobs": [{"name": name, "at": time.time()}]})
    return f"Asked the scheduler to run {name} now; it picks this up within 30 seconds."


def build_automation_tools(root: Path) -> list[AgentTool]:
    """The read and write automation tools, pinned to ``root``'s project."""
    pinned = Path(root)

    async def automations(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ):
        action = str(args.get("action") or "")
        if action not in _READ_ACTIONS:
            return text_result(
                f"unknown action {action!r}; use list or show",
                is_error=True,
            )
        now = time.time()
        if action == "list":
            return text_result(_list(pinned, now))
        name = str(args.get("name") or "").strip()
        if not name:
            return text_result("show needs a name.", is_error=True)
        return text_result(_show(pinned, name, now))

    async def automation_edit(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ):
        action = str(args.get("action") or "")
        if action not in _EDIT_ACTIONS:
            return text_result(
                f"unknown action {action!r}; use create, update, delete or run",
                is_error=True,
            )
        now = time.time()
        if action == "create":
            return text_result(_create(pinned, args, now))
        if action == "update":
            return text_result(_update(pinned, args, now))
        if action == "delete":
            return text_result(_delete(pinned, args))
        return text_result(_run(pinned, args))

    return [
        AgentTool(
            name="automations",
            description=tool_description("automations"),
            parameters=_LIST_PARAMETERS,
            execute=automations,
            approval="read",
            timeout_s=30.0,
        ),
        AgentTool(
            name="automation_edit",
            description=tool_description("automation_edit"),
            parameters=_EDIT_PARAMETERS,
            execute=automation_edit,
            approval="write",
            timeout_s=30.0,
        ),
    ]
