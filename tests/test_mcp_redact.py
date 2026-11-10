import httpx
import pytest

from zenagent.mcp.client import HttpTransport, McpError
from zenagent.mcp.config import parse_server
from zenagent.mcp.redact import redact_text, redact_url


def test_redact_url_masks_every_query_value():
    assert (
        redact_url("https://mcp-gw.dingtalk.com/server/x?key=abc123&sig=zzz")
        == "https://mcp-gw.dingtalk.com/server/x?key=***&sig=***"
    )
    assert redact_url("https://example.com/mcp") == "https://example.com/mcp"


def test_redact_url_masks_userinfo():
    assert redact_url("https://user:pass@example.com/mcp") == "https://***@example.com/mcp"


def test_redact_text_masks_embedded_urls():
    message = (
        "Client error '404 Not Found' for url "
        "'https://gw.example.com/server/x?key=topsecret'"
    )
    assert "topsecret" not in redact_text(message)
    assert "https://gw.example.com/server/x?key=***" in redact_text(message)


async def test_http_transport_status_error_never_carries_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="denied key=topsecret")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpTransport("https://gw.example.com/s?key=topsecret", client=client)
    with pytest.raises(McpError) as caught:
        await transport.request("tools/list", None, timeout_s=1.0)
    assert "topsecret" not in str(caught.value)


def test_parse_server_is_a_public_wrapper():
    config = parse_server("s", {"command": ["echo"]})
    assert config.transport_kind == "stdio"
    with pytest.raises(ValueError):
        parse_server("s", {})
    with pytest.raises(ValueError):
        parse_server("s", {"command": ["echo"], "url": "https://x"})
