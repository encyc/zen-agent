"""The single network seam for the web tools (tests patch these two functions)."""

from __future__ import annotations

import os

import httpx

__all__ = ["get", "post", "timeout"]

USER_AGENT = "zenagent/0.1 (+https://github.com/encyc/zen-agent)"


def timeout() -> httpx.Timeout:
    """Per-request budget; ``ZENAGENT_SEARCH_TIMEOUT`` (seconds) overrides it."""
    try:
        seconds = float(os.environ.get("ZENAGENT_SEARCH_TIMEOUT", "20"))
    except ValueError:
        seconds = 20.0
    return httpx.Timeout(seconds, connect=min(10.0, seconds))


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout(), headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )


async def get(url: str, **kwargs) -> httpx.Response:
    async with _client() as client:
        return await client.get(url, **kwargs)


async def post(url: str, **kwargs) -> httpx.Response:
    async with _client() as client:
        return await client.post(url, **kwargs)
