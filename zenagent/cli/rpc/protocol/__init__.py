"""The wire contract's single source of truth — assembled from _core/_chat/_host.

The original module grew past the LOC budget's coupling cap because it is
90% declarations; the split keeps every consumer import
(`from zenagent.cli.rpc import protocol`) working unchanged.
"""

from zenagent.cli.rpc.protocol._chat import (
    EVENT_FIELDS as EVENT_FIELDS,
)
from zenagent.cli.rpc.protocol._chat import (
    EVENT_TYPES as EVENT_TYPES,
)
from zenagent.cli.rpc.protocol._chat import (
    OPS as OPS,
)
from zenagent.cli.rpc.protocol._chat import (
    PARTIAL as PARTIAL,
)
from zenagent.cli.rpc.protocol._chat import (
    ApprovalResolutionRequest as ApprovalResolutionRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    AskResolutionRequest as AskResolutionRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    FsPathRequest as FsPathRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    GitChangesRequest as GitChangesRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    GitDiffRequest as GitDiffRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    GitLogRequest as GitLogRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    GitShowRequest as GitShowRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    LimitsResetRequest as LimitsResetRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    LimitsSetRequest as LimitsSetRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    McpAddRequest as McpAddRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    McpLayerRequest as McpLayerRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    McpNameRequest as McpNameRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    McpTestRequest as McpTestRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    MemoryCreateRequest as MemoryCreateRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    MemoryDeleteRequest as MemoryDeleteRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    MemoryUpdateRequest as MemoryUpdateRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    ModelSwitchRequest as ModelSwitchRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    ModeSwitchRequest as ModeSwitchRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    PluginsConfigureRequest as PluginsConfigureRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    PluginsIdRequest as PluginsIdRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    PluginsInstallRequest as PluginsInstallRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    PromptRequest as PromptRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SecretsClearRequest as SecretsClearRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SecretsSetRequest as SecretsSetRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SecretsTestRequest as SecretsTestRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SessionDequeueRequest as SessionDequeueRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SessionExportRequest as SessionExportRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SessionPathRequest as SessionPathRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SessionPendingIdRequest as SessionPendingIdRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SessionScopeRequest as SessionScopeRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SetPrefRequest as SetPrefRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SkillsNameRequest as SkillsNameRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    SkillsToggleRequest as SkillsToggleRequest,
)
from zenagent.cli.rpc.protocol._chat import (
    TextRequest as TextRequest,
)
from zenagent.cli.rpc.protocol._core import (
    ANY as ANY,
)
from zenagent.cli.rpc.protocol._core import (
    BOOL as BOOL,
)
from zenagent.cli.rpc.protocol._core import (
    BOOL_LAX as BOOL_LAX,
)
from zenagent.cli.rpc.protocol._core import (
    FLOAT as FLOAT,
)
from zenagent.cli.rpc.protocol._core import (
    INT as INT,
)
from zenagent.cli.rpc.protocol._core import (
    NUMBER_LAX as NUMBER_LAX,
)
from zenagent.cli.rpc.protocol._core import (
    STRING as STRING,
)
from zenagent.cli.rpc.protocol._core import (
    STRING_LAX as STRING_LAX,
)
from zenagent.cli.rpc.protocol._core import (
    Field as Field,
)
from zenagent.cli.rpc.protocol._core import (
    NoFields as NoFields,
)
from zenagent.cli.rpc.protocol._core import (
    OpSpec as OpSpec,
)
from zenagent.cli.rpc.protocol._core import (
    WireError as WireError,
)
from zenagent.cli.rpc.protocol._core import (
    WireRequest as WireRequest,
)
from zenagent.cli.rpc.protocol._core import (
    WireType as WireType,
)
from zenagent.cli.rpc.protocol._core import (
    list_of as list_of,
)
from zenagent.cli.rpc.protocol._core import (
    one_of as one_of,
)
from zenagent.cli.rpc.protocol._core import (
    record as record,
)
from zenagent.cli.rpc.protocol._core import (
    ref as ref,
)
from zenagent.cli.rpc.protocol._core import (
    refs as refs,
)
from zenagent.cli.rpc.protocol._core import (
    shape as shape,
)
from zenagent.cli.rpc.protocol._host import (
    AutomationsRequest as AutomationsRequest,
    HOST_OPS as HOST_OPS,
)
from zenagent.cli.rpc.protocol._host import (
    HOST_PARTIAL as HOST_PARTIAL,
)
from zenagent.cli.rpc.protocol._host import (
    HOST_SHAPES as HOST_SHAPES,
)
from zenagent.cli.rpc.protocol._views import (
    SHAPES as SHAPES,
)

_ENVELOPE_ONLY = ("shutdown", "follow_up", "steer", "approval", "ask")
_ENVELOPE_ONLY_SUBS = {
    ("secrets", "set"),
    ("secrets", "clear"),
    ("limits", "set"),
    ("limits", "reset"),
}


def validate_registry() -> list[str]:
    """Structural problems a test can assert empty: dangling refs, stub
    leakage (an op declaring no result keys outside the sanctioned stubs)."""
    problems: list[str] = []

    def _check_table(table: dict, shapes: dict, label: str) -> None:
        for name, entry in shapes.items():
            for field_name, field in entry["fields"].items():
                ty = field.ty
                if isinstance(ty, WireType) and ty.kind == "ref" and ty.arg not in shapes:
                    problems.append(
                        f"{label} shape {name}.{field_name}: dangling ref {ty.arg}"
                    )
                if isinstance(ty, WireType) and ty.kind == "refs":
                    members = ty.arg if isinstance(ty.arg, tuple) else ()
                    for member in members:
                        if member not in shapes:
                            problems.append(
                                f"{label} shape {name}.{field_name}: dangling ref {member}"
                            )
        for kind, spec in table.items():
            if spec.subs is None and not spec.result and kind not in _ENVELOPE_ONLY:
                problems.append(f"{label} op {kind}: declares no result keys")
            for sub_name, sub in (spec.subs or {}).items():
                if not sub.result and (kind, sub_name) not in _ENVELOPE_ONLY_SUBS:
                    problems.append(
                        f"{label} op {kind}.{sub_name}: sub-spec declares no result keys"
                    )

    _check_table(OPS, SHAPES, "")
    _check_table(HOST_OPS, SHAPES | HOST_SHAPES, "host")
    return problems
