"""Search providers: three keyed APIs and two keyless scrapers.

Ordered by expected quality where a key exists; the keyless pair keeps the
tool usable on a fresh machine. Every provider normalises into
:class:`SearchResult` so the chain and the tool stay provider-agnostic.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta
from urllib.parse import unquote, urlsplit, urlunsplit

from zenagent.tools.web import net
from zenagent.tools.web.query import (
    BRAVE_SYNTAX,
    DEEPSEEK_SYNTAX,
    NATURAL_SYNTAX,
    QuerySyntax,
    StructuredQuery,
    format_query,
    format_scraper_query,
    parse_query,
)
from zenagent.tools.web.types import ProviderError, SearchRequest, SearchResult

__all__ = [
    "LABELS",
    "PROVIDERS",
    "available_providers",
    "brave",
    "deepseek",
    "duckduckgo",
    "tavily",
]

#: Size ceiling only (omp keeps the full excerpt and clips at render); one line
#: per result keeps the tool output readable without mutating the text.
_SNIPPET_CHARS = 400
_LIMIT_MAX = 20
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL)
_WS = re.compile(r"[ \t\r\f\v]+")

#: Keyless HTML engines: no date operators, and ``inurl:`` is demoted upstream.
SCRAPER_SYNTAX = QuerySyntax(
    phrases=True, negation=True, or_groups=True, site=True, in_title=True, filetype=True
)

_RECENCY_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


def _clean(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _one_line(text: str) -> str:
    """Collapse whitespace and cap the length — never rewrite the content.

    omp's providers do exactly this (`normalizeSearchText`: ``\\s+`` -> space,
    trim) and leave display truncation to the renderer. Stripping markdown
    markers here would eat legitimate content such as a leading ``# comment``
    in a code excerpt, so we do not.
    """
    return " ".join(str(text).split())[:_SNIPPET_CHARS]


def _strip_fragment(url: str) -> str:
    """Provider anchors (``#1``, ``#:~:text=``) are noise in a result list."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.fragment:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _after_date(recency: str | None) -> str | None:
    if recency not in _RECENCY_DAYS:
        return None
    return (date.today() - timedelta(days=_RECENCY_DAYS[recency])).isoformat()


def _key(name: str) -> str | None:
    """Stored key first, environment second (same rule as model providers)."""
    from zenagent import settings

    return settings.get_api_key(name)


def _limited(request: SearchRequest) -> int:
    return max(1, min(request.limit, _LIMIT_MAX))


def _parsed(request: SearchRequest) -> StructuredQuery:
    parsed = request.parsed
    return parsed if isinstance(parsed, StructuredQuery) else parse_query(request.query)


# -- DeepSeek (in-house search, same key as the model API) -------------------


async def deepseek(request: SearchRequest) -> list[SearchResult]:
    key = _key("DEEPSEEK_API_KEY")
    if not key:
        raise ProviderError("DEEPSEEK_API_KEY is not set")
    parsed = _parsed(request)
    query = format_query(parsed, DEEPSEEK_SYNTAX)
    after = parsed.after or _after_date(request.recency)
    if after and not parsed.after:
        query = f"{query} after:{after}"
    response = await net.post(
        "https://api.deepseek.com/v1/tools/search",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        json={"query": query[:128], "count": _limited(request)},
    )
    if response.status_code >= 400:
        raise ProviderError(f"DeepSeek search HTTP {response.status_code}")
    results = []
    for item in response.json().get("results", [])[: _limited(request)]:
        url = str(item.get("url", ""))
        title = _clean(str(item.get("title", "")))
        if not url or not title:
            continue
        snippet = item.get("summary") or item.get("snippet") or ""
        results.append(
            SearchResult(
                title=title,
                url=_strip_fragment(url),
                snippet=_one_line(str(snippet)),
                provider="deepseek",
                published=str(item.get("date", "")),
            )
        )
    return results


# -- Tavily -----------------------------------------------------------------


async def tavily(request: SearchRequest) -> list[SearchResult]:
    key = _key("TAVILY_API_KEY")
    if not key:
        raise ProviderError("TAVILY_API_KEY is not set")
    parsed = _parsed(request)
    payload: dict = {
        "api_key": key,
        "query": format_query(parsed, NATURAL_SYNTAX),
        "max_results": _limited(request),
        "search_depth": "basic",
        "include_answer": False,
    }
    # Tavily takes domains as API parameters, so they never reach the query text.
    if parsed.sites:
        payload["include_domains"] = list(parsed.sites)
    if parsed.excluded_sites:
        payload["exclude_domains"] = list(parsed.excluded_sites)
    days = _RECENCY_DAYS.get(request.recency or "")
    if days:
        payload["days"] = days
        payload["topic"] = "news"
    response = await net.post("https://api.tavily.com/search", json=payload)
    if response.status_code >= 400:
        raise ProviderError(f"Tavily HTTP {response.status_code}")
    results = []
    for item in response.json().get("results", [])[: _limited(request)]:
        url = str(item.get("url", ""))
        title = _clean(str(item.get("title", "")))
        if not url or not title:
            continue
        results.append(
            SearchResult(
                title=title,
                url=_strip_fragment(url),
                snippet=_one_line(str(item.get("content", ""))),
                provider="tavily",
                published=str(item.get("published_date", "")),
            )
        )
    return results


# -- Brave ------------------------------------------------------------------


async def brave(request: SearchRequest) -> list[SearchResult]:
    key = _key("BRAVE_API_KEY")
    if not key:
        raise ProviderError("BRAVE_API_KEY is not set")
    parsed = _parsed(request)
    params = {"q": format_query(parsed, BRAVE_SYNTAX), "count": _limited(request)}
    freshness = _BRAVE_FRESHNESS.get(request.recency or "")
    if freshness:
        params["freshness"] = freshness
    response = await net.get(
        "https://api.search.brave.com/res/v1/web/search",
        params=params,
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    if response.status_code >= 400:
        raise ProviderError(f"Brave HTTP {response.status_code}")
    results = []
    for item in response.json().get("web", {}).get("results", [])[: _limited(request)]:
        url = str(item.get("url", ""))
        title = _clean(str(item.get("title", "")))
        if not url or not title:
            continue
        results.append(
            SearchResult(
                title=title,
                url=_strip_fragment(url),
                snippet=_clean(str(item.get("description", "")))[:_SNIPPET_CHARS],
                provider="brave",
                published=str(item.get("age", "")),
            )
        )
    return results


# -- Bing (keyless; reachable from networks where DDG is blocked) -------------

_BING_HEADING = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL)
_BING_ANCHOR = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_BING_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)


