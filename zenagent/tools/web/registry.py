"""Provider order, availability, short-lived cache and the search chain."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

from zenagent.tools.web.providers import LABELS, PROVIDERS, available_providers
from zenagent.tools.web.query import StructuredQuery, apply_constraints, parse_query
from zenagent.tools.web.types import (
    Provider,
    ProviderError,
    SearchRequest,
    SearchResult,
    normalize_url,
)

__all__ = [
    "DEFAULT_ORDER",
    "ChainResult",
    "SearchChain",
    "configured_order",
    "provider_status",
]

#: Keyed providers first (better results), keyless scrapers as the floor.
DEFAULT_ORDER = ("deepseek", "tavily", "brave", "bing", "duckduckgo")

_CACHE_TTL = 60.0
_CACHE_MAX = 32


def configured_order() -> list[str]:
    """``ZENAGENT_SEARCH_PROVIDERS=deepseek,bing`` overrides the order."""
    raw = os.environ.get("ZENAGENT_SEARCH_PROVIDERS", "").strip()
    if not raw:
        return list(DEFAULT_ORDER)
    names = [name.strip().lower() for name in raw.split(",") if name.strip()]
    return [name for name in names if name in PROVIDERS]


def provider_status() -> list[tuple[str, bool, str]]:
    return available_providers()


@dataclass
class ChainResult:
    """What the chain produced, including why it fell through."""

    results: list[SearchResult] = field(default_factory=list)
    provider: str = ""
    attempts: list[str] = field(default_factory=list)
    cached: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.results)

    @property
    def failure_summary(self) -> str:
        return "; ".join(self.attempts) or "no providers available"


class SearchChain:
    """Try providers in order; the first non-empty answer wins."""

    def __init__(self, providers: dict[str, Provider] | None = None) -> None:
        self.providers = dict(providers or PROVIDERS)
        self._cache: dict[tuple, tuple[float, ChainResult]] = {}

    def _cached(self, key: tuple) -> ChainResult | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored, result = entry
        if time.monotonic() - stored > _CACHE_TTL:
            self._cache.pop(key, None)
            return None
        return ChainResult(
            results=result.results,
            provider=result.provider,
            attempts=list(result.attempts),
            cached=True,
        )

    def _store(self, key: tuple, result: ChainResult) -> None:
        if len(self._cache) >= _CACHE_MAX:
            oldest = min(self._cache, key=lambda item: self._cache[item][0])
            self._cache.pop(oldest, None)
        self._cache[key] = (time.monotonic(), result)

    async def search(self, request: SearchRequest) -> ChainResult:
        order = [name for name in configured_order() if name in self.providers]
        key = (tuple(order), request.query, request.limit, request.recency)
        cached = self._cached(key)
        if cached is not None:
            return cached

        parsed = request.parsed
        if not isinstance(parsed, StructuredQuery):
            parsed = parse_query(request.query)

        result = ChainResult()
        for name in order:
            provider = self.providers[name]
            try:
                found = await provider(request)
            except (ProviderError, httpx.HTTPError, OSError, ValueError) as exc:
                result.attempts.append(f"{LABELS.get(name, name)}: {exc}")
                continue
            if found:
                kept, relaxed = apply_constraints(dedupe(found), parsed)
                for label in relaxed:
                    result.attempts.append(f"{LABELS.get(name, name)}: relaxed {label}")
                if kept:
                    result.results = kept[: request.limit]
                    result.provider = name
                    result.attempts.append(
                        f"{LABELS.get(name, name)}: {len(result.results)} results"
                    )
                    break
            result.attempts.append(f"{LABELS.get(name, name)}: no results")
        self._store(key, result)
        return result


def dedupe(results: list[SearchResult]) -> list[SearchResult]:
    """Collapse repeats across (and within) providers by normalised URL."""
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        key = normalize_url(result.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


_default_chain: SearchChain | None = None


def default_chain() -> SearchChain:
    global _default_chain
    if _default_chain is None:
        _default_chain = SearchChain()
    return _default_chain
