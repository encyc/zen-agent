"""User configuration (``~/.pyagent/config.toml``) and context-window prudence.

Config is deliberately small: a default model and per-provider overrides for the
base URL / API-key environment variable. Custom providers are out of scope (v1
ships a packaged catalog), but pointing a known provider at a mirror is easy.

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

from pyagent.providers.catalog import ProviderConfig, model_from_mapping
from pyagent.types import AssistantMessage, Message, Usage

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
    return Path.home() / ".pyagent" / "config.toml"


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


def load_user_config(path: Path | None = None) -> UserConfig:
    """Read the config file; missing file means "use the defaults"."""
    target = Path(path) if path is not None else config_path()
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
