"""Host-surface (RpcHost) declarations: shapes, HOST_OPS, HOST_PARTIAL.

Request-id routing (prefixes) and the host-initiated push frames (workspace
updates, swarm role events) are transport facts, not op declarations.
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
    one_of,
    record,
    ref,
    shape,
)

# --- host (RpcHost) surface --------------------------------------------------
#: The host multiplexes every session slot of one checkout. Its families are
#: declared here with the same machinery as the chat surface; the frontier
#: test scans host.py the same way. Request-id routing (prefixes) and the
#: host-initiated push frames (workspace updates, swarm role events) are
#: transport facts, not op declarations.

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


class HostSupervisorRoot(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"project": Field(STRING_LAX)}


class HostSupervisorCancelRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "project": Field(STRING_LAX),
        "key": Field(STRING),  # `key` or `agentId`; the handler or-s them
        "agentId": Field(STRING),
    }


class HostSupervisorApproveRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "project": Field(STRING_LAX),
        "requestId": Field(STRING, required=True),
        "decision": Field(one_of("allow", "always", "deny")),
    }


class HostWorkspaceSetRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "project": Field(STRING_LAX),
        "mode": Field(one_of("coding", "zen", "swarm")),
        "routerSession": Field(STRING),
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
        shape("SupervisorAgentRow", {
            "key": Field(STRING),
            "agent": Field(STRING),
            "kind": Field(STRING),
            "label": Field(STRING),
            "state": Field(one_of("running", "idle")),
            "session": Field(STRING),
            "runId": Field(STRING, nullable=True),
        }, doc="One live agent of the project supervisor (plus the swarm "
               "scope keys the handle carries)."),
        shape("WorkspaceStateWire", {
            "project": Field(STRING),
            "mode": Field(one_of("coding", "zen", "swarm")),
            "updatedAt": Field(FLOAT),
            "routerSession": Field(STRING, nullable=True),
        }, doc="The project's backend-mode fact (result and push frame)."),
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
    "supervisor": OpSpec("supervisor", NoFields, {}, subs={
        "status": OpSpec("supervisor", HostSupervisorRoot, {
            "project": Field(STRING),
            "mode": Field(one_of("coding", "zen", "swarm")),
            "agents": Field(list_of(ref("SupervisorAgentRow"))),
            "counts": Field(record(INT)),
            "approvals": Field(list_of(record(ANY))),
        }),
        "cancel": OpSpec("supervisor", HostSupervisorCancelRequest, {
            "key": Field(STRING),
            "cancelled": Field(BOOL),
        }),
        "approve": OpSpec("supervisor", HostSupervisorApproveRequest, {}),
    }),
    "workspace": OpSpec("workspace", NoFields, {}, subs={
        "get": OpSpec("workspace", HostProjectField, {
            "project": Field(STRING),
            "mode": Field(one_of("coding", "zen", "swarm")),
            "updatedAt": Field(FLOAT),
            "routerSession": Field(STRING, nullable=True),
        }),
        "set": OpSpec("workspace", HostWorkspaceSetRequest, {
            "project": Field(STRING),
            "mode": Field(one_of("coding", "zen", "swarm")),
            "updatedAt": Field(FLOAT),
            "routerSession": Field(STRING, nullable=True),
        }),
    }),
}

#: Host kinds the dispatch handles that are not fully declared yet — the
#: shrink-me frontier of the second surface.
HOST_PARTIAL: dict[str, str] = {
    "swarm": "sub-op family nesting SwarmManager views (roles/tasks/messages/"
             "works/transcript); declare against manager view builders next",
    "zen_fleet": "fleet enable/disable/state/transcript over the zen roster "
                 "sidecar and slot states",
    "session": "host-side resume with sibling-slot semantics (frames vary by "
               "slot state)",
    "terminal": "PTY lifecycle + streamed frames (start/stop/resize)",
    "approval": "host-side approval routing for supervisor/swarm pendings",
    "ask": "host-side ask routing",
}


