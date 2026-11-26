"""The ``automations`` wire domain: manage scheduled runs from the desktop.

Host-level, like ``runs``: the desktop has to manage automations in projects it
has never opened, so **no op here constructs a Runtime or materialises a
transcript**. Everything they need is a file:

* ``automations.json`` — read and written here (atomic replace);
* ``automations.state.json`` — read only, written by the daemon;
* ``automations.request.json`` — written by ``run``, consumed by the daemon;
* ``runs.jsonl`` — read for the last round's cost.

When the daemon is not running the ops still succeed and say so in ``warnings``:
the definitions are valid either way, and a UI that hid its content because a
background process was down would be lying about what is configured.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from zenagent import project
from zenagent.agent.session import unique_session_path
from zenagent.app.automations import (
    SCHEDULE_EVERY,
    AutomationStore,
    Job,
    Schedule,
    StateStore,
    _atomic_write_json,
    state_for,
)
from zenagent.app.runs import RunLedger
from zenagent.cli.daemon import daemon_running
from zenagent.cli.rpc.errors import Refusal, failure_frame

__all__ = ["AutomationsOps"]

logger = logging.getLogger(__name__)

_REQUEST = "automations.request.json"


def _text(request: dict, key: str, *, required: bool = True) -> str:
    value = request.get(key)
    if value is None:
        if required:
            raise Refusal(f"{key} is required")
        return ""
    if not isinstance(value, str):
        raise Refusal(f"{key} must be a string")
    return value


def _root(self, request: dict) -> Path:
    """The project an op names, else the foreground project."""
    named = request.get("project")
    if isinstance(named, str) and named.strip():
        return project.find_project_root(named)
    active = getattr(self, "_active", None)
    if active is not None:
        return project.find_project_root(active.server.project)
    return project.find_project_root(self._options.root)


def _schedule_text(schedule: Schedule) -> str:
    # NOTE: compare against the constant, not a literal — tests/test_rpc_contract
    # scans this package for `kind == "<literal>"` patterns to keep the wire's
    # request list honest, and a schedule kind is not a wire kind.
    if schedule.kind == SCHEDULE_EVERY:
        if schedule.seconds % 3600 == 0:
            return f"every {schedule.seconds // 3600}h"
        return f"every {schedule.seconds // 60}m"
    return f"daily {schedule.at}"


class AutomationsOps:
    #: Set by an op that deliberately did nothing (currently only "run" with no
    #: daemon); cleared at the top of every request.
    _last_skip: str = ""

    """Mixin: see :class:`~.host.RpcHost` for the composed dispatcher.

    ``_send``/``_active``/``_options`` come from the composed host; they are
    declared here so this module type-checks on its own (the sibling mixins are
    grandfathered in the mypy ratchet, new modules are not).
    """

    #: Provided by the composed :class:`~.host.RpcHost`; declared for mypy.
    def _send(self, payload: dict) -> None:  # type: ignore[empty-body]
        """Send one frame down the host's write line."""

    async def _automations_op(self, request: dict, request_id) -> None:
        op = str(request.get("op") or "")
        self._last_skip = ""
        try:
            rows, warnings = self._automations_rows(request, op)
        except Refusal as exc:
            # A refusal is a normal outcome (bad schedule, unknown name), not a
            # failure to log: build the plain error frame.
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "code": "automations_refused",
                    "message": str(exc),
                }
            )
            return
        except (OSError, ValueError) as exc:
            self._send(
                failure_frame(
                    request_id,
                    code="automations_failed",
                    message="automations failed",
                    exc=exc,
                    op=op,
                )
            )
            return
        frame: dict = {
            "type": "result",
            "id": request_id,
            "ok": True,
            "jobs": rows,
            "warnings": warnings,
        }
        if self._last_skip:
            # "run now" that queued nothing: a client must be able to tell this
            # from success without parsing the warning text.
            frame["skipped"] = self._last_skip
        self._send(frame)

    # -- the work ------------------------------------------------------------

    def _automations_rows(
        self, request: dict, op: str
    ) -> tuple[list[dict], list[str]]:
        warnings: list[str] = []
        if op == "list_all":
            rows: list[dict] = []
            known = self._known_projects()
            for root in known:
                project_rows, project_warnings = self._rows_for(root, warnings)
                rows.extend(project_rows)
                warnings.extend(project_warnings)
            # list_all spans projects with their own daemons: there is no single
            # "is it running" to report, so each row stands on its own.
            return rows, warnings

        root = _root(self, request)
        if op == "list":
            return self._rows_for(root, warnings)
        if op == "add":
            self._add(root, request)
        elif op == "update":
            self._update(root, request)
        elif op == "rm":
            self._remove(root, request)
        elif op == "run":
            if self._request_run(root, request) == "no-daemon":
                rows, own = self._rows_for(root, warnings)
                self._last_skip = "no-daemon"
                return rows, [
                    *warnings,
                    *own,
                    f"no daemon is running for {root}; start it with `zen daemon`",
                ]
        else:
            raise Refusal(f"unknown automations op: {op!r}")
        if not daemon_running(root):
            warnings.append(
                f"no daemon is running for {root}; schedules are saved but idle"
            )
        rows, own = self._rows_for(root, warnings)
        return rows, [*warnings, *own]

    def _known_projects(self) -> list[Path]:
        """Every project that has an automations file, plus the foreground one."""
        roots: list[Path] = []
        projects_root = project.projects_root()
        if projects_root.is_dir():
            for candidate in sorted(projects_root.glob("*/automations.json")):
                meta = candidate.parent / "meta.json"
                try:
                    payload = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                root = payload.get("root") if isinstance(payload, dict) else None
                if isinstance(root, str) and root:
                    roots.append(Path(root))
        foreground = _root(self, {})
        if foreground not in roots:
            roots.append(foreground)
        return roots

    def _rows_for(self, root: Path, warnings: list[str]) -> tuple[list[dict], list[str]]:
        """This project's rows plus its own warnings (never mutating ``warnings``).

        Returns the warnings instead of appending to the caller's list: mutating
        it made `list_all` extend a list with itself, doubling every warning per
        project.
        """
        store = AutomationStore(root)
        jobs = store.load()
        mine = [f"{root}: {warning}" for warning in store.warnings]
        if not jobs:
            return [], mine
        states = StateStore(root).load()
        ledger = RunLedger(project.project_dir(root=root) / "runs.jsonl")
        runs = ledger.list(kind="automation")
        now = time.time()
        rows: list[dict] = []
        for job in jobs:
            state = state_for(states, job)
            labelled = [row for row in runs if row.label == job.name]
            rows.append(
                {
                    "project": str(root),
                    "name": job.name,
                    "prompt": job.prompt,
                    "schedule": _schedule_text(job.schedule),
                    "enabled": job.enabled,
                    "timeout_s": job.timeout_s,
                    "session": state.session or job.session,
                    "next_due": job.schedule.next_due(now, state.last_run_at),
                    "last_status": state.last_status,
                    "last_skip": state.last_skip,
                    "last_error": state.last_error,
                    "last_run_at": state.last_run_at or 0,
                    "missed": state.missed,
                    "run_count": state.run_count,
                    # `list()` is newest-first: the newest cost is the first row.
                    "cost": labelled[0].cost if labelled else 0.0,
                }
            )
        return rows, mine

    def _remember_project(self, root: Path) -> None:
        """Make sure ``meta.json`` exists.

        ``list_all`` discovers projects by globbing their machine-local state
        directory, and an automation added from the desktop to a project that
        was never opened has no metadata yet. Writing it here is what lets the
        next ``list_all`` find this project at all.
        """
        if project.load_project_meta(root=root) is None:
            project.write_project_meta(root=root)

    def _add(self, root: Path, request: dict) -> None:
        name = _text(request, "name")
        prompt = _text(request, "prompt")
        schedule = Schedule.parse(_text(request, "schedule"))
        timeout = request.get("timeout_s", 1800)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise Refusal("timeout_s must be a positive integer")
        session = _text(request, "session", required=False)
        if session and Path(session).name != session:
            # The transcript lives in the project's sessions dir; a path here
            # would write outside it and break the move-the-project rule.
            raise Refusal("session must be a bare file name, not a path")
        store = AutomationStore(root)
        store.load()
        self._remember_project(root)
        store.add(
            Job(
                id="",
                name=name,
                prompt=prompt,
                schedule=schedule,
                session=session or unique_session_path(root=root).name,
                timeout_s=timeout,
            )
        )

    def _update(self, root: Path, request: dict) -> None:
        name = _text(request, "name")
        changes: dict = {}
        if "enabled" in request:
            changes["enabled"] = bool(request.get("enabled"))
        if "prompt" in request:
            changes["prompt"] = _text(request, "prompt")
        if "schedule" in request:
            changes["schedule"] = Schedule.parse(_text(request, "schedule"))
        if "timeout_s" in request:
            timeout = request.get("timeout_s")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                raise Refusal("timeout_s must be a positive integer")
            changes["timeout_s"] = timeout
        if not changes:
            raise Refusal("nothing to update")
        store = AutomationStore(root)
        store.load()
        try:
            store.update(name, **changes)
        except KeyError as exc:
            raise Refusal(f"no automation named {name!r}") from exc

    def _remove(self, root: Path, request: dict) -> None:
        name = _text(request, "name")
        store = AutomationStore(root)
        store.load()
        if not store.remove(name):
            raise Refusal(f"no automation named {name!r}")

    def _request_run(self, root: Path, request: dict) -> str:
        name = _text(request, "name")
        store = AutomationStore(root)
        store.load()
        if store.get(name) is None:
            raise Refusal(f"no automation named {name!r}")
        if not daemon_running(root):
            # Not an error — the definition is fine, the scheduler is asleep —
            # but never a silent no-op either: the caller turns this into a
            # visible "start the daemon" warning.
            return "no-daemon"
        path = project.project_dir(root=root) / _REQUEST
        _atomic_write_json(path, {"jobs": [{"name": name, "at": time.time()}]})
        return "requested"
