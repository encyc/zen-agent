"""Types shared by the search providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["Provider", "ProviderError", "SearchRequest", "SearchResult", "normalize_url"]

_TRACKING_PREFIXES = ("utm_", "ref_", "gclid", "fbclid", "spm", "scm")


@dataclass(frozen=True)
class SearchResult:
    """One hit, already normalised across providers."""

    title: str
    url: str
    snippet: str = ""
    provider: str = ""
    published: str = ""


@dataclass(frozen=True)
class SearchRequest:
    query: str
    limit: int = 8
    recency: str | None = None  # day | week | month | year


class ProviderError(RuntimeError):
    """A provider failed in a way worth reporting (network, HTTP, parse)."""


#: A provider is a name plus a search function; availability is decided by the registry.
Provider = tuple[str, Callable[[SearchRequest], Awaitable[list[SearchResult]]]]


def normalize_url(url: str) -> str:
    """Drop the fragment and tracking parameters so duplicates collapse."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), "")
    )
