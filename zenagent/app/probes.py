"""Lightweight connection probes behind the settings screen's test buttons.

A probe sends the smallest possible request and reports whether the provider
accepted it. It never runs a turn, never touches the transcript, and never
returns key material: model providers stream one token with thinking off,
search providers ask for a single result.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from zenagent import settings
from zenagent.mcp.client import McpClient, McpError
from zenagent.mcp.config import McpServerConfig, make_transport
from zenagent.providers.catalog import Model, ProviderConfig
from zenagent.providers.registry import StreamFn, safe_stream, stream_fn_for
from zenagent.providers.stream_options import StreamOptions
from zenagent.tools.web import providers as web_providers
from zenagent.tools.web.types import ProviderError, SearchRequest
from zenagent.types import UserMessage

PROVIDERS = web_providers.PROVIDERS
KEY_ENVS = web_providers.KEY_ENVS

__all__ = [
    "KEY_ENVS",
    "MAX_MESSAGE_CHARS",
    "PROBE_TIMEOUT_S",
    "PROVIDERS",
    "McpProbeResult",
    "ProbeResult",
    "probe_mcp_server",
    "probe_model_provider",
    "probe_search_provider",
]

#: A probe is a health check, not a request: it must fail fast enough that the
#: row's spinner still feels like a button.
PROBE_TIMEOUT_S = 15.0
MAX_MESSAGE_CHARS = 200
_PING = "ping"


@dataclass(frozen=True)
class ProbeResult:
    connected: bool
    message: str = ""


@dataclass(frozen=True)
class McpProbeResult:
    connected: bool
    message: str = ""
    tools: tuple[str, ...] = ()


def _short(text: object) -> str:
    return " ".join(str(text or "").split())[:MAX_MESSAGE_CHARS]


def _provider_of(catalog: dict[str, ProviderConfig], provider_id: str):
    provider = catalog.get(provider_id)
    if provider is None or not provider.models:
        return None
    return provider


async def probe_model_provider(
    catalog: dict[str, ProviderConfig],
    provider_id: str,
    *,
    stream_fn: StreamFn | None = None,
    timeout: float = PROBE_TIMEOUT_S,
) -> ProbeResult:
    """One-token completion through the provider's first model."""
    provider = _provider_of(catalog, provider_id)
    if provider is None:
        return ProbeResult(False, f"unknown provider: {provider_id}")
    key = settings.get_api_key(provider.api_key_env)
    if not key:
        return ProbeResult(False, f"{provider.api_key_env} is not set")

    model: Model = provider.models[0]
    options = StreamOptions(api_key=key, thinking="off", max_tokens=1)
    messages = [UserMessage(content=_PING, timestamp=int(time.time()))]
    fn = stream_fn or stream_fn_for(model)

    async def run() -> ProbeResult:
        async for event in safe_stream(fn, model, messages, [], options):
            if event.type == "error":
                return ProbeResult(
                    False, _short(event.partial.error_message) or "provider error"
                )
            if event.type == "done":
                return ProbeResult(True)
        return ProbeResult(False, "no response from provider")

    try:
        return await asyncio.wait_for(run(), timeout)
    except TimeoutError:
        return ProbeResult(False, f"timed out after {timeout:g}s")


async def probe_search_provider(
    provider_id: str, *, timeout: float = PROBE_TIMEOUT_S
) -> ProbeResult:
    """One ``limit=1`` search; a 200 means the key (when needed) was accepted."""
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        return ProbeResult(False, f"unknown search provider: {provider_id}")
    env = KEY_ENVS.get(provider_id)
    if env and not settings.get_api_key(env):
        return ProbeResult(False, f"{env} is not set")
    try:
        await asyncio.wait_for(
            provider(SearchRequest(query=_PING, limit=1)), timeout
        )
    except TimeoutError:
        return ProbeResult(False, f"timed out after {timeout:g}s")
    except (ProviderError, httpx.HTTPError, OSError, ValueError) as exc:
        return ProbeResult(False, _short(exc))
    return ProbeResult(True)


async def probe_mcp_server(
    config: McpServerConfig, *, timeout: float = PROBE_TIMEOUT_S
) -> McpProbeResult:
    """Start one server, list its tools, and close it again.

    The probe owns a temporary client: it never joins the runtime's tool list,
    so a management page can test a server the app is not using.
    """

    async def run() -> McpProbeResult:
        client = McpClient(config.name, make_transport(config), timeout_s=timeout)
        try:
            await client.start()
            tools = await client.list_tools()
            return McpProbeResult(True, tools=tuple(tool.name for tool in tools))
        finally:
            await client.close()

    try:
        return await asyncio.wait_for(run(), timeout)
    except TimeoutError:
        return McpProbeResult(False, f"timed out after {timeout:g}s")
    except (McpError, OSError, ValueError) as exc:
        return McpProbeResult(False, _short(exc))
