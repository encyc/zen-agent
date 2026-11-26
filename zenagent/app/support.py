"""Small helpers shared by every frontend (no CLI imports)."""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from zenagent import settings
from zenagent.prompts import PromptContext, compose_system
from zenagent.providers.catalog import (
    Model,
    ModelRefError,
    ProviderConfig,
    resolve_model_ref,
)
from zenagent.types import Usage

__all__ = [
    "api_key_for",
    "cache_hit_ratio",
    "context_occupancy",
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
    """Pick the model to run: explicit ``provider/id``, else the catalog's
    ``default: true``."""
    if model_id:
        return resolve_model_ref(catalog, model_id)
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

def resolve_task_model(
    catalog: dict[str, ProviderConfig], raw: str
) -> tuple[Model, str]:
    """Resolve a ``task`` ``model`` argument to ``(model, api_key)``.

    Requires ``provider/model-id`` — the same strict lookup ``/model`` uses,
    through :func:`resolve_model_ref`; a bare id is refused because a model id
    is not an identity across providers. Raises ``ValueError`` with a hint
    naming the missing environment variable when the model, provider or its
    API key is absent — the task tool turns that into a fail-fast error
    envelope before any child runs.
    """
    try:
        model = resolve_model_ref(catalog, raw)
    except ModelRefError as exc:
        # ValueError is the task tool's fail-fast contract.
        raise ValueError(str(exc)) from None
    key = api_key_for(catalog, model)
    if not key:
        provider = catalog.get(model.provider)
        env = provider.api_key_env if provider else "the provider's key"
        raise ValueError(
            f"model {model.id!r} needs provider {model.provider!r} api key: set {env}"
        )
    return model, key


def session_id_for(model: Model, provided: str | None = None) -> str | None:
    """OpenCode's gateway routes on a session id; other providers do not care."""
    if provided:
        return provided
    return uuid.uuid4().hex if model.provider == "opencode-go" else None


def context_occupancy(usage: Usage, api: str) -> int:
    """Tokens the next request carries, sized by the last request's usage.

    ``Usage.input`` is the prompt total on OpenAI-style and Gemini dialects
    (cached tokens included), but the uncached remainder on Anthropic, where
    the cache buckets are reported separately. The assistant's output joins
    the transcript too, so it counts as occupancy. One request only: earlier
    turns are already inside that prompt, and adding them counts the session.
    """
    prompt = usage.input
    if api == "anthropic-messages":
        prompt += usage.cache_read + usage.cache_write
    return prompt + usage.output


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


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` durably: fsync the temp, then rename.

    ``os.replace`` before the bytes are on disk loses the file on a power cut
    (audit P2): the temp file is fsynced first, and a failed write leaves the
    original untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
