"""The wire contract's single source of truth: one registry, TS is generated.

Every request the :class:`~zenagent.cli.rpc.server.RpcServer` dispatches is
declared here — its fields with types, and the payload keys its result frame
carries beside the ``{type: "result", id, ok}`` envelope. ``desktop/src/rpc``
mirrors these declarations by generation (``scripts/gen_rpc_ts.py`` →
``wire.gen.ts``), so adding an op means touching this file, the handler, and
the frontend usage — not four hand-kept type copies.

Scope: the per-session chat surface only. ``RpcHost``'s multiplexed families
(swarm / zen / runs / supervisor / workspace) route by request-id prefix and
carry their own shapes; they get a second registry section when their TS side
migrates (plan: docs/superpowers/plans/2026-11-19-rpc-schema-codegen.md).

Division of labor with the handlers (the reason wiring cannot just replace
them): a request model validates **shape** — which fields may appear, of what
type, required or not. The handler keeps **semantics** — the worktree-scope
check, the commit regex, the exact refusal strings, the ``or``-default
quirks. Models tolerate what handlers tolerate: a field the handler accepts
and ignores stays optional; a junk-typed field raises :class:`WireError` and
the handler maps that to the same message it would have produced inline.

One deliberate tightening, invisible to real clients (the TS side never
sends these): ``git_log``'s ``limit``/``skip`` accept ``int | float | str``
(the handler's ``int(x)`` coercion surface) but not ``bool``, which
``int(True)`` used to let through as 1. Noted at the declaration.

Coverage is frozen by ``tests/test_rpc_protocol.py``: every kind literal in
server.py must be fully declared here (``OPS``) or explicitly stubbed in
``PARTIAL`` with a reason, and nothing may be declared that the dispatch does
not handle. PARTIAL shrinks family by family; the generator emits only what
``OPS`` declares, so a stub can never leak half-true types.
"""

from __future__ import annotations

from typing import Any, ClassVar, NamedTuple

# --- field type vocabulary ------------------------------------------------
#: Scalars. INT rejects bool (JSON true is not a number); FLOAT accepts both.
#: BOOL_LAX is the declared-as-boolean, server-tolerates-junk case (today's
#: ``value is True`` guards): the generator emits ``boolean`` with a note.
STRING = "string"
INT = "int"
FLOAT = "float"
BOOL = "bool"
BOOL_LAX = "bool_lax"
#: int | float | str — the handler's ``int(x)`` coercion surface; bool refused.
NUMBER_LAX = "number_lax"
ANY = "any"


class WireType(NamedTuple):
    """A composite field type: list/record/ref/enum with its item/value/name."""

    kind: str
    arg: object


#: WireType discriminators. Named constants rather than inline literals: the
#: two-sided contract test greps this package for dispatch comparisons
#: (``kind`` against a quoted word) to pin the surface; these are field-type
#: tags, not kinds, so they are named constants rather than literals.
KIND_LIST = "list"
KIND_RECORD = "record"
KIND_REF = "ref"
KIND_ENUM = "enum"


def list_of(item: object) -> WireType:
    return WireType(KIND_LIST, item)


def record(value: object) -> WireType:
    """A JSON object with unknown keys, all of one value type."""
    return WireType(KIND_RECORD, value)


def one_of(*values: str) -> WireType:
    """A string restricted to a closed set; TS renders the literal union."""
    return WireType(KIND_ENUM, values)


def ref(name: str) -> WireType:
    """A named shape from :data:`SHAPES` (a declared interface, not inline)."""
    return WireType(KIND_REF, name)


class Field(NamedTuple):
    """One wire field: its type, and how presence works.

    Request side: ``required`` fields must be present and type-correct; the
    rest may be absent or JSON-null (handlers read both as ``None`` today, so
    the model does too). Result side: ``optional`` marks keys that
    conditionally appear in the payload (absent, never null); ``nullable``
    marks keys that are always present but carry JSON null for the empty
    case — the two are different wire shapes and generate different TS.
    """

    ty: object
    required: bool = False
    optional: bool = False
    nullable: bool = False


