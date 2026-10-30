"""Network tools: ``web_search`` and ``fetch``.

pi's built-in set is deliberately local (bash/edit/find/grep/ls/read/write);
``web_search`` is omp's addition, and it is the one capability a coding agent
noticeably misses -- "what does this library's API look like now" cannot be
answered from the repository alone.

The provider chain mirrors omp's shape at a much smaller scale: a keyed API
(Brave) when the environment provides one, otherwise DuckDuckGo's HTML
frontend, which needs no key. ``fetch`` turns a result into readable text so
the model can actually use it instead of guessing from a snippet.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass

import httpx

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.types import TextContent

__all__ = ["FETCH", "MAX_FETCH_CHARS", "MAX_RESULTS", "WEB_SEARCH"]

#: Cap on results returned by ``web_search``.
MAX_RESULTS = 8
#: Cap on characters returned by ``fetch``.
MAX_FETCH_CHARS = 12_000
#: Snippet width returned by ``web_search``.
_SNIPPET_CHARS = 240

USER_AGENT = "zenagent/0.1 (+https://github.com/encyc/zen-agent)"
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

_BING_RESULT = re.compile(
    r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<p[^>]*>(.*?)</p>)?',
    re.DOTALL,
)
_DDG_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DDG_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL)
_WS = re.compile(r"[ \t\r\f\v]+")


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def _clean(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _error(message: str) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=message)], details={"error": message}, is_error=True
    )


def _text(text: str, **details) -> ToolResultAgent:
    return ToolResultAgent(content=[TextContent(text=text)], details=details)


async def _http_get(url: str, **kwargs) -> httpx.Response:
    """Single seam for tests: every network call goes through here."""
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        return await client.get(url, **kwargs)


async def _http_post(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        return await client.post(url, **kwargs)


async def _search_brave(query: str, limit: int) -> list[SearchResult] | None:
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return None
    response = await _http_get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    results: list[SearchResult] = []
    for item in payload.get("web", {}).get("results", [])[:limit]:
        results.append(
            SearchResult(
                title=_clean(item.get("title", "")),
                url=item.get("url", ""),
                snippet=_clean(item.get("description", ""))[:_SNIPPET_CHARS],
            )
        )
    return results


def _parse_bing(body: str, limit: int) -> list[SearchResult]:
    """Split on ``b_algo`` blocks; the title lives in ``<h2>``, not the first link.

    Anchors before the heading are breadcrumb/cite rows, so pulling the first
    ``<a>`` in the block yields a URL as the title (seen in real runs).
    """
    results: list[SearchResult] = []
    for block in body.split('class="b_algo')[1:]:
        if len(results) >= limit:
            break
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.DOTALL)
        if heading is None:
            continue
        link = re.search(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', heading.group(1), re.DOTALL
        )
        if link is None:
            continue
        paragraph = re.search(r"<p[^>]*>(.*?)</p>", block[heading.end() :], re.DOTALL)
        title = _clean(link.group(2))
        if not title:
            continue
        results.append(
            SearchResult(
                title=title,
                url=html.unescape(link.group(1)),
                snippet=_clean(paragraph.group(1) if paragraph else "")[:_SNIPPET_CHARS],
            )
        )
    return results


async def _search_bing(query: str, limit: int) -> list[SearchResult]:
    """Bing's HTML endpoint: reachable in networks where DDG is not."""
    response = await _http_get(
        "https://cn.bing.com/search",
        params={"q": query, "count": str(limit), "setlang": "en"},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    response.raise_for_status()
    return _parse_bing(response.text, limit)


async def _search_duckduckgo(query: str, limit: int) -> list[SearchResult]:
    response = await _http_post(
        "https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"}
    )
    response.raise_for_status()
    body = response.text
    snippets = [_clean(match) for match in _DDG_SNIPPET.findall(body)]
    results: list[SearchResult] = []
    for index, match in enumerate(_DDG_RESULT.finditer(body)):
        if len(results) >= limit:
            break
        url = html.unescape(match.group(1))
        # DDG wraps links in a redirect; the target is the `uddg` parameter.
        redirect = re.search(r"uddg=([^&]+)", url)
        if redirect:
            from urllib.parse import unquote

            url = unquote(redirect.group(1))
        title = _clean(match.group(2))
        if not title or not url.startswith(("http://", "https://")):
            continue
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=snippets[index][:_SNIPPET_CHARS] if index < len(snippets) else "",
            )
        )
    return results


def _format(results: list[SearchResult], *, provider: str) -> str:
    if not results:
        return "no results"
    lines = [f"{len(results)} result(s) via {provider}"]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.title}")
        lines.append(f"   {result.url}")
        if result.snippet:
            lines.append(f"   {result.snippet}")
    return "\n".join(lines)


async def _web_search(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    query = str(args.get("query", "")).strip()
    if not query:
        return _error("query is required")
    limit = int(args.get("limit") or MAX_RESULTS)
    limit = max(1, min(limit, MAX_RESULTS))

    # Provider chain: a keyed API if the environment offers one, then the
    # no-key scrapers that are reachable from where the agent runs.
    chain: list[tuple[str, object]] = []
    if os.environ.get("BRAVE_API_KEY"):
        chain.append(("Brave", _search_brave))
    chain.extend([("Bing", _search_bing), ("DuckDuckGo", _search_duckduckgo)])

    failures: list[str] = []
    for name, provider in chain:
        try:
            results = await provider(query, limit)  # type: ignore[operator]
        except (httpx.HTTPError, ValueError) as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            continue
        if results:
            return _text(_format(results, provider=name), results=len(results), query=query)
        failures.append(f"{name}: no results")
    return _error("web search failed — " + "; ".join(failures))


async def _fetch(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return _error("url must start with http:// or https://")
    try:
        response = await _http_get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return _error(f"fetch failed: {exc}")

    content_type = response.headers.get("content-type", "")
    body = response.text
    if "html" in content_type or body.lstrip().startswith("<"):
        body = _SCRIPT_STYLE.sub(" ", body)
        text = _clean(body)
    else:
        text = body
    truncated = len(text) > MAX_FETCH_CHARS
    if truncated:
        text = text[:MAX_FETCH_CHARS] + "\n… (truncated)"
    header = f"fetched {url} ({response.status_code}, {len(response.content)} bytes)"
    return _text(f"{header}\n\n{text}", url=url, truncated=truncated)


WEB_SEARCH = AgentTool(
    name="web_search",
    description=(
        "Search the web and return titles, URLs and snippets. Use it when the "
        "answer depends on information outside the repository (current library "
        "APIs, release notes, error messages). Follow up with `fetch` to read a "
        f"page. Returns at most {MAX_RESULTS} results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {
                "type": "integer",
                "description": f"Maximum results (default {MAX_RESULTS}).",
            },
        },
        "required": ["query"],
    },
    execute=_web_search,
)

FETCH = AgentTool(
    name="fetch",
    description=(
        "Fetch a URL and return its readable text (HTML is stripped). Use it "
        "after `web_search` to read a result in full."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute http(s) URL."}},
        "required": ["url"],
    },
    execute=_fetch,
)
