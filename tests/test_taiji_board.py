"""The taiji board projection: registry rows become the panel's view model.

The wire layer (``cli/rpc.py``) must only translate these dicts; the
field names, truncation and gating semantics live here, next to the
supervisor that owns the records.
"""

from __future__ import annotations

from zenagent.app.taiji import board
from zenagent.app.taiji.registry import WorkerRecord


def _record(status: str = "ok", attention: str = "") -> WorkerRecord:
    return WorkerRecord(
        name="clean-docs",
        branch="task/clean-docs",
        base="main",
        worktree="/tmp/wt",
        status=status,
        created_at=1.0,
        updated_at=2.0,
        session="s.jsonl",
        summary="did things",
        attention=attention,
    )


class _StubSupervisor:
    """Duck-typed: project_worker only needs diffstat; project_board needs the reads."""

    def __init__(self, records: list[WorkerRecord] | None = None):
        self._records = records or []
        self.diffstat_calls: list[str] = []

    def diffstat(self, record):
        self.diffstat_calls.append(record.name)
        return [{"file": "a.md", "insertions": 1, "deletions": 0}]

    def list_workers(self):
        return self._records

    def slots_state(self):
        return {"max": 3, "busy": 1, "queued": 0}


def test_parse_attention_truncates_options():
    raw = '{"question": "q", "options": ["a", 2, "c", "d", "e"]}'
    assert board.parse_attention(raw) == {"question": "q", "options": ["a", "2", "c", "d"]}


def test_parse_attention_falls_back_on_garbage():
    assert board.parse_attention("not json") == {"question": "not json", "options": []}


def test_parse_attention_none_when_empty():
    assert board.parse_attention("") is None


def test_project_worker_gates_diffstat_on_ok():
    stub = _StubSupervisor()
    view = board.project_worker(stub, _record("ok"))
    assert view["diffstat"] == [{"file": "a.md", "insertions": 1, "deletions": 0}]
    assert stub.diffstat_calls == ["clean-docs"]


def test_project_worker_skips_diffstat_when_not_ok():
    stub = _StubSupervisor()
    view = board.project_worker(stub, _record("error"))
    assert view["diffstat"] is None
    assert stub.diffstat_calls == []


def test_project_worker_key_set_is_pinned():
    view = board.project_worker(_StubSupervisor(), _record("ok"))
    assert set(view) == {
        "name",
        "branch",
        "base",
        "status",
        "summary",
        "session",
        "created_at",
        "updated_at",
        "attention",
        "diffstat",
    }


def test_project_worker_parses_attention():
    raw = '{"question": "Continue?", "options": ["yes", "no"]}'
    view = board.project_worker(_StubSupervisor(), _record(attention=raw))
    assert view["attention"] == {"question": "Continue?", "options": ["yes", "no"]}


def test_project_board_active_shape():
    record = _record("ok")
    stub = _StubSupervisor([record])
    result = board.project_board(stub, router_busy=True)
    assert result == {
        "active": True,
        "router_busy": True,
        "slots": {"max": 3, "busy": 1, "queued": 0},
        "workers": [board.project_worker(stub, record)],
    }


def test_project_worker_preserves_scalar_fields():
    record = _record("merged")
    view = board.project_worker(_StubSupervisor(), record)
    assert view["name"] == record.name
    assert view["branch"] == record.branch
    assert view["base"] == record.base
    assert view["status"] == "merged"
    assert view["summary"] == "did things"
    assert view["session"] == "s.jsonl"
    assert view["created_at"] == 1.0
    assert view["updated_at"] == 2.0
    assert view["attention"] is None
