"""Host-surface (RpcHost) declarations: shapes, HOST_OPS, HOST_PARTIAL.

Request-id routing (prefixes) and the host-initiated push frames are
transport facts, not op declarations.
"""

from __future__ import annotations

from typing import ClassVar

from zenagent.cli.rpc.protocol._core import (
    ANY,
    BOOL,
    BOOL_LAX,
    FLOAT,
    INT,
    STRING,
    STRING_LAX,
    Field,
    NoFields,
    OpSpec,
    WireRequest,
    list_of,
    record,
    ref,
    shape,
)

# --- host (RpcHost) surface --------------------------------------------------
#: The host multiplexes every session slot of one checkout. Its families are
#: declared here with the same machinery as the chat surface; the frontier
#: test scans host.py the same way. Request-id routing (prefixes) and the
#: host-initiated push frames are transport facts, not op declarations.

class HostRunsListRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "project": Field(STRING_LAX),
        "kind": Field(STRING_LAX),
        "agent": Field(STRING_LAX),
        "workItem": Field(STRING_LAX),
        "session": Field(STRING_LAX),
        "status": Field(STRING_LAX),
        "since": Field(INT),
        "limit": Field(INT),
    }


class HostRunsGetRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "project": Field(STRING_LAX),
        "runId": Field(STRING, required=True),
    }


class HostProjectField(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"project": Field(STRING_LAX)}


class HostProjectOpenRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "root": Field(STRING, required=True),
        "resume": Field(STRING_LAX),
        "background": Field(BOOL_LAX),
    }


def _host_shapes() -> list[tuple[str, dict]]:
    return [
        shape("RunView", {
            "id": Field(STRING),
            "kind": Field(STRING),
            "status": Field(STRING),
            "startedAt": Field(INT),
            "updatedAt": Field(INT),
            "endedAt": Field(INT, nullable=True),
            "durationMs": Field(INT, nullable=True),
            "agentId": Field(STRING),
            "session": Field(STRING),
            "workItemId": Field(STRING, nullable=True),
            "parentRunId": Field(STRING, nullable=True),
            "taskId": Field(STRING, nullable=True),
            "label": Field(STRING),
            "termination": Field(STRING, nullable=True),
            "turns": Field(INT, nullable=True),
            "cost": Field(FLOAT),
            "error": Field(STRING, nullable=True),
        }, doc="One ledger run (RunRecord.to_json minus the storage envelope)."),
        shape("ResidentSlot", {
            "project": Field(STRING),
            "key": Field(STRING),
            "session": Field(STRING),
            "active": Field(BOOL),
        }, doc="One resident session slot of the host's hello/project answers."),
    ]


HOST_SHAPES: dict[str, dict] = dict(_host_shapes())

HOST_OPS: dict[str, OpSpec] = {
    "hello": OpSpec("hello", NoFields, {
        "protocol_version": Field(INT),
        "active": Field(STRING),
        "projects": Field(list_of(ref("ResidentSlot"))),
    }),
    "shutdown": OpSpec("shutdown", NoFields, {}),
    "project": OpSpec("project", NoFields, {}, subs={
        "open": OpSpec("project", HostProjectOpenRequest, {
            "root": Field(STRING),
            "key": Field(STRING),
            "session": Field(STRING),
            "active": Field(STRING),
            "resident": Field(list_of(record(ANY))),
        }),
        "close": OpSpec("project", HostProjectField, {
            "project": Field(STRING),
            "resident": Field(list_of(record(ANY))),
        }),
        "switch": OpSpec("project", HostProjectOpenRequest, {
            "root": Field(STRING),
            "key": Field(STRING),
            "session": Field(STRING),
            "active": Field(STRING),
            "resident": Field(list_of(record(ANY))),
        }),
        "relocate": OpSpec("project", NoFields, {
            "old_root": Field(STRING),
            "new_root": Field(STRING),
            "old_key": Field(STRING),
            "new_key": Field(STRING),
            "sessions": Field(INT),
            "moved": Field(INT),
        }),
    }),
    "runs": OpSpec("runs", NoFields, {}, subs={
        "list": OpSpec("runs", HostRunsListRequest, {
            "runs": Field(list_of(ref("RunView"))),
            "total": Field(INT),
        }),
        "get": OpSpec("runs", HostRunsGetRequest, {
            "run": Field(ref("RunView"), nullable=True),
        }),
    }),
}

#: Host kinds the dispatch handles that are not fully declared yet — the
#: shrink-me frontier of the second surface.
HOST_PARTIAL: dict[str, str] = {
    "session": "host-side resume with sibling-slot semantics (frames vary by "
               "slot state)",
    "terminal": "PTY lifecycle + streamed frames (start/stop/resize)",
    "approval": "host-side approval routing for resident-slot pendings",
    "ask": "host-side ask routing",
}


