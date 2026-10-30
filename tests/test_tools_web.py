"""web_search / fetch: provider chain, parsing and failure messages."""

import httpx
import pytest

from zenagent.tools import web as web_module
from zenagent.tools.web import FETCH, WEB_SEARCH


async def call(tool, **args):
    return await tool.execute("call_1", args, None)


DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">
  Example <b>A</b>
</a>
<a class="result__snippet" href="#">First result snippet.</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">
  Example B
</a>
<a class="result__snippet" href="#">Second snippet.</a>
</body></html>
"""


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)


BING_HTML = """
<ol id="b_results">
<li class="b_algo"><h2><a href="https://zhihu.example/q/1">Bing <b>hit</b> one</a></h2>
<div class="b_caption"><p>Bing snippet one.</p></div></li>
<li class="b_algo"><h2><a href="https://zhihu.example/q/2">Bing hit two</a></h2>
<div class="b_caption"><p>Bing snippet two.</p></div></li>
</ol>
"""


async def test_duckduckgo_results_are_parsed(monkeypatch):
    async def fake_post(url, **kwargs):
        return httpx.Response(200, text=DDG_HTML, request=httpx.Request("POST", url))

    monkeypatch.setattr(web_module, "_http_post", fake_post)
    results = await web_module._search_duckduckgo("example", 5)
    assert [result.url for result in results] == ["https://example.com/a", "https://example.com/b"]
    assert results[0].title == "Example A"
    assert results[0].snippet == "First result snippet."


async def test_bing_results_are_parsed():
    results = web_module._parse_bing(BING_HTML, 5)
    assert [result.title for result in results] == ["Bing hit one", "Bing hit two"]
    assert results[0].url == "https://zhihu.example/q/1"
    assert results[0].snippet == "Bing snippet one."


async def test_chain_falls_through_to_duckduckgo_when_bing_is_empty(monkeypatch):
    async def empty_bing(url, **kwargs):
        return httpx.Response(200, text="<html></html>", request=httpx.Request("GET", url))

    async def ddg(url, **kwargs):
        return httpx.Response(200, text=DDG_HTML, request=httpx.Request("POST", url))

    monkeypatch.setattr(web_module, "_http_get", empty_bing)
    monkeypatch.setattr(web_module, "_http_post", ddg)
    result = await call(WEB_SEARCH, query="example")
    assert "via DuckDuckGo" in result.content[0].text


async def test_brave_is_preferred_when_a_key_exists(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")

    async def fake_get(url, **kwargs):
        payload = {
            "web": {
                "results": [
                    {"title": "Brave hit", "url": "https://brave.example/1", "description": "d"}
                ]
            }
        }
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(web_module, "_http_get", fake_get)
    result = await call(WEB_SEARCH, query="x")
    assert "via Brave" in result.content[0].text
    assert "https://brave.example/1" in result.content[0].text


async def test_search_requires_a_query():
    result = await call(WEB_SEARCH, query="  ")
    assert result.is_error and "query" in result.content[0].text


async def test_search_reports_every_provider_failure(monkeypatch):
    async def boom(url, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(web_module, "_http_get", boom)
    monkeypatch.setattr(web_module, "_http_post", boom)
    result = await call(WEB_SEARCH, query="x")
    assert result.is_error
    text = result.content[0].text
    assert "web search failed" in text
    assert "Bing" in text and "DuckDuckGo" in text, "the chain says which providers failed"


async def test_fetch_strips_html_and_reports_status(monkeypatch):
    async def fake_get(url, **kwargs):
        body = "<html><head><style>x{}</style></head><body><h1>Hi</h1><p>Body text</p></body></html>"
        return httpx.Response(
            200, text=body, headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(web_module, "_http_get", fake_get)
    result = await call(FETCH, url="https://example.com")
    text = result.content[0].text
    assert "fetched https://example.com (200" in text
    assert "Hi" in text and "Body text" in text
    assert "<h1>" not in text and "x{}" not in text, "tags and styles are stripped"


async def test_fetch_rejects_non_http_urls():
    result = await call(FETCH, url="file:///etc/passwd")
    assert result.is_error and "http" in result.content[0].text


async def test_fetch_truncates_long_pages(monkeypatch):
    async def fake_get(url, **kwargs):
        return httpx.Response(
            200, text="x" * (web_module.MAX_FETCH_CHARS + 100),
            headers={"content-type": "text/plain"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(web_module, "_http_get", fake_get)
    result = await call(FETCH, url="https://example.com/big")
    assert "truncated" in result.content[0].text
    assert result.details["truncated"] is True
