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
#: Per-call fetch timeout bounds (seconds); the network layer's default applies
#: when the model does not ask.
MIN_FETCH_TIMEOUT_S = 1.0
MAX_FETCH_TIMEOUT_S = 45.0
RECENCY_VALUES = ("day", "week", "month", "year")

_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL)
_WS = re.compile(r"[ \t\r\f\v]+")

#: Elements that are never readable content.
_DROP_ELEMENTS = re.compile(
    r"<(script|style|noscript|template|svg|iframe)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
#: Preferred content containers, in priority order.
_CONTENT_BLOCKS = (
    (re.compile(r"<article[^>]*>(.*?)</article>", re.DOTALL | re.IGNORECASE), "article"),
    (re.compile(r"<main[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE), "main"),
)
#: Page chrome removed even inside the chosen content container.
_CHROME = re.compile(
    r"<(nav|aside|footer|header)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
#: Block-level boundaries become line breaks so the text stays paragraph-shaped.
_BLOCK_BOUNDARY = re.compile(
    r"</?(?:p|div|section|article|main|li|ul|ol|tr|table|h[1-6]|br|pre|blockquote)\b[^>]*>",
    re.IGNORECASE,
)


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


def readable_text(body: str) -> tuple[str, str]:
    """Extract readable text from an HTML page; returns ``(text, method)``.

    Prefer an ``<article>``/``<main>`` container (a page's actual content),
    drop scripts/styles and page chrome, and turn block boundaries into line
    breaks so paragraph structure survives. ``method`` is reported in the
    result details (``article``/``main``/``body``).
    """
    cleaned = _DROP_ELEMENTS.sub(" ", body)
    content, method = cleaned, "body"
    for pattern, name in _CONTENT_BLOCKS:
        match = pattern.search(cleaned)
        if match is not None:
            content, method = match.group(1), name
            break
    content = _CHROME.sub(" ", content)
    content = _BLOCK_BOUNDARY.sub("\n", content)
    content = html.unescape(_TAG.sub(" ", content))
    lines = [" ".join(line.split()) for line in content.splitlines()]
    return "\n".join(line for line in lines if line), method


def _clamp_fetch_timeout(raw) -> float | None:
    """Validate ``timeout_s``; None means "use the network layer default"."""
    if raw is None or raw == "":
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return max(MIN_FETCH_TIMEOUT_S, min(seconds, MAX_FETCH_TIMEOUT_S))


async def _fetch(tool_call_id: str, args: dict, abort, on_update=None) -> ToolResultAgent:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return _text("url must start with http:// or https://", is_error=True)
    timeout_s = _clamp_fetch_timeout(args.get("timeout_s"))
    kwargs = {} if timeout_s is None else {"timeout": timeout_s}
    try:
        response = await net.get(url, **kwargs)
        response.raise_for_status()
    except (httpx.HTTPError, OSError, UnicodeDecodeError) as exc:
        return _text(f"fetch failed: {exc}", is_error=True)

    content_type = response.headers.get("content-type", "")
    body = response.text
    if "html" in content_type or body.lstrip().startswith("<"):
        text, method = readable_text(body)
    else:
        text, method = body, "text"
    truncated = len(text) > MAX_FETCH_CHARS
    if truncated:
        text = text[:MAX_FETCH_CHARS] + "\n… (truncated)"
    header = f"fetched {url} ({response.status_code}, {len(response.content)} bytes)"
    return _text(
        f"{header}\n\n{text}",
        url=url,
        truncated=truncated,
        timeout_s=timeout_s,
        method=method,
    )


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
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL."},
            "timeout_s": {
                "type": "number",
                "description": (
                    "Per-call fetch timeout in seconds (default: network layer; "
                    f"clamped to {MIN_FETCH_TIMEOUT_S:g}-{MAX_FETCH_TIMEOUT_S:g})."
                ),
            },
        },
        "required": ["url"],
    },
    execute=_fetch,
    approval="read",
)
