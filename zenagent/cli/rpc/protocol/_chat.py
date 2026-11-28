"""Chat-surface declarations: request models, shapes, OPS, PARTIAL, events."""

from __future__ import annotations

from typing import Any, ClassVar

from zenagent.cli.rpc.protocol._core import (
    ANY,
    BOOL,
    BOOL_LAX,
    FLOAT,
    INT,
    NUMBER_LAX,
    STRING,
    STRING_LAX,
    Field,
    NoFields,
    OpSpec,
    WireError,
    WireRequest,
    list_of,
    one_of,
    record,
    ref,
    refs,
)

# --- request models ---------------------------------------------------------

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
    """A model selection: ``provider/model-id`` (a bare id only when unique)."""

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
    FIELDS: ClassVar[dict[str, Field]] = {
        "scope": Field(STRING_LAX),
        # exclude (default) | include | only; junk falls back to the
        # handler's default — a filter is an optimization, not a contract.
        "archived": Field(STRING_LAX),
    }


class SessionArchiveRequest(WireRequest):
    """archive / restore: one saved session and the resulting state."""

    FIELDS: ClassVar[dict[str, Field]] = {
        "path": Field(STRING, required=True),
        "archived": Field(BOOL, required=True),
    }


class _TranscriptPaging(WireRequest):
    """`limit`/`before` page the snapshot; a malformed cursor is ignored
    (paging is an optimization — the handler's _optional_count), not an
    error, so the model tolerates junk here."""

    FIELDS: ClassVar[dict[str, Field]] = {
        "limit": Field(INT),
        "before": Field(INT),
    }


class SecretsSetRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "value": Field(STRING, required=True),
    }


class SecretsClearRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"name": Field(STRING, required=True)}


class SecretsTestRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "scope": Field(one_of("model", "search")),
        "target": Field(STRING, required=True),
    }


class SkillsNameRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"name": Field(STRING, required=True)}


class SkillsToggleRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "enabled": Field(BOOL, required=True),
    }


class LimitsSetRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "key": Field(STRING, required=True),
        "value": Field(ANY),  # coerce_value semantics: per-kind coercion
    }


class LimitsResetRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"key": Field(STRING, required=True)}


class PluginsIdRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"plugin": Field(STRING, required=True)}


class PluginsConfigureRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "plugin": Field(STRING, required=True),
        "values": Field(record(STRING)),
    }


class PluginsInstallRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "path": Field(STRING),
        "plugin": Field(STRING),
    }


class McpNameRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {"name": Field(STRING, required=True)}


class McpAddRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "layer": Field(one_of("global", "project")),
        "command": Field(STRING),
        "url": Field(STRING),
        "transport": Field(one_of("stdio", "http")),
        "env": Field(record(STRING)),
        "headers": Field(record(STRING)),
    }


class McpLayerRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "layer": Field(one_of("global", "project")),
    }


class McpBuiltinRequest(WireRequest):
    #: ``name``, never ``id``: the envelope's ``id`` is the request correlation
    #: id and the client overwrites it on every send (see plugin's ``plugin``
    #: field for the same reason).
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "enabled": Field(BOOL, required=True),
    }


class McpTestRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "name": Field(STRING, required=True),
        "layer": Field(STRING_LAX),
    }


class ApprovalResolutionRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "request_id": Field(STRING_LAX),  # handler str()s it
        "decision": Field(one_of("allow", "always", "deny")),
    }


