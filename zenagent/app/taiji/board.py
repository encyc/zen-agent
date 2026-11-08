"""The dispatch board projection: registry rows as the panel renders them.

Field names, truncation and gating here are wire contract — the desktop
mirrors them in ``desktop/src/rpc/types.ts`` and
``tests/test_rpc_contract.py`` pins the key sets. Change a key here and
change the TS interface in the same commit.
"""

from __future__ import annotations

import json

from zenagent.app.taiji.registry import WorkerRecord

#: The panel shows at most four attention options; the rest wait in chat.
MAX_ATTENTION_OPTIONS = 4


def parse_attention(raw: str) -> dict | None:
    """One worker's attention payload as the board shows it.

    ``None`` when the worker is not asking anything; a question with the
    raw text as fallback when the payload is not structured.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return {"question": raw, "options": []}
    if not isinstance(data, dict):
        return {"question": raw, "options": []}
    options = [
        str(label)
        for label in (data.get("options") or [])
        if isinstance(label, (str, int, float))
    ][:MAX_ATTENTION_OPTIONS]
    return {"question": str(data.get("question", "")), "options": options}


def project_worker(supervisor, record: WorkerRecord) -> dict:
    """One registry row as the board renders it."""
    return {
        "name": record.name,
        "branch": record.branch,
        "base": record.base,
        "status": record.status,
        "summary": record.summary,
        "session": record.session,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "attention": parse_attention(record.attention),
        "prompt": record.prompt,
        "diffstat": supervisor.diffstat(record) if record.status == "ok" else None,
    }


def project_board(supervisor, router_busy: bool) -> dict:
    """The whole board body for an active host."""
    return {
        "active": True,
        "router_busy": router_busy,
        "slots": supervisor.slots_state(),
        "workers": [project_worker(supervisor, record) for record in supervisor.list_workers()],
    }
