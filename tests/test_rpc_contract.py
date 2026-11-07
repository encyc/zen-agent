"""Two-sided wire contract between ``zenagent/cli/rpc.py`` and the desktop.

The Python dispatcher is the authority; ``desktop/src/rpc/types.ts`` carries a
contract-tested ``REQUEST_KINDS`` list and the same ``PROTOCOL_VERSION``. These
tests go red the moment either side drifts — the failure the 2026-11-07
architecture review called "kept in sync by discipline and comments".
"""

import asyncio
import json
import re
import time
from pathlib import Path

RPC_PATH = Path(__file__).resolve().parents[1] / "zenagent" / "cli" / "rpc.py"
TS_PATH = (
    Path(__file__).resolve().parents[1] / "desktop" / "src" / "rpc" / "types.ts"
)


def test_request_kinds_match_ts_contract_list():
    py = set(re.findall(r'kind == "([a-z_]+)"', RPC_PATH.read_text()))
    source = TS_PATH.read_text()
    match = re.search(r"export const REQUEST_KINDS = \[([^\]]*)\]", source, re.DOTALL)
    assert match, "desktop/src/rpc/types.ts lost REQUEST_KINDS"
    ts = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert {"hello"} <= py
    assert py == ts, f"drift: python-only={sorted(py - ts)} ts-only={sorted(ts - py)}"


def test_protocol_version_pinned_both_sides():
    assert "PROTOCOL_VERSION = 2" in RPC_PATH.read_text()
    assert "PROTOCOL_VERSION = 2" in TS_PATH.read_text()


def _ok_stream():
    from zenagent.events import AssistantEvent
    from zenagent.types import AssistantMessage, TextContent, Usage

    async def stream(model, messages, tools, options):
        partial = AssistantMessage(
            content=[],
            provider="p",
            model="m",
            usage=Usage(),
            stop_reason="pending",
            timestamp=int(time.time()),
        )
        yield AssistantEvent(type="start", partial=partial)
        block = TextContent(text="")
        partial.content.append(block)
        block.text += "ok"
        yield AssistantEvent(type="text_delta", partial=partial, content_index=0, delta="ok")
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return stream


async def test_hello_reports_protocol_version(runtime_factory):
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices, build_default_commands
    from zenagent.app.models import ModelManager
    from zenagent.app.sessions import SessionManager
    from zenagent.cli.rpc import PROTOCOL_VERSION, RpcServer

    runtime = await runtime_factory(stream_fn=_ok_stream())
    chat = ChatController(runtime)
    services = AppServices(
        runtime=runtime,
        chat=chat,
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )
    writes: list[str] = []
    queue: list[str] = []

    async def read_line() -> str | None:
        await asyncio.sleep(0)
        return queue.pop(0) if queue else None

    server = RpcServer(
        runtime=runtime,
        read_line=read_line,
        write_line=writes.append,
        chat=chat,
        models=services.models,
        sessions=services.sessions,
        commands=build_default_commands(services),
    )
    assert await server._handle_line('{"type":"hello","id":"h1"}')
    assert json.loads(writes[0]) == {
        "type": "result",
        "id": "h1",
        "protocol_version": PROTOCOL_VERSION,
    }
