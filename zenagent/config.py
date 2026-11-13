"""User configuration and context-window prudence.

Config is deliberately small: a default model and per-provider overrides for the
base URL / API-key environment variable. Custom providers are out of scope (v1
ships a packaged catalog), but pointing a known provider at a mirror is easy.

Two layers are read by default: the machine-wide ``~/.zenagent/config.toml``
and a committable project layer at ``<root>/.zenagent/config.toml`` (root = the
nearest directory with ``.git``). Scalars and per-provider tables merge with the
project layer winning; list-shaped keys append (``tools.allow``) or union
(``tools.deny``, so a project can never widen permissions). Relative
``extensions`` paths (and ``safety.allow_outside`` entries) resolve against the
directory of the file that names them. ``[safety]``, ``[backup]`` and
``[task]`` are global-only: a project layer that defines any of them is
rejected, so a checked-out repository can never grant itself permissions,
switch off backups, or raise its own subagent budget and transcript retention.

:func:`naive_overflow_prune` is the automatic last resort before a request goes
out: it estimates the context level (real ``usage`` when we have it, a char/4
estimate otherwise) and drops the oldest whole turns until the request fits.
Tool calls are always dropped together with their results, so the pruned
transcript never has a dangling ``tool_call``.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import project
from zenagent.limits import Limits, parse_limits
from zenagent.providers.catalog import ProviderConfig, model_from_mapping
from zenagent.types import AssistantMessage, Message, Usage

__all__ = [
    "DEFAULT_TASK_KEEP_TRANSCRIPTS",
    "DEFAULT_TASK_MAX_COST",
    "DEFAULT_TASK_MAX_TRANSCRIPT_BYTES",
    "DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES",
    "DEFAULT_TASK_MAX_TURNS",
    "DEFAULT_TASK_TIMEOUT_S",
    "ContextGuard",
    "UserConfig",
    "apply_user_config",
    "config_path",
    "load_user_config",
    "naive_overflow_prune",
    "token_estimate",
]

#: Fraction of the context window that triggers pruning.
PRUNE_TRIGGER = 0.85
#: Fraction the pruned transcript is brought back down to.
PRUNE_TARGET = 0.70


def config_path() -> Path:
    """Default config location."""
    return Path.home() / ".zenagent" / "config.toml"


#: Provider keys accepted inside ``[providers.custom.<id>]``.
_CUSTOM_PROVIDER_KEYS = frozenset(
    {"name", "base_url", "api_key_env", "adapter", "models"}
)
#: Keys accepted inside ``[safety]``.
_SAFETY_KEYS = frozenset({"allow_outside", "allow_destructive", "allow_protected"})
#: Keys accepted inside ``[backup]``.
_BACKUP_KEYS = frozenset({"enabled", "max_file_bytes", "keep_runs"})
#: Defaults for a missing ``[backup]`` key.
_DEFAULT_BACKUP_MAX_FILE_BYTES = 2 * 1024 * 1024
_DEFAULT_BACKUP_KEEP_RUNS = 50

#: Keys accepted inside ``[task]`` (subagent budgets, global-only).
_TASK_KEYS = frozenset(
    {
        "max_turns",
        "timeout_s",
        "max_cost",
        "keep_transcripts",
        "max_transcript_bytes",
        "max_transcript_file_bytes",
    }
)
#: Default subagent turn budget: a long multi-file investigation still fits,
#: while the main loop's own ``_MAX_TURNS`` (100) keeps the parent bounded.
DEFAULT_TASK_MAX_TURNS = 300
#: Default subagent wall-clock budget, in seconds: a loose fuse against the
#: runaway no-output runs observed in session audits, not a working ceiling.
#: 0 disables the watchdog.
DEFAULT_TASK_TIMEOUT_S = 900
#: Default subagent spend cap (None = no cap beyond the session's own).
DEFAULT_TASK_MAX_COST: float | None = None
#: Default subagent transcripts retained per session.
DEFAULT_TASK_KEEP_TRANSCRIPTS = 64
#: Default total bytes of retained subagent transcripts per session.
DEFAULT_TASK_MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
#: Default per-transcript cap; a larger transcript is cut and marked.
DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES = 8 * 1024 * 1024

#: Keys accepted inside ``[zen]`` (fleet behaviour, global-only).
_ZEN_KEYS = frozenset({"max_concurrent", "batch_window_s", "report_limit", "worker_mode"})
#: Default number of zen workers running at once.
DEFAULT_ZEN_MAX_CONCURRENT = 3
#: Default report batching window: reports finishing inside it become one message.
DEFAULT_ZEN_BATCH_WINDOW_S = 0.5
#: Default cap on one worker report injected into the router conversation.
DEFAULT_ZEN_REPORT_LIMIT = 800
#: How a worker's permission mode is chosen. ``inherit`` keeps the router's
#: mode but downgrades auto to ask (the historical behaviour); the other values
#: pin it, and ``auto`` means workers may mutate without approvals.
DEFAULT_ZEN_WORKER_MODE = "inherit"
ZEN_WORKER_MODES = ("inherit", "ask", "plan", "auto")
#: Pre-rename worker-mode spellings; normalized before validation.
LEGACY_ZEN_WORKER_MODES = {"zen": "auto"}

#: Keys accepted inside ``[swarm]`` (room policy, global-only).
_SWARM_KEYS = frozenset(
    {"max_concurrent", "max_chain_depth", "max_chain_cost", "runtime_idle_s"}
)
#: Default number of swarm role runs in flight per room.
DEFAULT_SWARM_MAX_CONCURRENT = 3
#: Default cap on how deep a delegation chain may grow.
DEFAULT_SWARM_MAX_CHAIN_DEPTH = 8
#: Default spend cap for one delegation chain, in USD.
DEFAULT_SWARM_MAX_CHAIN_COST = 5.0
#: Default idle time before a (role, work item) runtime is closed; 0 disables.
DEFAULT_SWARM_RUNTIME_IDLE_S = 600.0


def _absolute_path(raw: str, target: Path) -> str:
    """Normalize one config path: ``~`` expands, relatives resolve next to the file."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (target.parent / candidate).resolve()
    return str(candidate)