class WireError(ValueError):
    """A request failed shape validation; the handler owns the wire message."""


class WireRequest:
    """Declarative request model: ``from_payload`` validates, handlers use it.

    Subclass and declare ``FIELDS``; the parsed values arrive as attributes.
    """

    FIELDS: ClassVar[dict[str, Field]] = {}

    def __init__(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            setattr(self, name, value)

    def __getattr__(self, name: str) -> Any:
        """Parsed field access (``req.worktree``): values are set in
        ``__init__`` from ``FIELDS``, so static analysis sees this hook.
        Undeclared names still raise AttributeError at runtime."""
        raise AttributeError(name)

    @classmethod
    def from_payload(cls, payload: dict) -> WireRequest:
        values: dict[str, Any] = {}
        for name, field in cls.FIELDS.items():
            value = payload.get(name)
            if value is None:
                if field.required:
                    raise WireError(f"{name} is required")
                values[name] = None
                continue
            values[name] = _check(name, value, field.ty)
        return cls(values)


def _check(name: str, value: Any, ty: object) -> Any:
    if isinstance(ty, WireType):
        if ty.kind == KIND_LIST:
            if not isinstance(value, list):
                raise WireError(f"{name} must be a list")
            return [_check(f"{name}[]", item, ty.arg) for item in value]
        if ty.kind == KIND_RECORD:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object")
            return {
                key: _check(f"{name}.{key}", item, ty.arg)
                for key, item in value.items()
            }
        if ty.kind == KIND_REF:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object")
            return value
        if ty.kind == KIND_ENUM:
            allowed = ty.arg if isinstance(ty.arg, tuple) else ()
            if not isinstance(value, str) or value not in allowed:
                raise WireError(f"{name} must be one of {', '.join(allowed)}")
            return value
        raise WireError(f"{name}: unknown field type {ty.kind}")  # pragma: no cover
    if ty == BOOL_LAX:
        return value  # handler semantics (`is True`); junk tolerated, as today
    if ty == NUMBER_LAX:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise WireError(f"{name} must be a number")
        return value
    if ty == ANY:
        return value
    if not isinstance(ty, str):
        raise WireError(f"{name}: unknown field type {ty!r}")  # pragma: no cover
    _scalars: dict[str, type | tuple[type, ...]] = {
        STRING: str,
        INT: int,
        FLOAT: (int, float),
        BOOL: bool,
    }
    scalar = _scalars.get(ty)
    if scalar is None:
        raise WireError(f"{name}: unknown field type {ty!r}")  # pragma: no cover
    if ty in (INT, FLOAT) and isinstance(value, bool):
        raise WireError(f"{name} must be a number")
    if not isinstance(value, scalar):
        raise WireError(f"{name} has the wrong type")
    return value


# --- request models ---------------------------------------------------------

class NoFields(WireRequest):
    """An op whose request carries no fields beyond the envelope."""

    FIELDS: ClassVar[dict[str, Field]] = {}


class GitChangesRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"worktree": Field(STRING)}


class GitLogRequest(WireRequest):
    """`limit`/`skip` accept ``int | float | str`` — the handler's ``int(x)``
    coercion surface — and refuse everything else, ``bool`` included."""

    FIELDS: ClassVar[dict[str, Field]] = {
        "limit": Field(NUMBER_LAX),
        "skip": Field(NUMBER_LAX),
    }

    @classmethod
    def from_payload(cls, payload: dict) -> WireRequest:
        values: dict[str, Any] = {}
        for name, field in cls.FIELDS.items():
            value = payload.get(name)
            if value is None:
                if field.required:
                    raise WireError(f"{name} is required")
                values[name] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise WireError(f"{name} must be a number")
            values[name] = value
        return cls(values)


class GitShowRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"commit": Field(STRING, required=True)}


class GitDiffRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "path": Field(STRING, required=True),
        "staged": Field(BOOL_LAX),
        "worktree": Field(STRING),
    }


class FsPathRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"path": Field(STRING)}


class MemoryCreateRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "title": Field(STRING, required=True),
        "body": Field(STRING, required=True),
    }


class MemoryUpdateRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "entry_id": Field(STRING, required=True),
        "title": Field(STRING, required=True),
        "body": Field(STRING, required=True),
    }


class MemoryDeleteRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"entry_id": Field(STRING, required=True)}


# --- named result shapes ---------------------------------------------------
#: Interfaces the generator emits verbatim (``ts`` renames where the
#: frontend's established name differs from the py-side one). Field order is
#: emitted order.

def shape(name: str, fields: dict[str, Field], *, ts: str | None = None, doc: str = "") -> tuple[str, dict]:
    return name, {"ts": ts or name, "doc": doc, "fields": fields}


SHAPES: dict[str, dict] = dict(
    [
        shape("Usage", {
            "input": Field(INT),
            "output": Field(INT),
            "cache_read": Field(INT),
            "cache_write": Field(INT),
            "cost": Field(FLOAT),
        }, doc="Token/cost totals for one run (server `_usage`)."),
        shape("ToolSummary", {
            "name": Field(STRING),
            "description": Field(STRING),
            "approval": Field(STRING),
        }, ts="ToolRow", doc="One entry of the `tools` catalog."),
        shape("CommandSummary", {
            "name": Field(STRING),
            "description": Field(STRING),
        }, ts="CommandEntry", doc="One built-in slash command."),
        shape("FsEntry", {
            "name": Field(STRING),
            "path": Field(STRING),
            "kind": Field(one_of("dir", "file")),
            "size": Field(INT),
            "hidden": Field(BOOL),
        }, ts="WorkspaceEntry", doc="One row of a directory listing (folders first, sorted)."),
        shape("FilePayload", {
            "path": Field(STRING),
            "kind": Field(STRING),
            "size": Field(INT, optional=True),
            "text": Field(STRING, optional=True),
            "total_lines": Field(INT, optional=True),
            "truncated": Field(BOOL, optional=True),
            "mime": Field(STRING, optional=True),
            "data_url": Field(STRING, optional=True),
        }, doc="One file's content: shape varies by `kind` (text/image/binary/tooLarge)."),
        shape("IndexEntry", {
            "path": Field(STRING),
            "kind": Field(one_of("dir", "file")),
        }, ts="FsIndexEntry", doc="One row of the composer's `@` snapshot."),
        shape("MemoryEntry", {
            "id": Field(STRING),
            "title": Field(STRING),
            "body": Field(STRING),
            "created": Field(STRING),
            "updated": Field(STRING),
            "source": Field(STRING),
        }, doc="One project-memory note (server-side dataclass, asdict)."),
        shape("GitFileChange", {
            "path": Field(STRING),
            "state": Field(one_of("staged", "unstaged", "untracked")),
            "plus": Field(INT, nullable=True),
            "minus": Field(INT, nullable=True),
        }, doc="One file's working-tree state; plus/minus are null for untracked."),
        shape("ChangesOverview", {
            "is_repo": Field(BOOL),
            "branch": Field(STRING),
            "files": Field(list_of(ref("GitFileChange"))),
            "untracked_more": Field(INT),
        }, ts="GitChangesPayload", doc="The changes list payload."),
        shape("GitCommit", {
            "hash": Field(STRING),
            "short": Field(STRING),
            "parents": Field(list_of(STRING)),
            "subject": Field(STRING),
            "author": Field(STRING),
            "timestamp": Field(INT),
            "refs": Field(list_of(STRING)),
        }, doc="One commit of the graph page."),
        shape("CommitPage", {
            "commits": Field(list_of(ref("GitCommit"))),
        }, doc="The git_log payload."),
        shape("CommitFile", {
            "path": Field(STRING),
            "additions": Field(INT),
            "removals": Field(INT),
            "diff": Field(STRING),
        }, ts="GitShowFile", doc="One file inside a commit detail."),
        shape("CommitDetail", {
            "found": Field(BOOL),
            "short": Field(STRING),
            "subject": Field(STRING),
            "files": Field(list_of(ref("CommitFile"))),
        }, ts="GitShowPayload", doc="The git_show payload; found=false for an unknown commit."),
        shape("DiffPayload", {
            "text": Field(STRING),
            "binary": Field(BOOL),
            "truncated": Field(BOOL),
            "missing": Field(BOOL),
        }, ts="GitDiffPayload", doc="One file's unified diff."),
        shape("WorktreeRecord", {
            "path": Field(STRING),
            "head": Field(STRING),
            "branch": Field(STRING),
            "timestamp": Field(INT),
            "bare": Field(BOOL),
            "detached": Field(BOOL),
            "current": Field(BOOL),
        }, ts="WorktreeEntry", doc="One worktree of the overview list."),
        shape("WorktreeStatus", {
            "branch": Field(STRING),
            "staged": Field(INT),
            "dirty": Field(INT),
            "untracked": Field(INT),
            "clean": Field(BOOL),
        }, ts="GitStatusPayload", doc="The current worktree's counts; the overview's `status` is null outside a repo."),
        shape("WorktreeOverview", {
            "root": Field(STRING),
            "is_repo": Field(BOOL),
            "is_worktree": Field(BOOL),
            "main": Field(STRING),
            "branch": Field(STRING),
            "worktrees": Field(list_of(ref("WorktreeRecord"))),
            "status": Field(ref("WorktreeStatus"), nullable=True),
        }, doc="The git_worktrees payload."),
    ]
)


