"""Lightweight connection probes: one tiny request per provider, never a turn."""

import asyncio

import pytest

from zenagent.app import probes
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import load_catalog
from zenagent.tools.web.types import ProviderError, SearchRequest, SearchResult


def done_stream():
    async def stream(model, messages, tools, options):
        from zenagent.types import AssistantMessage, Usage

        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="stop",
            timestamp=0,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


def error_stream(message: str):
    async def stream(model, messages, tools, options):
        from zenagent.types import AssistantMessage, Usage

        partial = AssistantMessage(
            content=[],
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="error",
            timestamp=0,
            error_message=message,
        )
        yield AssistantEvent(type="start", partial=partial)
        yield AssistantEvent(type="error", partial=partial, reason="error")

    return stream


async def test_model_probe_reports_connected_when_the_stream_finishes(app_env):
    catalog = load_catalog()
    result = await probes.probe_model_provider(
        catalog, "deepseek", stream_fn=done_stream()
    )
    assert result.connected is True
    assert result.message == ""


async def test_model_probe_keeps_the_terminal_error_message(app_env):
    catalog = load_catalog()
    result = await probes.probe_model_provider(
        catalog, "deepseek", stream_fn=error_stream("HTTP 401: invalid key")
    )
    assert result.connected is False
    assert "HTTP 401" in result.message


async def test_model_probe_flags_a_missing_key(app_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from zenagent import settings

    settings.clear_cache()
    catalog = load_catalog()
    result = await probes.probe_model_provider(
        catalog, "deepseek", stream_fn=done_stream()
    )
    assert result.connected is False
    assert "DEEPSEEK_API_KEY" in result.message


async def test_model_probe_times_out_instead_of_hanging(app_env):
    async def slow(model, messages, tools, options):
        await asyncio.sleep(5)
        yield  # pragma: no cover - cancelled by the timeout

    catalog = load_catalog()
    result = await probes.probe_model_provider(
        catalog, "deepseek", stream_fn=slow, timeout=0.01
    )
    assert result.connected is False
    assert "timed out" in result.message


async def test_search_probe_asks_for_one_result(app_env, monkeypatch):
    seen = []

    async def fake(request: SearchRequest) -> list[SearchResult]:
        seen.append(request)
        return [SearchResult(title="t", url="https://example.com")]

    monkeypatch.setitem(probes.PROVIDERS, "bing", fake)
    result = await probes.probe_search_provider("bing")
    assert result.connected is True
    assert seen and seen[0].limit == 1


async def test_search_probe_reports_a_provider_error(app_env, monkeypatch):
    async def fake(request: SearchRequest) -> list[SearchResult]:
        raise ProviderError("Brave HTTP 401")

    monkeypatch.setitem(probes.PROVIDERS, "brave", fake)
    monkeypatch.setenv("BRAVE_API_KEY", "unused")
    result = await probes.probe_search_provider("brave")
    assert result.connected is False
    assert "Brave HTTP 401" in result.message


async def test_search_probe_never_calls_a_keyed_provider_without_a_key(
    app_env, monkeypatch
):
    calls = []

    async def fake(request: SearchRequest) -> list[SearchResult]:
        calls.append(request)
        return []

    monkeypatch.setitem(probes.PROVIDERS, "tavily", fake)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    from zenagent import settings

    settings.clear_cache()
    result = await probes.probe_search_provider("tavily")
    assert result.connected is False
    assert "TAVILY_API_KEY" in result.message
    assert calls == []


async def test_probe_messages_are_bounded(app_env):
    catalog = load_catalog()
    result = await probes.probe_model_provider(
        catalog, "deepseek", stream_fn=error_stream("x" * 5000)
    )
    assert result.connected is False
    assert len(result.message) <= probes.MAX_MESSAGE_CHARS


def test_probe_result_is_immutable():
    from dataclasses import FrozenInstanceError

    result = probes.ProbeResult(True, "")
    with pytest.raises(FrozenInstanceError):
        result.connected = False  # type: ignore[misc]


def test_timeout_default_is_short():
    assert 0 < probes.PROBE_TIMEOUT_S <= 30