def _unknown_section_keys(
    table: dict, allowed: frozenset[str], section: str, target: Path
) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(
            f"config {target}: [{section}] has unknown keys {sorted(unknown)}"
        )


def _bool_value(
    table: dict, key: str, default: bool, section: str, target: Path
) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: {section}.{key} must be a boolean"
        )
    return value


def _positive_int(
    table: dict, key: str, default: int, section: str, target: Path
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"config {target}: {section}.{key} must be a positive integer")
    return value


def _string_array(table: dict, key: str, section: str, target: Path) -> list[str]:
    value = table.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(
            f"config {target}: {section}.{key} must be an array of strings"
        )
    return [*value]


def _section_table(data: dict, section: str, target: Path) -> dict:
    """Return one optional top-level section; a missing section is empty."""
    raw = data.get(section)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: [{section}] must be a table"
        )
    return raw


def _parse_safety(table: dict, target: Path) -> tuple[list[str], bool, bool]:
    """Parse a ``[safety]`` table into ``(allow_outside, destructive, protected)``."""
    _unknown_section_keys(table, _SAFETY_KEYS, "safety", target)
    allow_outside = [
        _absolute_path(item, target)
        for item in _string_array(table, "allow_outside", "safety", target)
    ]
    return (
        allow_outside,
        _bool_value(table, "allow_destructive", False, "safety", target),
        _bool_value(table, "allow_protected", False, "safety", target),
    )


def _parse_backup(table: dict, target: Path) -> tuple[bool, int, int]:
    """Parse a ``[backup]`` table into ``(enabled, max_file_bytes, keep_runs)``."""
    _unknown_section_keys(table, _BACKUP_KEYS, "backup", target)
    return (
        _bool_value(table, "enabled", True, "backup", target),
        _positive_int(
            table, "max_file_bytes", _DEFAULT_BACKUP_MAX_FILE_BYTES, "backup", target
        ),
        _positive_int(table, "keep_runs", _DEFAULT_BACKUP_KEEP_RUNS, "backup", target),
    )


def _positive_float_or_none(
    table: dict, key: str, default: float | None, section: str, target: Path
) -> float | None:
    value = table.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"config {target}: {section}.{key} must be a positive number")
    return float(value)