# --- op registry ------------------------------------------------------------

class OpSpec(NamedTuple):
    """One wire op: request model plus the result payload's declared keys."""

    kind: str
    request: type[WireRequest]
    result: dict[str, Field]
    #: memory-style sub-operations: `op` value → its own spec. When present,
    #: the base spec's result is ignored and each sub carries its own.
    subs: dict[str, OpSpec] | None = None


OPS: dict[str, OpSpec] = {
    "hello": OpSpec("hello", NoFields, {"protocol_version": Field(INT)}),
    "shutdown": OpSpec("shutdown", NoFields, {}),
    "cancel": OpSpec("cancel", NoFields, {"cancelled": Field(BOOL)}),
    "tools": OpSpec("tools", NoFields, {"tools": Field(list_of(ref("ToolSummary")))}),
    "usage": OpSpec("usage", NoFields, {"usage": Field(ref("Usage"))}),
    "fs_list": OpSpec("fs_list", FsPathRequest, {
        "path": Field(STRING),
        "entries": Field(list_of(ref("FsEntry"))),
        "truncated": Field(BOOL),
    }),
    "fs_read": OpSpec("fs_read", FsPathRequest, {"file": Field(ref("FilePayload"))}),
    "fs_index": OpSpec("fs_index", NoFields, {
        "entries": Field(list_of(ref("IndexEntry"))),
        "truncated": Field(BOOL),
    }),
    "git_worktrees": OpSpec("git_worktrees", NoFields, {
        "worktree": Field(ref("WorktreeOverview")),
    }),
    "git_changes": OpSpec("git_changes", GitChangesRequest, {
        "changes": Field(ref("ChangesOverview")),
    }),
    "git_log": OpSpec("git_log", GitLogRequest, {"log": Field(ref("CommitPage"))}),
    "git_show": OpSpec("git_show", GitShowRequest, {"show": Field(ref("CommitDetail"))}),
    "git_diff": OpSpec("git_diff", GitDiffRequest, {"diff": Field(ref("DiffPayload"))}),
    "memory": OpSpec("memory", NoFields, {}, subs={
        "list": OpSpec("memory", NoFields, {"entries": Field(list_of(ref("MemoryEntry")))}),
        "create": OpSpec("memory", MemoryCreateRequest, {"entry": Field(ref("MemoryEntry"))}),
        "update": OpSpec("memory", MemoryUpdateRequest, {"entry": Field(ref("MemoryEntry"))}),
        "delete": OpSpec("memory", MemoryDeleteRequest, {"deleted": Field(BOOL)}),
    }),
}


