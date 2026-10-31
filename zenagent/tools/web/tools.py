"""The model-facing tools: ``web_search`` and ``fetch``."""

from __future__ import annotations

import html
import re

import httpx

from zenagent.agent.loop import AgentTool, ToolResultAgent
from zenagent.tools.web import net
from zenagent.tools.web.providers import LABELS
from zenagent.tools.web.query import parse_query
from zenagent.tools.web.registry import default_chain
from zenagent.tools.web.types import SearchRequest
from zenagent.types import TextContent

__all__ = [
    "FETCH",
    "MAX_FETCH_CHARS",
    "MAX_RESULTS",
    "RECENCY_VALUES",
    "WEB_SEARCH",
    "format_results",
    "run_search",
]

MAX_RESULTS = 8
MAX_FETCH_CHARS = 12_000
RECENCY_VALUES = ("day", "week", "month", "year")

_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL)
_WS = re.compile(r"[ \t\r\f\v]+")


def _text(body: str, *, is_error: bool = False, **details) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=body)], details=details, is_error=is_error
    )


def format_results(result) -> str:
    if not result.ok:
        return f"web search failed — {result.failure_summary}"
    lines = [f"{len(result.results)} result(s) via {LABELS.get(result.provider, result.provider)}"]
    for index, item in enumerate(result.results, start=1):
        lines.append(f"{index}. {item.title}")
        lines.append(f"   {item.url}")
        if item.snippet:
            lines.append(f"   {item.snippet}")
        if item.published:
            lines.append(f"   published: {item.published}")
    return "\n".join(lines)


async def run_search(query: str, *, limit: int = MAX_RESULTS, recency: str | None = None):
    """Chain search with the configured providers (used by tool and /search)."""
    request = SearchRequest(
        query=query, limit=limit, recency=recency, parsed=parse_query(query)
    )
    return await default_chain().search(request)


async def _web_search(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    query = str(args.get("query", "")).strip()
    if not query:
        return _text("query is required", is_error=True)
    recency = args.get("recency")
    if recency not in (None, *RECENCY_VALUES):
        return _text(f"recency must be one of {', '.join(RECENCY_VALUES)}", is_error=True)
    try:
        limit = int(args.get("limit") or MAX_RESULTS)
    except (TypeError, ValueError):
        limit = MAX_RESULTS
    result = await run_search(query, limit=max(1, min(limit, MAX_RESULTS)), recency=recency)
    return _text(
        format_results(result),
        provider=result.provider,
        results=len(result.results),
        attempts=result.attempts,
        cached=result.cached,
        is_error=not result.ok,
    )


def strip_html(body: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", _SCRIPT_STYLE.sub(" ", body)))).strip()


async def _fetch(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return _text("url must start with http:// or https://", is_error=True)
    try:
        response = await net.get(url)
        response.raise_for_status()
    except (httpx.HTTPError, OSError, UnicodeDecodeError) as exc:
        return _text(f"fetch failed: {exc}", is_error=True)

    content_type = response.headers.get("content-type", "")
    body = response.text
    text = strip_html(body) if "html" in content_type or body.lstrip().startswith("<") else body
    truncated = len(text) > MAX_FETCH_CHARS
    if truncated:
        text = text[:MAX_FETCH_CHARS] + "\n… (truncated)"
    header = f"fetched {url} ({response.status_code}, {len(response.content)} bytes)"
    return _text(f"{header}\n\n{text}", url=url, truncated=truncated)


WEB_SEARCH = AgentTool(
    name="web_search",
    description=(
        "Search the web: returns titles, URLs and snippets. Use it when the "
        "answer depends on information outside this repository (current library "
        "APIs, release notes, error messages).\n"
        "The query accepts Google-style operators: \"exact phrase\", -exclude, "
        "site:domain, -site:domain, filetype:pdf, intitle:word, inurl:word, "
        "lang:en, after:2025-01-01, before:2026-01-01, and `a OR b`. Each engine "
        "gets only the operators it understands; constraints that would return "
        "nothing are relaxed and reported.\n"
        "Chain: DeepSeek -> Tavily -> Brave -> Bing -> DuckDuckGo, first usable "
        f"wins. Returns at most {MAX_RESULTS} results; follow up with `fetch` to "
        "read a page."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {
                "type": "integer",
                "description": f"Maximum results (default {MAX_RESULTS}).",
            },
            "recency": {
                "type": "string",
                "enum": list(RECENCY_VALUES),
                "description": "Restrict to results newer than this window.",
            },
        },
        "required": ["query"],
    },
    execute=_web_search,
    approval="read",
)

FETCH = AgentTool(
    name="fetch",
    description=(
        "Fetch a URL and return its readable text (HTML stripped). Use it after "
        "`web_search` to read a result in full."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute http(s) URL."}},
        "required": ["url"],
    },
    execute=_fetch,
    approval="read",
)