def _positive_float(
    table: dict, key: str, default: float, section: str, target: Path
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"config {target}: {section}.{key} must be a positive number")
    return float(value)


def _non_negative_float(
    table: dict, key: str, default: float, section: str, target: Path
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(
            f"config {target}: {section}.{key} must be a non-negative number"
        )
    return float(value)


def _choice(
    table: dict, key: str, default: str, choices: tuple[str, ...], section: str, target: Path
) -> str:
    value = table.get(key, default)
    if value not in choices:
        raise ValueError(
            f"config {target}: {section}.{key} must be one of {list(choices)}"
        )
    return value


def _parse_task(
    table: dict, target: Path
) -> tuple[int, int, float | None, int, int, int]:
    """Parse a ``[task]`` table into the subagent budget fields."""
    _unknown_section_keys(table, _TASK_KEYS, "task", target)
    timeout_s = table.get("timeout_s", DEFAULT_TASK_TIMEOUT_S)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s < 0:
        # 0 disables the wall-clock watchdog; anything negative is a typo.
        raise ValueError(
            f"config {target}: task.timeout_s must be a non-negative integer"
        )
    values = (
        _positive_int(table, "max_turns", DEFAULT_TASK_MAX_TURNS, "task", target),
        timeout_s,
        _positive_float_or_none(
            table, "max_cost", DEFAULT_TASK_MAX_COST, "task", target
        ),
        _positive_int(
            table, "keep_transcripts", DEFAULT_TASK_KEEP_TRANSCRIPTS, "task", target
        ),
        _positive_int(
            table,
            "max_transcript_bytes",
            DEFAULT_TASK_MAX_TRANSCRIPT_BYTES,
            "task",
            target,
        ),
        _positive_int(
            table,
            "max_transcript_file_bytes",
            DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES,
            "task",
            target,
        ),
    )
    if values[5] > values[4]:
        # A total budget smaller than one transcript would make retention
        # impossible: the store would either prune the file it just wrote or
        # keep the first file that lands (transcripts then pile up silently).
        raise ValueError(
            f"config {target}: task.max_transcript_file_bytes "
            f"({values[5]}) must not exceed task.max_transcript_bytes ({values[4]})"
        )
    return values


def _parse_custom_providers(raw: object, target: Path) -> dict[str, ProviderConfig]:
    """Parse ``[providers.custom.<id>]`` tables into ProviderConfig objects."""
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: providers.custom must be a table"
        )
    providers: dict[str, ProviderConfig] = {}
    for provider_id, table in raw.items():
        if not isinstance(table, dict):
            raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
                f"config {target}: providers.custom.{provider_id} must be a table"
            )
        unknown = set(table) - _CUSTOM_PROVIDER_KEYS
        if unknown:
            raise ValueError(
                f"config {target}: providers.custom.{provider_id} has unknown keys "
                f"{sorted(unknown)} (allowed: {sorted(_CUSTOM_PROVIDER_KEYS)})"
            )
        base_url = table.get("base_url")
        api_key_env = table.get("api_key_env")
        adapter = table.get("adapter")
        for label, value in (
            ("base_url", base_url),
            ("api_key_env", api_key_env),
            ("adapter", adapter),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"config {target}: providers.custom.{provider_id}.{label} "
                    "must be a non-empty string"
                )
        models_raw = table.get("models")
        if not isinstance(models_raw, list) or not models_raw:
            raise ValueError(
                f"config {target}: providers.custom.{provider_id}.models "
                "must be a non-empty array of tables"
            )
        models = [
            model_from_mapping(provider_id, base_url, adapter, entry)
            for entry in models_raw
        ]
        providers[provider_id] = ProviderConfig(
            id=provider_id,
            name=str(table.get("name") or provider_id),
            base_url=base_url,
            api_key_env=api_key_env,
            adapter=adapter,
            models=models,
        )
    return providers