# --- declared-but-unwired kinds (the shrink-me frontier) --------------------
#: Kinds server.py dispatches that OPS does not fully declare yet. Each entry
#: carries the reason it is still a stub; the frontier test fails if a kind
#: lands in neither map, and the generator never emits stubs.

PARTIAL: dict[str, str] = {
    "prompt": "images + busy flow; request {text, images?}",
    "status": "conditional keys (session/mode/... only with a runtime)",
    "steer": "queued-steer semantics",
    "follow_up": "queued-follow-up semantics",
    "models": "ModelEntry catalog (models.entries() asdict)",
    "commands": "result is trivial ({commands: [{name, description}]}) — declare next",
    "transcript": "view-model catalog (RunView/notices/compactions asdicts)",
    "subagent_transcript": "view-model catalog + paging (after/limit)",
    "model": "model_id set + result snapshot",
    "mode": "mode set + result snapshot",
    "approval": "decision resolution (allow|always|deny)",
    "ask": "answers resolution",
    "session": "sub-op family (list/open/new/fork/export/import/pin/...)",
    "command": "dynamic slash-command execution",
    "secrets": "sub-op family (set/clear/probe)",
    "skills": "sub-op family (list/toggle/install/...)",
    "prefs": "pref get/set (name, value)",
    "set_pref": "pref set (name, value)",
    "limits": "sub-op family (get/set + refused-write reporting)",
    "plugins": "sub-op family (list/install/uninstall/credentials)",
    "mcp": "sub-op family (probe/add/remove)",
    "project": "sub-op family (get/list/set/rename/forget)",
}

# --- events (flat wire truth) ----------------------------------------------
#: What `codec.serialize_event` puts on the wire: one flat object per event,
#: `type` discriminates, every other key is conditional on that type. The TS
#: side refines this into a discriminated union; the generated flat interface
#: is the wire truth it must stay compatible with.

EVENT_FIELDS: dict[str, Field] = {
    "type": Field(STRING, required=True),
    "tool_name": Field(STRING, optional=True),
    "tool_call_id": Field(STRING, optional=True),
    "is_error": Field(BOOL, optional=True),
    "assistant_event": Field(record(ANY), optional=True),
    "attempt": Field(INT, optional=True),
    "max_retries": Field(INT, optional=True),
    "delay_ms": Field(INT, optional=True),
    "model": Field(STRING, optional=True),
    "error": Field(STRING, optional=True),
    "message": Field(record(ANY), optional=True),
    "new_messages": Field(list_of(record(ANY)), optional=True),
    "args": Field(STRING, optional=True),
    "result_text": Field(STRING, optional=True),
    "details": Field(STRING, optional=True),
    "tool_stats": Field(record(ANY), optional=True),
    "partial_result": Field(STRING, optional=True),
    "duration_ms": Field(INT, optional=True),
}

#: Event type literals (AgentEvent.type), for the generator's union.
EVENT_TYPES: tuple[str, ...] = (
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "retry_attempt",
)


# --- registry integrity -----------------------------------------------------

def validate_registry() -> list[str]:
    """Structural problems a test can assert empty: dangling refs, stub
    leakage (an op declaring no result keys outside the sanctioned stubs)."""
    problems: list[str] = []
    for name, entry in SHAPES.items():
        for field_name, field in entry["fields"].items():
            ty = field.ty
            if isinstance(ty, WireType) and ty.kind == KIND_REF and ty.arg not in SHAPES:
                problems.append(f"shape {name}.{field_name}: dangling ref {ty.arg}")
    for kind, spec in OPS.items():
        if spec.subs is None and not spec.result and kind != "shutdown":
            problems.append(f"op {kind}: declares no result keys")
        for sub_name, sub in (spec.subs or {}).items():
            if not sub.result:
                problems.append(f"op {kind}.{sub_name}: sub-spec declares no result keys")
    return problems
