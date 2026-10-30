"""User configuration and context-window prudence.

Config is deliberately small: a default model and per-provider overrides for the
base URL / API-key environment variable. Custom providers are out of scope (v1
ships a packaged catalog), but pointing a known provider at a mirror is easy.

Two layers are read by default: the machine-wide ``~/.zenagent/config.toml``
and a committable project layer at ``<root>/.zenagent/config.toml`` (root = the
nearest directory with ``.git``). Scalars and per-provider tables merge with the
project layer winning; list-shaped keys append (``tools.allow``) or union
(``tools.deny``, so a project can never widen permissions). Relative
``extensions`` paths resolve against the directory of the file that names them.

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
from zenagent.providers.catalog import ProviderConfig, model_from_mapping
from zenagent.types import AssistantMessage, Message, Usage

__all__ = [
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
_CUSTOM_PROVIDER_KEYS = frozenset({"name", "base_url", "api_key_env", "adapter", "models"})


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


def _load_config_file(target: Path) -> UserConfig:
    """Read one config file; a missing file means "use the defaults"."""
    if not target.is_file():
        return UserConfig()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ValueError(f"cannot read config {target}: {exc}") from exc

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
        raise ValueError(
            f"config {target}: extensions must be an array of strings"
        )
    extensions: list[str] = []
    for item in extensions_raw:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = (target.parent / candidate).resolve()
        extensions.append(str(candidate))

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
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
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
    return UserConfig(
        default_model=default_model,
        provider_overrides=overrides,
        custom_providers=custom_providers,
        extensions=extensions,
        tool_allow=tool_allow,
        tool_deny=tool_deny,
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
    )


def load_user_config(path: Path | None = None, *, cwd: Path | None = None) -> UserConfig:
    """Read config, layering the project file over the global one.

    An explicit ``path`` disables layering: only that file is read, preserving
    the old behaviour for callers that choose a file themselves. Otherwise the
    global ``~/.zenagent/config.toml`` is read first and ``<root>/.zenagent/
    config.toml`` second; ``cwd`` selects the project root (default: cwd).
    """
    if path is not None:
        return _load_config_file(Path(path))
    return _merge_user_configs(
        _load_config_file(config_path()),
        _load_config_file(project.project_config_path(cwd)),
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
        return await naive_overflow_prune(messages, self.context_window, self.last_usage)