@dataclass
class UserConfig:
    """Parsed user configuration."""

    default_model: str | None = None
    #: provider id -> {"base_url": ..., "api_key_env": ...}
    provider_overrides: dict[str, dict] = field(default_factory=dict)
    #: providers defined entirely in config.toml (merged into the catalog)
    custom_providers: dict[str, ProviderConfig] = field(default_factory=dict)
    #: extension module paths to load (relative paths resolve from the config file)
    extensions: list[str] = field(default_factory=list)
    #: fnmatch patterns: tools that never need confirmation
    tool_allow: list[str] = field(default_factory=list)
    #: fnmatch patterns: tools that are always refused
    tool_deny: list[str] = field(default_factory=list)
    #: absolute paths write tools may touch outside the workspace
    safety_allow_outside: list[str] = field(default_factory=list)
    #: destructive shell commands are allowed without confirmation
    safety_allow_destructive: bool = False
    #: writes to protected paths (``~/.ssh``, ``~/.zenagent``, …) are allowed
    safety_allow_protected: bool = False
    #: snapshot writes so ``/restore`` can roll them back
    backup_enabled: bool = True
    #: writes larger than this are never snapshotted
    backup_max_file_bytes: int = _DEFAULT_BACKUP_MAX_FILE_BYTES
    #: number of backup runs kept per project
    backup_keep_runs: int = _DEFAULT_BACKUP_KEEP_RUNS
    #: ``[task]``: provider requests a subagent may make before it is cut off
    task_max_turns: int = DEFAULT_TASK_MAX_TURNS
    #: ``[task]``: wall-clock budget per subagent run, in seconds (0 = no watchdog)
    task_timeout_s: int = DEFAULT_TASK_TIMEOUT_S
    #: ``[task]``: spend cap per subagent run (None = uncapped)
    task_max_cost: float | None = DEFAULT_TASK_MAX_COST
    #: ``[task]``: subagent transcripts retained per session
    task_keep_transcripts: int = DEFAULT_TASK_KEEP_TRANSCRIPTS
    #: ``[task]``: total bytes of retained subagent transcripts per session
    task_max_transcript_bytes: int = DEFAULT_TASK_MAX_TRANSCRIPT_BYTES
    #: ``[task]``: one transcript past this is cut and marked truncated
    task_max_transcript_file_bytes: int = DEFAULT_TASK_MAX_TRANSCRIPT_FILE_BYTES
    #: ``[limits]``: prompt/loop budgets (global-only; see zenagent.limits)
    limits: Limits = field(default_factory=Limits)
    #: ``[zen]``: workers running at once (global-only)
    zen_max_concurrent: int = DEFAULT_ZEN_MAX_CONCURRENT
    #: ``[zen]``: report batching window in seconds
    zen_batch_window_s: float = DEFAULT_ZEN_BATCH_WINDOW_S
    #: ``[zen]``: one worker report is cut to this many characters
    zen_report_limit: int = DEFAULT_ZEN_REPORT_LIMIT
    #: ``[zen]``: worker permission mode (inherit|ask|plan|auto)
    zen_worker_mode: str = DEFAULT_ZEN_WORKER_MODE
    #: ``[swarm]``: role runs in flight per room (global-only)
    swarm_max_concurrent: int = DEFAULT_SWARM_MAX_CONCURRENT
    #: ``[swarm]``: delegation chain depth cap
    swarm_max_chain_depth: int = DEFAULT_SWARM_MAX_CHAIN_DEPTH
    #: ``[swarm]``: delegation chain spend cap in USD
    swarm_max_chain_cost: float = DEFAULT_SWARM_MAX_CHAIN_COST
    #: ``[swarm]``: idle seconds before a run unit's runtime is reclaimed
    swarm_runtime_idle_s: float = DEFAULT_SWARM_RUNTIME_IDLE_S


