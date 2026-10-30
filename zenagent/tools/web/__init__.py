"""Network tools: ``web_search`` (multi-provider) and ``fetch``."""

from __future__ import annotations

from zenagent.tools.web.providers import LABELS
from zenagent.tools.web.registry import (
    DEFAULT_ORDER,
    ChainResult,
    SearchChain,
    configured_order,
    provider_status,
)
from zenagent.tools.web.tools import (
    FETCH,
    MAX_FETCH_CHARS,
    MAX_RESULTS,
    WEB_SEARCH,
    format_results,
    run_search,
)

__all__ = [
    "DEFAULT_ORDER",
    "FETCH",
    "LABELS",
    "MAX_FETCH_CHARS",
    "MAX_RESULTS",
    "WEB_SEARCH",
    "ChainResult",
    "SearchChain",
    "configured_order",
    "format_results",
    "provider_status",
    "run_search",
]