class AskResolutionRequest(WireRequest):
    FIELDS: ClassVar[dict[str, Field]] = {
        "request_id": Field(STRING_LAX),  # handler str()s it
        "answers": Field(ANY),  # (string[] | null)[]; handler validates
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


# --- op registry ------------------------------------------------------------

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
    "fs_exists": OpSpec("fs_exists", FsPathRequest, {
"path": Field(STRING),
"exists": Field(BOOL),
    }),
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
"model_ref": Field(STRING, optional=True),
"usage": Field(ref("Usage")),
"termination": Field(STRING, nullable=True),
"detail": Field(STRING, optional=True),
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
"resident": Field(BOOL, optional=True),
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
"detail": Field(STRING, optional=True),
"error": Field(STRING, optional=True),
"session": Field(STRING, optional=True),
    }, ok_mutable=True),
    "follow_up": OpSpec("follow_up", TextRequest, {}),
    "steer": OpSpec("steer", TextRequest, {}),
    "model": OpSpec("model", ModelSwitchRequest, {
"model": Field(STRING),
"model_ref": Field(STRING, optional=True),
"reason": Field(STRING, nullable=True),
    }, ok_mutable=True),
    "mode": OpSpec("mode", ModeSwitchRequest, {
"mode": Field(STRING, optional=True),
"reason": Field(STRING, optional=True),
    }, ok_mutable=True),
    "prefs": OpSpec("prefs", NoFields, {"prefs": Field(record(BOOL))}),
    "set_pref": OpSpec("set_pref", SetPrefRequest, {"prefs": Field(record(BOOL))}),
    "approval": OpSpec("approval", ApprovalResolutionRequest, {}),
    "ask": OpSpec("ask", AskResolutionRequest, {}),
    "secrets": OpSpec("secrets", NoFields, {}, subs={
"list": OpSpec("secrets", NoFields, {
    "model_providers": Field(list_of(ref("SecretProviderRow"))),
    "search_providers": Field(list_of(ref("SecretProviderRow"))),
}),
"set": OpSpec("secrets", SecretsSetRequest, {}),
"clear": OpSpec("secrets", SecretsClearRequest, {}),
"test": OpSpec("secrets", SecretsTestRequest, {
    "connected": Field(BOOL),
    "message": Field(STRING),
}),
    }),
    "limits": OpSpec("limits", NoFields, {}, subs={
"get": OpSpec("limits", NoFields, {
    "path": Field(STRING),
    "project_path": Field(STRING),
    "groups": Field(list_of(STRING)),
    "knobs": Field(list_of(ref("LimitKnob"))),
    "error": Field(STRING, optional=True),
}, ok_mutable=True),
"set": OpSpec("limits", LimitsSetRequest, {}),
"reset": OpSpec("limits", LimitsResetRequest, {}),
    }),
    "skills": OpSpec("skills", NoFields, {}, subs={
"list": OpSpec("skills", NoFields, {
    "skills": Field(list_of(ref("SkillRow"))),
    "warnings": Field(list_of(STRING)),
    "notes": Field(list_of(STRING)),
}),
"rescan": OpSpec("skills", NoFields, {
    "skills": Field(list_of(ref("SkillRow"))),
    "warnings": Field(list_of(STRING)),
    "notes": Field(list_of(STRING)),
}),
"read": OpSpec("skills", SkillsNameRequest, {
    "name": Field(STRING),
    "path": Field(STRING),
    "body": Field(STRING),
    "truncated": Field(BOOL),
}),
"toggle": OpSpec("skills", SkillsToggleRequest, {
    "name": Field(STRING),
    "enabled": Field(BOOL),
    "notes": Field(list_of(STRING)),
}),
    }),
    "plugins": OpSpec("plugins", NoFields, {}, subs={
"list": OpSpec("plugins", NoFields, {
    "plugins": Field(list_of(ref("PluginRow"))),
    "catalog": Field(list_of(ref("PluginCatalogRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
"install": OpSpec("plugins", PluginsInstallRequest, {
    "plugins": Field(list_of(ref("PluginRow"))),
    "catalog": Field(list_of(ref("PluginCatalogRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
"uninstall": OpSpec("plugins", PluginsIdRequest, {
    "plugins": Field(list_of(ref("PluginRow"))),
    "catalog": Field(list_of(ref("PluginCatalogRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
"enable": OpSpec("plugins", PluginsIdRequest, {
    "plugins": Field(list_of(ref("PluginRow"))),
    "catalog": Field(list_of(ref("PluginCatalogRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
"disable": OpSpec("plugins", PluginsIdRequest, {
    "plugins": Field(list_of(ref("PluginRow"))),
    "catalog": Field(list_of(ref("PluginCatalogRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
"configure": OpSpec("plugins", PluginsConfigureRequest, {
    "plugins": Field(list_of(ref("PluginRow"))),
    "catalog": Field(list_of(ref("PluginCatalogRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
    }),
    "mcp": OpSpec("mcp", NoFields, {}, subs={
"list": OpSpec("mcp", NoFields, {
    "servers": Field(list_of(ref("McpServerRow"))),
    "builtin": Field(list_of(ref("BuiltinMcpRow"))),
    "problem": Field(STRING, nullable=True),
}),
"add": OpSpec("mcp", McpAddRequest, {
    "servers": Field(list_of(ref("McpServerRow"))),
    "problem": Field(STRING, nullable=True),
}),
"remove": OpSpec("mcp", McpLayerRequest, {
    "servers": Field(list_of(ref("McpServerRow"))),
    "problem": Field(STRING, nullable=True),
}),
"test": OpSpec("mcp", McpTestRequest, {
    "connected": Field(BOOL),
    "tools": Field(list_of(STRING)),
    "message": Field(STRING),
}),
"builtin": OpSpec("mcp", McpBuiltinRequest, {
    "servers": Field(list_of(ref("McpServerRow"))),
    "builtin": Field(list_of(ref("BuiltinMcpRow"))),
    "problem": Field(STRING, nullable=True),
    "apply_pending": Field(BOOL),
}),
    }),
    "project": OpSpec("project", NoFields, {}, subs={
"relocate": OpSpec("project", NoFields, {
    "old_root": Field(STRING),
    "new_root": Field(STRING),
    "old_key": Field(STRING),
    "new_key": Field(STRING),
    "sessions": Field(INT),
    "moved": Field(INT),
}),
    }),
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
"archive": OpSpec("session", SessionArchiveRequest, {
    "path": Field(STRING),
    "archived": Field(BOOL),
    "archived_at": Field(FLOAT),
}),
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
    "command": "dynamic slash-command result (skill submissions, command output)",
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
    # ``agent_end`` only: why the run closed and the reason behind it.
    "termination": Field(STRING, optional=True),
    "detail": Field(STRING, optional=True),
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