def _load_config_file(target: Path, *, project_layer: bool = False) -> UserConfig:
    """Read one config file; a missing file means "use the defaults".

    ``project_layer=True`` marks the committable ``<root>/.zenagent/config.toml``
    layer: it may not set the global-only ``[safety]``/``[backup]``/``[task]``/
    ``[limits]``/``[zen]``/``[swarm]`` sections, so a repository can never grant
    itself permissions, raise its own subagent budgets, resize the prompt
    budgets of the machine it was cloned onto, or widen multi-agent
    concurrency.
    """
    if not target.is_file():
        return UserConfig()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ValueError(f"cannot read config {target}: {exc}") from exc

    global_only = [
        name
        for name in ("safety", "backup", "task", "limits", "zen", "swarm")
        if name in data
    ]
    if project_layer and global_only:
        listed = ", ".join(f"[{name}]" for name in global_only)
        raise ValueError(
            f"config {target}: {listed} are global-only; move them to {config_path()}"
        )

    default_model = data.get("default_model")
    if default_model is not None and not isinstance(default_model, str):
        raise ValueError(f"config {target}: default_model must be a string")

    overrides: dict[str, dict] = {}
    custom_providers: dict[str, ProviderConfig] = {}
    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: [providers] must be a table"
        )
    extensions_raw = data.get("extensions") or []
    if not isinstance(extensions_raw, list) or not all(
        isinstance(item, str) for item in extensions_raw
    ):
        raise ValueError(f"config {target}: extensions must be an array of strings")
    extensions = [_absolute_path(item, target) for item in extensions_raw]

    tools_table = data.get("tools") or {}
    if not isinstance(tools_table, dict):
        raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
            f"config {target}: [tools] must be a table"
        )
    unknown_tool_keys = set(tools_table) - {"allow", "deny"}
    if unknown_tool_keys:
        raise ValueError(
            f"config {target}: [tools] has unknown keys {sorted(unknown_tool_keys)}"
        )
    tool_allow: list[str] = []
    tool_deny: list[str] = []
    for label, destination in (("allow", tool_allow), ("deny", tool_deny)):
        value = tools_table.get(label) or []
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(
                f"config {target}: tools.{label} must be an array of strings"
            )
        destination.extend(value)

    if "custom" in providers:
        custom_providers = _parse_custom_providers(providers["custom"], target)
    for provider_id, values in providers.items():
        if provider_id == "custom":
            continue  # reserved for full provider definitions
        if not isinstance(values, dict):
            raise ValueError(  # noqa: TRY004 - config errors are ValueError by contract
                f"config {target}: providers.{provider_id} must be a table"
            )
        allowed = {"base_url", "api_key_env"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                f"config {target}: providers.{provider_id} has unknown keys "
                f"{sorted(unknown)} (allowed: {sorted(allowed)})"
            )
        overrides[provider_id] = {
            key: str(value) for key, value in values.items() if value is not None
        }

    safety_allow_outside, safety_allow_destructive, safety_allow_protected = (
        _parse_safety(_section_table(data, "safety", target), target)
    )
    backup_enabled, backup_max_file_bytes, backup_keep_runs = _parse_backup(
        _section_table(data, "backup", target), target
    )
    (
        task_max_turns,
        task_timeout_s,
        task_max_cost,
        task_keep_transcripts,
        task_max_transcript_bytes,
        task_max_transcript_file_bytes,
    ) = _parse_task(_section_table(data, "task", target), target)
    limits = parse_limits(data.get("limits"), target)
    zen_table = _section_table(data, "zen", target)
    _unknown_section_keys(zen_table, _ZEN_KEYS, "zen", target)
    zen_max_concurrent = _positive_int(
        zen_table, "max_concurrent", DEFAULT_ZEN_MAX_CONCURRENT, "zen", target
    )
    zen_batch_window_s = _non_negative_float(
        zen_table, "batch_window_s", DEFAULT_ZEN_BATCH_WINDOW_S, "zen", target
    )
    zen_report_limit = _positive_int(
        zen_table, "report_limit", DEFAULT_ZEN_REPORT_LIMIT, "zen", target
    )
    raw_worker_mode = zen_table.get("worker_mode")
    if isinstance(raw_worker_mode, str):
        zen_table["worker_mode"] = LEGACY_ZEN_WORKER_MODES.get(
            raw_worker_mode, raw_worker_mode
        )
    zen_worker_mode = _choice(
        zen_table,
        "worker_mode",
        DEFAULT_ZEN_WORKER_MODE,
        ZEN_WORKER_MODES,
        "zen",
        target,
    )
    swarm_table = _section_table(data, "swarm", target)
    _unknown_section_keys(swarm_table, _SWARM_KEYS, "swarm", target)
    swarm_max_concurrent = _positive_int(
        swarm_table,
        "max_concurrent",
        DEFAULT_SWARM_MAX_CONCURRENT,
        "swarm",
        target,
    )
    swarm_max_chain_depth = _positive_int(
        swarm_table,
        "max_chain_depth",
        DEFAULT_SWARM_MAX_CHAIN_DEPTH,
        "swarm",
        target,
    )
    swarm_max_chain_cost = _positive_float(
        swarm_table,
        "max_chain_cost",
        DEFAULT_SWARM_MAX_CHAIN_COST,
        "swarm",
        target,
    )
    swarm_runtime_idle_s = _non_negative_float(
        swarm_table,
        "runtime_idle_s",
        DEFAULT_SWARM_RUNTIME_IDLE_S,
        "swarm",
        target,
    )
    return UserConfig(
        default_model=default_model,
        provider_overrides=overrides,
        custom_providers=custom_providers,
        extensions=extensions,
        tool_allow=tool_allow,
        tool_deny=tool_deny,
        safety_allow_outside=safety_allow_outside,
        safety_allow_destructive=safety_allow_destructive,
        safety_allow_protected=safety_allow_protected,
        backup_enabled=backup_enabled,
        backup_max_file_bytes=backup_max_file_bytes,
        backup_keep_runs=backup_keep_runs,
        task_max_turns=task_max_turns,
        task_timeout_s=task_timeout_s,
        task_max_cost=task_max_cost,
        task_keep_transcripts=task_keep_transcripts,
        task_max_transcript_bytes=task_max_transcript_bytes,
        task_max_transcript_file_bytes=task_max_transcript_file_bytes,
        limits=limits,
        zen_max_concurrent=zen_max_concurrent,
        zen_batch_window_s=zen_batch_window_s,
        zen_report_limit=zen_report_limit,
        zen_worker_mode=zen_worker_mode,
        swarm_max_concurrent=swarm_max_concurrent,
        swarm_max_chain_depth=swarm_max_chain_depth,
        swarm_max_chain_cost=swarm_max_chain_cost,
        swarm_runtime_idle_s=swarm_runtime_idle_s,
    )


