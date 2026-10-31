"""Small helpers shared by every frontend (no CLI imports)."""

from __future__ import annotations

import uuid
from pathlib import Path

from zenagent import settings
from zenagent.prompts import PromptContext, compose_system
from zenagent.providers.catalog import Model, ProviderConfig, find_model
from zenagent.types import Usage

__all__ = [
    "api_key_for",
    "cache_hit_ratio",
    "describe_tools",
    "resolve_model",
    "session_id_for",
    "system_prompt",
]


def describe_tools(tools: list) -> str:
    """One-line-per-tool listing shared by the TUI and REPL ``/tools`` commands.

    Reads ``agent.tools``, so MCP servers, the skills tool and extension tools
    are included exactly as the model sees them (the point of the command).
    """
    if not tools:
        return "tools (0): none registered"
    rows = [f"tools ({len(tools)}):"]
    for tool in tools:
        description = " ".join((getattr(tool, "description", "") or "").split())
        rows.append(f"  {tool.name}: {description}" if description else f"  {tool.name}")
    return "\n".join(rows)


def system_prompt(tool_names: list[str]) -> str:
    """Base prompt: identity, tool list, and the compact tool policy.

    Thin compatibility wrapper around :func:`zenagent.prompts.compose_system`;
    the text lives in ``zenagent/prompts/blocks/system/``.
    """
    context = PromptContext(cwd=Path.cwd(), tool_names=tuple(tool_names))
    return compose_system(context).text


def resolve_model(catalog: dict[str, ProviderConfig], model_id: str | None) -> Model:
    """Pick the model to run: explicit id, else the catalog's ``default: true``."""
    if model_id:
        return find_model(catalog, model_id)
    for provider in catalog.values():
        for model in provider.models:
            if model.default:
                return model
    for provider in catalog.values():
        if provider.models:
            return provider.models[0]
    raise LookupError("the model catalog is empty")


def api_key_for(catalog: dict[str, ProviderConfig], model: Model) -> str | None:
    provider = catalog.get(model.provider)
    if provider is None:
        return None
    # Settings (set inside the app) win over the environment; see zenagent.settings.
    return settings.get_api_key(provider.api_key_env)


def session_id_for(model: Model, provided: str | None = None) -> str | None:
    """OpenCode's gateway routes on a session id; other providers do not care."""
    if provided:
        return provided
    return uuid.uuid4().hex if model.provider == "opencode-go" else None


def cache_hit_ratio(usage: Usage, api: str) -> float | None:
    """Share of prompt tokens served from the provider cache, per wire dialect.

    ``Usage.input`` means different things per adapter: Anthropic reports the
    uncached input only (cache reads/writes are separate fields), while the
    OpenAI-style and Gemini adapters report a prompt total that already includes
    cached tokens. Mixing the two yields the classic half-rate bug (1000 prompt
    tokens with 750 cached must read 75%, not 43%), so the dialect decides the
    denominator. Returns ``None`` when nothing was cached (the segment hides).
    """
    if not usage.cache_read:
        return None
    if api == "anthropic-messages":
        prompt = usage.input + usage.cache_read + usage.cache_write
    else:
        prompt = usage.input
    if prompt <= 0:
        return None
    return usage.cache_read / prompt
