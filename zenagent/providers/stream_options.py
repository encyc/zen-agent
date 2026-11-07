"""Vendor-neutral per-call streaming options.

Lives outside any single adapter: the agent loop, the registry, every
provider adapter, and the harness all speak this one type, so it must
not be homed in a concrete dialect module.
"""

from __future__ import annotations

from dataclasses import dataclass

from zenagent.events import AbortToken

__all__ = ["StreamOptions"]


@dataclass
class StreamOptions:
    """Per-call options for one streaming completion request.

    ``thinking`` is the user-level reasoning setting ("off"/"low"/
    "medium"/"high"); the request side translates it into each dialect's
    ``thinking`` block / bare ``reasoning_effort``. ``abort`` is the
    cooperative cancellation token shared with the loop and transport.
    """

    api_key: str = ""
    thinking: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    session_id: str | None = None
    extra_headers: dict | None = None
    abort: AbortToken | None = None