def _dedupe(values: list[str]) -> list[str]:
    """Stable de-duplication (first occurrence wins)."""
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            merged.append(value)
    return merged


def _merge_user_configs(base: UserConfig, override: UserConfig) -> UserConfig:
    """Layer ``override`` (project config) on top of ``base`` (global config)."""
    provider_overrides = dict(base.provider_overrides)
    provider_overrides.update(override.provider_overrides)
    custom_providers = dict(base.custom_providers)
    custom_providers.update(override.custom_providers)
    return UserConfig(
        default_model=(
            override.default_model
            if override.default_model is not None
            else base.default_model
        ),
        provider_overrides=provider_overrides,
        custom_providers=custom_providers,
        extensions=[*base.extensions, *override.extensions],
        tool_allow=_dedupe([*base.tool_allow, *override.tool_allow]),
        tool_deny=_dedupe([*base.tool_deny, *override.tool_deny]),
        # global-only sections always come from the global layer
        safety_allow_outside=list(base.safety_allow_outside),
        safety_allow_destructive=base.safety_allow_destructive,
        safety_allow_protected=base.safety_allow_protected,
        backup_enabled=base.backup_enabled,
        backup_max_file_bytes=base.backup_max_file_bytes,
        backup_keep_runs=base.backup_keep_runs,
        task_max_turns=base.task_max_turns,
        task_timeout_s=base.task_timeout_s,
        task_max_cost=base.task_max_cost,
        task_keep_transcripts=base.task_keep_transcripts,
        task_max_transcript_bytes=base.task_max_transcript_bytes,
        task_max_transcript_file_bytes=base.task_max_transcript_file_bytes,
        limits=base.limits,
        zen_max_concurrent=base.zen_max_concurrent,
        zen_batch_window_s=base.zen_batch_window_s,
        zen_report_limit=base.zen_report_limit,
        zen_worker_mode=base.zen_worker_mode,
        swarm_max_concurrent=base.swarm_max_concurrent,
        swarm_max_chain_depth=base.swarm_max_chain_depth,
        swarm_max_chain_cost=base.swarm_max_chain_cost,
        swarm_runtime_idle_s=base.swarm_runtime_idle_s,
    )


