"""The ``runs`` wire handlers: the project's run ledger.

Runs are host-level because they span every mode: a project may have coding
runs, a zen router and its workers, and swarm rounds in the same ledger. The
handlers are read-only; the writers are the runtimes themselves.
"""

from __future__ import annotations

from pathlib import Path

from zenagent.app.runs import RunLedger, RunRecord
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.project import find_project_root, project_dir

__all__ = ["RunsOps"]


def _run_view(record: RunRecord) -> dict:
    """The wire projection: drop the storage envelope, keep the fields."""
    record_json = record.to_json()
    record_json.pop("type", None)
    record_json.pop("version", None)
    return record_json


def _optional_str(request: dict, key: str) -> str | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"{key} must be a non-empty string")
    return value


def _optional_int(request: dict, key: str, *, minimum: int = 0) -> int | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise Refusal(f"{key} must be an integer >= {minimum}")
    return value


class RunsOps:
    """Mixin: see :class:`~.host.RpcHost` for the composed dispatcher."""

    async def _runs_op(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            root = self._runs_root(request)
            ledger = RunLedger(project_dir(root=root) / "runs.jsonl")
            if op == "list":
                rows = ledger.list(
                    kind=_optional_str(request, "kind"),
                    agent=_optional_str(request, "agent"),
                    work_item=_optional_str(request, "workItem"),
                    session=_optional_str(request, "session"),
                    status=_optional_str(request, "status"),
                    since=_optional_int(request, "since"),
                    limit=_optional_int(request, "limit", minimum=1) or 100,
                )
                self._send(
                    {
                        "type": "result",
                        "id": request_id,
                        "ok": True,
                        "runs": [_run_view(row) for row in rows],
                        "total": len(rows),
                    }
                )
                return
            if op == "get":
                run_id = _optional_str(request, "runId")
                if run_id is None:
                    raise Refusal("runs get needs runId", code="runs_invalid")
                record = ledger.get(run_id)
                self._send(
                    {
                        "type": "result",
                        "id": request_id,
                        "ok": True,
                        "run": None if record is None else _run_view(record),
                    }
                )
                return
            raise Refusal(f"unknown runs op: {op}", code="runs_invalid")
        except Refusal as exc:
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "runs_invalid",
                    {"op": label},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a bad op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="runs_failed",
                    message="runs failed",
                    exc=exc,
                    op=label,
                )
            )

    def _runs_root(self, request: dict) -> Path:
        """The project a ledger query names, else the foreground project."""
        project = request.get("project")
        if isinstance(project, str) and project.strip():
            return find_project_root(project)
        active = getattr(self, "_active", None)
        if active is not None:
            return find_project_root(active.server.project)
        return find_project_root(self._options.root)
