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
#: Declared-as-string, server-tolerates-junk (``str(value)``/``==`` guards).
STRING_LAX = "string_lax"
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
KIND_REFS = "refs"


def list_of(item: object) -> WireType:
    return WireType(KIND_LIST, item)


def record(value: object) -> WireType:
    """A JSON object with unknown keys, all of one value type."""
    return WireType(KIND_RECORD, value)


def one_of(*values: str) -> WireType:
    """A string restricted to a closed set; TS renders the literal union."""
    return WireType(KIND_ENUM, values)


def refs(*names: str) -> WireType:
    """A union of named shapes (a transcript row, say); TS renders ``A | B``."""
    return WireType(KIND_REFS, names)


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
    """A request failed shape validation; the handler owns the wire message.

    ``field`` names the offending key so a handler can render its canonical
    refusal text (``f"{op} needs {exc.field}"``) instead of the model's
    generic one.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


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
                    raise WireError(f"{name} is required", field=name)
                values[name] = None
                continue
            values[name] = _check(name, value, field.ty)
        return cls(values)


def _check(name: str, value: Any, ty: object) -> Any:
    if isinstance(ty, WireType):
        if ty.kind == KIND_LIST:
            if not isinstance(value, list):
                raise WireError(f"{name} must be a list", field=name)
            return [_check(f"{name}[]", item, ty.arg) for item in value]
        if ty.kind == KIND_RECORD:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object", field=name)
            return {
                key: _check(f"{name}.{key}", item, ty.arg)
                for key, item in value.items()
            }
        if ty.kind == KIND_REF:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object", field=name)
            return value
        if ty.kind == KIND_ENUM:
            allowed = ty.arg if isinstance(ty.arg, tuple) else ()
            if not isinstance(value, str) or value not in allowed:
                raise WireError(f"{name} must be one of {', '.join(allowed)}", field=name)
            return value
        if ty.kind == KIND_REFS:
            if not isinstance(value, dict):
                raise WireError(f"{name} must be an object")
            return value
        raise WireError(f"{name}: unknown field type {ty.kind}")  # pragma: no cover
    if ty == BOOL_LAX:
        return value  # handler semantics (`is True`); junk tolerated, as today
    if ty == STRING_LAX:
        return value  # handler semantics (`or default` / `==`); junk tolerated
    if ty == NUMBER_LAX:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise WireError(f"{name} must be a number", field=name)
        return value
    if ty == ANY:
        return value
    if not isinstance(ty, str):
        raise WireError(f"{name}: unknown field type {ty!r}", field=name)  # pragma: no cover
    _scalars: dict[str, type | tuple[type, ...]] = {
        STRING: str,
        INT: int,
        FLOAT: (int, float),
        BOOL: bool,
    }
    scalar = _scalars.get(ty)
    if scalar is None:
        raise WireError(f"{name}: unknown field type {ty!r}", field=name)  # pragma: no cover
    if ty in (INT, FLOAT) and isinstance(value, bool):
        raise WireError(f"{name} must be a number", field=name)
    if not isinstance(value, scalar):
        raise WireError(f"{name} has the wrong type", field=name)
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
                    raise WireError(f"{name} is required", field=name)
                values[name] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise WireError(f"{name} must be a number", field=name)
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


class TextRequest(WireRequest):
    """follow_up / steer: one non-empty (handler-checked) text."""

    FIELDS: ClassVar[dict[str, Field]] = {"text": Field(STRING, required=True)}


class PromptRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "text": Field(STRING, required=True),
        "images": Field(list_of(STRING)),
    }


class ModelSwitchRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"model_id": Field(STRING, required=True)}


class ModeSwitchRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"mode": Field(STRING)}


class SetPrefRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "value": Field(BOOL_LAX),  # handler semantics: bool(value)
    }


class SessionPathRequest(WireRequest):
    """resume / delete / import: one non-empty (handler-checked) path."""

    FIELDS: ClassVar[dict[str, Field]] = {"path": Field(STRING, required=True)}


class SessionExportRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "format": Field(STRING_LAX),  # junk falls into the handler's `or "md"`
        "path": Field(STRING),
    }


class SessionPendingIdRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"pending_id": Field(INT, required=True)}


class SessionDequeueRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"pending_id": Field(INT)}


class SessionScopeRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"scope": Field(STRING_LAX)}


class _TranscriptPaging(WireRequest):
    """`limit`/`before` page the snapshot; a malformed cursor is ignored
    (paging is an optimization — the handler's _optional_count), not an
    error, so the model tolerates junk here."""

    FIELDS: ClassVar[dict[str, Field]] = {
        "limit": Field(INT),
        "before": Field(INT),
    }


class _SubagentTranscriptRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "call_id": Field(STRING, required=True),
        "after": Field(INT),
    }


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
        shape("ModelEntry", {
            "id": Field(STRING),
            "provider": Field(STRING),
            "context_window": Field(INT),
            "max_tokens": Field(INT),
            "reasoning": Field(BOOL),
            "supports_vision": Field(BOOL),
            "available": Field(BOOL),
            "reason": Field(STRING, nullable=True),
            "current": Field(BOOL),
        }, doc="One catalog model, annotated with key availability."),
        shape("SessionSummary", {
            "path": Field(STRING),
            "name": Field(STRING),
            "title": Field(STRING),
            "project": Field(STRING),
            "project_path": Field(STRING),
            "messages": Field(INT),
            "current": Field(BOOL),
            "pinned": Field(BOOL),
            "created_at": Field(FLOAT),
            "updated_at": Field(FLOAT),
            "model": Field(STRING),
            "derived_from": Field(STRING),
            "workspace": Field(one_of("coding", "zen", "swarm")),
        }, doc="One session of a listing; timestamps are unix seconds."),
        shape("CostBreakdown", {
            "input": Field(FLOAT),
            "output": Field(FLOAT),
            "cache_read": Field(FLOAT),
            "cache_write": Field(FLOAT),
            "total": Field(FLOAT),
        }, doc="One call's cost, priced per component; `total` is their sum."),
        shape("MessageUsage", {
            "input": Field(INT),
            "output": Field(INT),
            "cache_read": Field(INT),
            "cache_write": Field(INT),
            "reasoning": Field(INT, nullable=True),
            "cost": Field(ref("CostBreakdown")),
        }, doc="Token usage and cost of one model call, as message views nest it."),
        shape("ContextSlice", {
            "id": Field(STRING),
            "label": Field(STRING),
            "chars": Field(INT),
            "tokens": Field(INT),
            "pct": Field(FLOAT),
        }, doc="One component of the context window (tokens estimated)."),
        shape("ContextReport", {
            "slices": Field(list_of(ref("ContextSlice"))),
            "total_tokens": Field(INT),
            "window": Field(INT),
            "measured": Field(BOOL),
            "prompt_tokens": Field(INT),
            "free_tokens": Field(INT),
        }, doc="The context window split into slices."),
        shape("PendingMessage", {
            "id": Field(INT),
            "lane": Field(one_of("queue", "steer")),
            "text": Field(STRING),
            "images": Field(INT),
            "created_at": Field(FLOAT),
        }, doc="One user message waiting to be delivered."),
        shape("JobView", {
            "id": Field(STRING),
            "command": Field(STRING),
            "status": Field(one_of("running", "ok", "failed", "timed_out")),
            "started_at": Field(FLOAT),
            "duration_ms": Field(INT, nullable=True),
            "exit_code": Field(INT, nullable=True),
        }, doc="One background job as the sidebar renders it."),
        shape("SubagentView", {
            "call_id": Field(STRING),
            "name": Field(STRING),
            "status": Field(one_of("running", "ok", "error", "aborted", "truncated", "interrupted")),
            "duration_ms": Field(INT),
            "cost": Field(FLOAT),
            "tokens": Field(INT),
            "has_transcript": Field(BOOL),
            "transcript_truncated": Field(BOOL),
        }, doc="One task subagent: a live card or a persisted transcript's meta."),
        shape("GoalView", {
            "objective": Field(STRING),
            "status": Field(STRING),
            "check": Field(STRING, nullable=True),
            "turns_used": Field(INT),
            "max_turns": Field(INT),
            "tokens_used": Field(INT),
            "max_tokens": Field(INT),
            "wall_seconds": Field(FLOAT),
            "max_wall_seconds": Field(INT),
            "last_verdict": Field(STRING, nullable=True),
            "last_reason": Field(STRING),
            "updated_at": Field(INT),
        }, doc="Read-only goal snapshot for status bars and sidebars."),
        shape("MessageView", {
            "kind": Field(one_of("user", "assistant", "skill")),
            "text": Field(STRING),
            "skill_args": Field(STRING, nullable=True),
            "skill_body": Field(STRING, nullable=True),
            "thinking": Field(STRING, nullable=True),
            "thinking_started_at": Field(FLOAT, nullable=True),
            "thinking_seconds": Field(FLOAT, nullable=True),
            "timestamp": Field(INT),
            "steering": Field(BOOL),
            "goal": Field(BOOL),
            "id": Field(STRING),
            "model": Field(STRING),
            "provider": Field(STRING),
            "usage": Field(ref("MessageUsage"), nullable=True),
            "duration_ms": Field(INT, nullable=True),
        }, doc="A rendered user/assistant/skill turn."),
        shape("ToolCallView", {
            "kind": Field(one_of("tool")),
            "call_id": Field(STRING),
            "name": Field(STRING),
            "args_preview": Field(STRING),
            "args": Field(record(ANY)),
            "id": Field(STRING),
            "details": Field(record(ANY)),
            "status": Field(one_of("running", "ok", "error", "interrupted", "timed_out")),
            "progress": Field(STRING),
            "result_text": Field(STRING),
            "is_error": Field(BOOL),
            "duration_ms": Field(INT, nullable=True),
            "diff": Field(STRING, nullable=True),
            "started_at": Field(FLOAT, nullable=True),
        }, doc="A tool call card; history lacks duration/diff, live events fill them."),
        shape("DividerView", {
            "kind": Field(one_of("divider")),
            "tokens_before": Field(INT, nullable=True),
            "reason": Field(STRING, nullable=True),
            "id": Field(STRING),
        }, doc="The compaction divider, positioned inside the transcript stream."),
        shape("TranscriptNotice", {
            "kind": Field(one_of("divider", "info")),
            "text": Field(STRING),
            "source": Field(STRING),
        }, doc="An out-of-band line a controller wants rendered."),
        shape("CompactionRecord", {
            "type": Field(one_of("compaction")),
            "tokensBefore": Field(INT),
            "reason": Field(STRING),
            "kept": Field(INT),
        }, doc="A context compaction record: the replay boundary."),
        shape("RunStatusWire", {
            "model_id": Field(STRING),
            "thinking": Field(STRING, nullable=True),
            "running": Field(BOOL),
            "input_tokens": Field(INT),
            "output_tokens": Field(INT),
            "cache_read": Field(INT),
            "cache_write": Field(INT),
            "cache_hit_ratio": Field(FLOAT, nullable=True),
            "cost": Field(FLOAT),
            "context_used": Field(INT),
            "context_window": Field(INT),
            "follow_ups": Field(INT),
            "steering": Field(INT),
            "continuing": Field(INT),
            "turn_ms": Field(INT),
            "turn_output_tokens": Field(INT),
            "last_error": Field(STRING),
            "session_name": Field(STRING),
            "cwd": Field(STRING),
            "mode": Field(STRING),
            "git_branch": Field(STRING),
            "git_staged": Field(INT),
            "git_dirty": Field(INT),
            "git_untracked": Field(INT),
            "todos": Field(list_of(record(ANY))),
            "subagent_cost": Field(FLOAT),
            "subagent_tokens": Field(INT),
            "pending": Field(list_of(ref("PendingMessage"))),
            "jobs": Field(list_of(ref("JobView"))),
            "subagents": Field(list_of(ref("SubagentView"))),
            "active_subagent": Field(STRING, nullable=True),
            "context": Field(ref("ContextReport"), nullable=True),
            "goal": Field(ref("GoalView"), nullable=True),
        }, doc="RunStatus.to_wire(): the complete snapshot under `status`."),
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
    #: True when the handler sends ``ok: false`` as a normal outcome (model
    #: refusals, failed prompts); the generator then emits ``ok: boolean``
    #: instead of the literal-true envelope.
    ok_mutable: bool = False


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
    "models": OpSpec("models", NoFields, {"models": Field(list_of(ref("ModelEntry")))}),
    "status": OpSpec("status", NoFields, {
        "running": Field(BOOL),
        "messages": Field(INT),
        "model": Field(STRING),
        "usage": Field(ref("Usage")),
        "termination": Field(STRING, nullable=True),
        "session": Field(STRING, optional=True),
        "mode": Field(one_of("plan", "ask", "auto"), optional=True),
        "tools": Field(INT, optional=True),
        "busy": Field(BOOL, optional=True),
        "follow_ups": Field(INT, optional=True),
        "steering": Field(INT, optional=True),
        "continuing": Field(INT, optional=True),
        "cost": Field(FLOAT, optional=True),
        "thinking": Field(STRING, optional=True, nullable=True),
        "context_used": Field(INT, optional=True, nullable=True),
        "context_window": Field(INT, optional=True, nullable=True),
        "context": Field(ref("ContextReport"), optional=True, nullable=True),
        "cache_hit_ratio": Field(FLOAT, optional=True, nullable=True),
        "turn_ms": Field(INT, optional=True, nullable=True),
        "todos": Field(list_of(record(ANY)), optional=True),
        "last_error": Field(STRING, optional=True),
        "pending": Field(list_of(ref("PendingMessage")), optional=True),
        "jobs": Field(list_of(ref("JobView")), optional=True),
        "subagents": Field(list_of(ref("SubagentView")), optional=True),
        "active_subagent": Field(STRING, optional=True, nullable=True),
        "subagent_cost": Field(FLOAT, optional=True),
        "subagent_tokens": Field(INT, optional=True),
        "goal": Field(ref("GoalView"), optional=True, nullable=True),
        "status": Field(ref("RunStatusWire"), optional=True),
    }),
    "transcript": OpSpec("transcript", _TranscriptPaging, {
        "transcript": Field(list_of(refs("MessageView", "ToolCallView", "DividerView"))),
        "notices": Field(list_of(ref("TranscriptNotice"))),
        "compactions": Field(list_of(ref("CompactionRecord")), optional=True),
        "hasMoreBefore": Field(BOOL, optional=True),
        "total": Field(INT, optional=True),
    }),
    "subagent_transcript": OpSpec("subagent_transcript", _SubagentTranscriptRequest, {
        "available": Field(BOOL),
        "transcript": Field(list_of(refs("MessageView", "ToolCallView"))),
        "total": Field(INT, optional=True),
    }),
    "commands": OpSpec("commands", NoFields, {
        "commands": Field(list_of(ref("CommandSummary"))),
    }),
    "prompt": OpSpec("prompt", PromptRequest, {
        "text": Field(STRING),
        "usage": Field(ref("Usage")),
        "termination": Field(STRING, nullable=True),
        "error": Field(STRING, optional=True),
        "session": Field(STRING, optional=True),
    }, ok_mutable=True),
    "follow_up": OpSpec("follow_up", TextRequest, {}),
    "steer": OpSpec("steer", TextRequest, {}),
    "model": OpSpec("model", ModelSwitchRequest, {
        "model": Field(STRING),
        "reason": Field(STRING, nullable=True),
    }, ok_mutable=True),
    "mode": OpSpec("mode", ModeSwitchRequest, {
        "mode": Field(STRING, optional=True),
        "reason": Field(STRING, optional=True),
    }, ok_mutable=True),
    "prefs": OpSpec("prefs", NoFields, {"prefs": Field(record(BOOL))}),
    "set_pref": OpSpec("set_pref", SetPrefRequest, {"prefs": Field(record(BOOL))}),
    "session": OpSpec("session", NoFields, {}, subs={
        "current": OpSpec("session", NoFields, {"path": Field(STRING), "name": Field(STRING)}),
        "list": OpSpec("session", SessionScopeRequest, {
            "scope": Field(STRING),
            "sessions": Field(list_of(ref("SessionSummary"))),
        }),
        "new": OpSpec("session", NoFields, {"path": Field(STRING)}),
        "fork": OpSpec("session", NoFields, {"path": Field(STRING)}),
        "resume": OpSpec("session", SessionPathRequest, {"path": Field(STRING)}),
        "import": OpSpec("session", SessionPathRequest, {"path": Field(STRING)}),
        "undo": OpSpec("session", NoFields, {
            "dropped": Field(INT),
            "path": Field(STRING, nullable=True),
        }),
        "delete": OpSpec("session", SessionPathRequest, {"path": Field(STRING)}),
        "compact": OpSpec("session", NoFields, {
            "compacted": Field(BOOL),
            "path": Field(STRING, optional=True),
        }),
        "retry": OpSpec("session", NoFields, {
            "retried": Field(BOOL),
            "text": Field(STRING, optional=True),
        }),
        "export": OpSpec("session", SessionExportRequest, {"path": Field(STRING)}),
        "promote": OpSpec("session", SessionPendingIdRequest, {
            "outcome": Field(one_of("ok", "blocked_images", "not_running", "missing")),
        }),
        "dequeue": OpSpec("session", SessionDequeueRequest, {
            "dequeued": Field(BOOL),
            "text": Field(STRING, nullable=True),
        }),
        "interrupt_pending": OpSpec("session", SessionPendingIdRequest, {"sent": Field(BOOL)}),
        "clear_continuations": OpSpec("session", NoFields, {"cleared": Field(INT)}),
        "clear_pending": OpSpec("session", NoFields, {"cleared": Field(INT)}),
        "clear_todos": OpSpec("session", NoFields, {"cleared": Field(INT)}),
    }),
}


# --- declared-but-unwired kinds (the shrink-me frontier) --------------------
#: Kinds server.py dispatches that OPS does not fully declare yet. Each entry
#: carries the reason it is still a stub; the frontier test fails if a kind
#: lands in neither map, and the generator never emits stubs.

PARTIAL: dict[str, str] = {
    "approval": "decision resolution (allow|always|deny; str() coercion quirks)",
    "ask": "answers resolution",
    "command": "dynamic slash-command execution",
    "secrets": "sub-op family (set/clear/probe)",
    "skills": "sub-op family (list/toggle/install/...)",
    "limits": "sub-op family (get/set + refused-write reporting)",
    "plugins": "sub-op family (list/install/uninstall/credentials)",
    "mcp": "sub-op family (probe/add/remove)",
    "project": "sub-op family (relocate; from/to roots)",
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
            if isinstance(ty, WireType) and ty.kind == KIND_REFS:
                for member in ty.arg:
                    if member not in SHAPES:
                        problems.append(
                            f"shape {name}.{field_name}: dangling ref {member}"
                        )
    envelope_only = ("shutdown", "follow_up", "steer")
    for kind, spec in OPS.items():
        if spec.subs is None and not spec.result and kind not in envelope_only:
            problems.append(f"op {kind}: declares no result keys")
        for sub_name, sub in (spec.subs or {}).items():
            if not sub.result:
                problems.append(f"op {kind}.{sub_name}: sub-spec declares no result keys")
    return problems