def load_user_config(
    path: Path | None = None, *, cwd: Path | None = None
) -> UserConfig:
    """Read config, layering the project file over the global one.

    An explicit ``path`` disables layering: only that file is read, preserving
    the old behaviour for callers that choose a file themselves (it counts as
    the global layer, so ``[safety]``/``[backup]`` are allowed there). Otherwise
    the global ``~/.zenagent/config.toml`` is read first and ``<root>/.zenagent/
    config.toml`` second; ``cwd`` selects the project root (default: cwd).
    """
    if path is not None:
        return _load_config_file(Path(path))
    global_config = config_path()
    project_config = project.project_config_path(cwd)
    if project_config.resolve() == global_config.resolve():
        # Run from $HOME (or a dotfiles repo rooted there): the "project" file is
        # the global file itself, so parse it once — as the global layer.
        return _load_config_file(global_config)
    return _merge_user_configs(
        _load_config_file(global_config),
        _load_config_file(project_config, project_layer=True),
    )


def apply_user_config(
    catalog: dict[str, ProviderConfig],
    config: UserConfig,
    warnings: list[str] | None = None,
) -> dict[str, ProviderConfig]:
    """Return the catalog with the user's overrides and custom providers applied."""
    for provider_id, override in config.provider_overrides.items():
        provider = catalog.get(provider_id)
        if provider is None:
            continue  # unknown provider: ignore rather than fail the run
        if "api_key_env" in override:
            provider.api_key_env = override["api_key_env"]
        if "base_url" in override:
            for model in provider.models:
                if model.base_url == provider.base_url:
                    model.base_url = override["base_url"]
            provider.base_url = override["base_url"]
    for provider_id, provider in config.custom_providers.items():
        if provider_id in catalog and warnings is not None:
            warnings.append(f"config overrides packaged provider {provider_id!r}")
        catalog[provider_id] = provider
    return catalog


# ---------------------------------------------------------------------------
# Context-window prudence
# ---------------------------------------------------------------------------


def _estimate_chars(messages: list[Message]) -> int:
    """Rough token estimate from serialized size (4 characters ≈ 1 token)."""
    size = 0
    for message in messages:
        size += len(json.dumps(message.to_json(), ensure_ascii=False))
    return size // 4


def token_estimate(messages: list[Message], last_usage: Usage | None = None) -> int:
    """Best available estimate of how full the context is.

    A real ``usage`` from the provider beats any estimate, so prefer it when the
    caller has one; otherwise fall back to the char/4 heuristic.
    """
    if last_usage is not None:
        return last_usage.input + last_usage.cache_read
    return _estimate_chars(messages)


def _split_turns(messages: list[Message]) -> tuple[list[Message], list[list[Message]]]:
    """Split into a leading system block and whole user-initiated turns."""
    leading: list[Message] = []
    index = 0
    while index < len(messages) and type(messages[index]).__name__ == "SystemMessage":
        leading.append(messages[index])
        index += 1
    turns: list[list[Message]] = []
    for message in messages[index:]:
        from_user = type(message).__name__ == "UserMessage"
        if from_user or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return leading, turns


async def naive_overflow_prune(
    messages: list[Message],
    context_window: int,
    last_usage: Usage | None = None,
) -> list[Message]:
    """Drop the oldest whole turns when the request would overflow the window.

    Never drops the leading system message or the newest turn; a turn that
    requested tools is dropped together with its tool results, so no
    ``tool_call`` is left without a result.
    """
    if context_window <= 0 or not messages:
        return messages

    used = token_estimate(messages, last_usage)
    if used <= context_window * PRUNE_TRIGGER:
        return messages

    target = context_window * PRUNE_TARGET
    leading, turns = _split_turns(messages)
    remaining = used
    while len(turns) > 1 and remaining > target:
        dropped = turns.pop(0)
        remaining -= _estimate_chars(dropped)

    pruned = [*leading, *(message for turn in turns for message in turn)]
    return pruned if len(pruned) < len(messages) else messages


@dataclass
class ContextGuard:
    """``transform_context`` hook plus the usage tracker that feeds it."""

    context_window: int
    last_usage: Usage | None = None

    def on_event(self, event) -> None:
        """Remember the last real usage (subscribe this to Agent events)."""
        if event.type == "message_end" and isinstance(event.message, AssistantMessage):
            self.last_usage = event.message.usage

    async def transform(self, messages: list[Message]) -> list[Message]:
        return await naive_overflow_prune(
            messages, self.context_window, self.last_usage
        )
