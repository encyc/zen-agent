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


def _rpc_source() -> str:
    """The whole wire package's source: contract greps span codec/server/ops."""
    pkg = Path(__file__).resolve().parents[1] / "zenagent" / "cli" / "rpc"
    return "\n".join(sorted(p.read_text() for p in pkg.glob("*.py")))


RPC_PATH = _rpc_source()  # a string now, not a path — same grep surface
TS_PATH = (
    Path(__file__).resolve().parents[1] / "desktop" / "src" / "rpc" / "types.ts"
)


def test_request_kinds_match_ts_contract_list():
    py = set(re.findall(r'kind == "([a-z_]+)"', RPC_PATH))
    source = TS_PATH.read_text()
    match = re.search(r"export const REQUEST_KINDS = \[([^\]]*)\]", source, re.DOTALL)
    assert match, "desktop/src/rpc/types.ts lost REQUEST_KINDS"
    ts = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert {"hello"} <= py
    assert py == ts, f"drift: python-only={sorted(py - ts)} ts-only={sorted(ts - py)}"


def test_protocol_version_pinned_both_sides():
    assert "PROTOCOL_VERSION = 2" in RPC_PATH
    assert "PROTOCOL_VERSION = 2" in TS_PATH.read_text()


def test_rpc_never_touches_supervisor_internals():
    """The wire layer consumes the taiji supervisor's public API only — the
    2026-11-08 review found it reading ``registry.records`` and calling the
    private ``_record``; board projection lives in ``app/taiji/board.py``."""
    rpc = RPC_PATH
    assert "registry.records" not in rpc
    assert "._record(" not in rpc


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
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
    }


def test_toolcallview_carries_a_kind_discriminator():
    """ToolCallView is the only transcript view without a ``kind``; the desktop
    dispatches on ``'kind' in view`` — a silent break waiting for a refactor."""
    views = (Path(__file__).resolve().parents[1] / "zenagent" / "app" / "views.py").read_text()
    assert 'kind: Literal["tool"]' in views


def test_view_kinds_agree_between_python_and_ts():
    views = (Path(__file__).resolve().parents[1] / "zenagent" / "app" / "views.py").read_text()
    ts = TS_PATH.read_text()
    first_union = re.search(r"kind: Literal\[([^\]]+)\]", views)
    assert first_union, "MessageView lost its kind union"
    py_kinds = set(re.findall(r'"([a-z_]+)"', first_union.group(1)))
    assert py_kinds == {"user", "assistant", "skill"}

    message_view = re.search(r"export interface MessageView \{[^}]+\}", ts)
    assert message_view, "types.ts lost MessageView"
    kind_line = re.search(r"kind: ([^\n]+)", message_view.group(0))
    assert kind_line
    ts_kinds = set(re.findall(r"'([a-z_]+)'", kind_line.group(1)))
    assert ts_kinds == py_kinds

    tool_view = re.search(r"export interface ToolCallView \{[^}]+\}", ts)
    assert tool_view and "kind: 'tool'" in tool_view.group(0)


def test_hello_result_shape_matches_ts():
    """The reviewer found hello answering without ``ok`` while TS required it —
    three sources, three shapes, every suite green. Pin the result frame."""
    rpc = RPC_PATH
    ts = TS_PATH.read_text()
    hello = re.search(r'if kind == "hello":.*?return True', rpc, re.DOTALL)
    assert hello, "hello branch moved"
    assert '"ok": True' in hello.group(0), "hello result must carry ok like every result frame"
    hello_result = re.search(r"export interface HelloResult \{[^}]+\}", ts)
    assert hello_result
    for field in ("ok: true", "protocol_version: number"):
        assert field in hello_result.group(0)


def _board_worker_keys() -> set[str]:
    board = (
        Path(__file__).resolve().parents[1] / "zenagent" / "app" / "taiji" / "board.py"
    ).read_text()
    match = re.search(r"def project_worker\(.*?\n(.*?)\n\n", board, re.DOTALL)
    assert match, "board.project_worker moved"
    return set(re.findall(r'"([a-z_]+)":', match.group(1)))


def _ts_interface_fields(name: str) -> set[str]:
    ts = TS_PATH.read_text()
    match = re.search(rf"export interface {name} \{{([^}}]+)\}}", ts)
    assert match, f"types.ts lost {name}"
    return set(re.findall(r"^\s{2}(\w+)", match.group(1), re.MULTILINE))


def test_taiji_worker_shape_matches_ts():
    """The third hand-maintained projection (supervisor record → board dict →
    TaijiWorker) gets the same drift alarm the request kinds have."""
    py = _board_worker_keys()
    ts = _ts_interface_fields("TaijiWorker")
    assert py, "board.project_worker lost its dict"
    assert py == ts, f"drift: py-only={sorted(py - ts)} ts-only={sorted(ts - py)}"


def test_taiji_board_shape_matches_ts():
    board = (
        Path(__file__).resolve().parents[1] / "zenagent" / "app" / "taiji" / "board.py"
    ).read_text()
    match = re.search(r"def project_board\(.*?return \{(.*?)\}", board, re.DOTALL)
    assert match, "board.project_board lost its dict"
    py = set(re.findall(r'"([a-z_]+)":', match.group(1)))
    ts = _ts_interface_fields("TaijiBoard")
    assert py == ts, f"drift: py-only={sorted(py - ts)} ts-only={sorted(ts - py)}"


def test_taiji_error_codes_agree_both_sides():
    """Server taiji errors cross the wire as codes + params; the desktop
    dictionary must carry a key for every code the server can send."""
    rpc = RPC_PATH
    py_codes = set(re.findall(r'"code": "(taiji\.[a-z_]+)"', rpc))
    assert py_codes == {
        "taiji.inactive",
        "taiji.merge_blocked",
        "taiji.worker_missing",
        "taiji.bad_limit",
    }
    errors_ts = (
        Path(__file__).resolve().parents[1] / "desktop" / "src" / "i18n" / "errors.ts"
    ).read_text()
    ts_keys = set(re.findall(r"'(taiji\.[a-z_]+)':", errors_ts))
    assert ts_codes_ok(py_codes, ts_keys)


def ts_codes_ok(py_codes: set[str], ts_keys: set[str]) -> bool:
    return py_codes <= set(ts_keys)