def parse_bing(body: str, limit: int) -> list[SearchResult]:
    """Split on ``b_algo`` blocks; the title lives in ``<h2>``, not the first link."""
    results: list[SearchResult] = []
    for block in body.split('class="b_algo')[1:]:
        if len(results) >= limit:
            break
        heading = _BING_HEADING.search(block)
        if heading is None:
            continue
        link = _BING_ANCHOR.search(heading.group(1))
        if link is None:
            continue
        title = _clean(link.group(2))
        if not title:
            continue
        paragraph = _BING_PARAGRAPH.search(block[heading.end() :])
        results.append(
            SearchResult(
                title=title,
                url=_strip_fragment(html.unescape(link.group(1))),
                snippet=_clean(paragraph.group(1) if paragraph else "")[:_SNIPPET_CHARS],
                provider="bing",
            )
        )
    return results


async def bing(request: SearchRequest) -> list[SearchResult]:
    parsed = _parsed(request)
    response = await net.get(
        "https://cn.bing.com/search",
        params={
            "q": format_scraper_query(parsed, SCRAPER_SYNTAX),
            "count": str(_limited(request)),
            "setlang": "en",
        },
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    if response.status_code >= 400:
        raise ProviderError(f"Bing HTTP {response.status_code}")
    return parse_bing(response.text, _limited(request))


# -- DuckDuckGo (keyless; blocked in some networks) ---------------------------

_DDG_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DDG_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def parse_duckduckgo(body: str, limit: int) -> list[SearchResult]:
    snippets = [_clean(match) for match in _DDG_SNIPPET.findall(body)]
    results: list[SearchResult] = []
    for index, match in enumerate(_DDG_RESULT.finditer(body)):
        if len(results) >= limit:
            break
        url = html.unescape(match.group(1))
        redirect = re.search(r"uddg=([^&]+)", url)
        if redirect:
            url = unquote(redirect.group(1))
        title = _clean(match.group(2))
        if not title or not url.startswith(("http://", "https://")):
            continue
        results.append(
            SearchResult(
                title=title,
                url=_strip_fragment(url),
                snippet=snippets[index][:_SNIPPET_CHARS] if index < len(snippets) else "",
                provider="duckduckgo",
            )
        )
    return results


async def duckduckgo(request: SearchRequest) -> list[SearchResult]:
    parsed = _parsed(request)
    response = await net.post(
        "https://html.duckduckgo.com/html/",
        data={"q": format_scraper_query(parsed, SCRAPER_SYNTAX), "kl": "wt-wt"},
    )
    if response.status_code >= 400:
        raise ProviderError(f"DuckDuckGo HTTP {response.status_code}")
    return parse_duckduckgo(response.text, _limited(request))


#: Human labels for the transcript and ``/search`` status.
LABELS = {
    "deepseek": "DeepSeek",
    "tavily": "Tavily",
    "brave": "Brave",
    "bing": "Bing",
    "duckduckgo": "DuckDuckGo",
}


PROVIDERS = {
    "deepseek": deepseek,
    "tavily": tavily,
    "brave": brave,
    "bing": bing,
    "duckduckgo": duckduckgo,
}


def available_providers() -> list[tuple[str, bool, str]]:
    """``(name, available, reason)`` for the ``/search`` status view."""
    out: list[tuple[str, bool, str]] = []
    for name in PROVIDERS:
        if name == "deepseek":
            ok, reason = bool(_key("DEEPSEEK_API_KEY")), "needs DEEPSEEK_API_KEY"
        elif name == "tavily":
            ok, reason = bool(_key("TAVILY_API_KEY")), "needs TAVILY_API_KEY"
        elif name == "brave":
            ok, reason = bool(_key("BRAVE_API_KEY")), "needs BRAVE_API_KEY"
        else:
            ok, reason = True, "keyless"
        out.append((name, ok, reason))
    return out
