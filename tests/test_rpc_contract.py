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


def _wire_codes(prefix: str) -> set[str]:
    """Stable error codes the RPC source can send, in either spelling: a code
    literal inside a frame dict, or a keyword argument to the shared frame
    helpers (`error_frame`/`failure_frame`)."""
    pattern = rf"({prefix}[a-z_.]*)"
    return set(re.findall(rf'"code": "{pattern}"', RPC_PATH)) | set(
        re.findall(rf'code="{pattern}"', RPC_PATH)
    )
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
    assert "PROTOCOL_VERSION = 3" in RPC_PATH
    assert "PROTOCOL_VERSION = 3" in TS_PATH.read_text()


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


def test_sanitized_error_codes_agree_both_sides():
    """I3: prompt/fs failures cross as codes; the desktop dictionary must
    carry a copy key for each, or the UI silently falls back to English."""
    py_codes = _wire_codes(r"(?:prompt|fs)")
    assert py_codes == {"prompt_failed", "fs_error"}
    errors_ts = (
        Path(__file__).resolve().parents[1] / "desktop" / "src" / "i18n" / "errors.ts"
    ).read_text()
    ts_keys = set(re.findall(r"'((?:prompt|fs)[a-z_]*)':", errors_ts))
    assert ts_codes_ok(py_codes, ts_keys)


def test_transcript_paging_fields_are_additive_and_camel_case():
    """I4: `limit`/`before` in, `hasMoreBefore`/`total` out; `after` in,
    `total` out. Optional on both sides, camelCase on the wire, version
    untouched."""
    rpc = RPC_PATH
    assert '"hasMoreBefore":' in rpc, "transcript result lost hasMoreBefore"
    assert '"total":' in rpc, "transcript result lost its absolute total"
    assert 'get("limit")' in rpc
    assert 'get("before")' in rpc
    assert 'get("after")' in rpc

    ts = TS_PATH.read_text()
    transcript = re.search(r"export interface TranscriptResult \{[^}]+\}", ts)
    assert transcript, "types.ts lost TranscriptResult"
    assert "hasMoreBefore?: boolean" in transcript.group(0)
    assert "total?: number" in transcript.group(0)

    subagent = re.search(r"export interface SubagentTranscriptResult \{[^}]+\}", ts)
    assert subagent, "types.ts lost SubagentTranscriptResult"
    assert "total?: number" in subagent.group(0)


def ts_codes_ok(py_codes: set[str], ts_keys: set[str]) -> bool:
    return py_codes <= set(ts_keys)


SERVER_FRAME_TYPES = {
    "result", "error", "event", "zen_fleet",
    "swarm_message", "swarm_task", "swarm_agent", "swarm_notice", "swarm_event",
    "swarm_work",
}


def _backend_source() -> str:
    """The wire package plus the swarm/zen frame emitters: every server-initiated
    frame literal the desktop's ServerFrame union must cover."""
    pkg = Path(__file__).resolve().parents[1] / "zenagent"
    parts = [RPC_PATH]
    for rel in ("app/swarm/manager.py", "app/swarm/frames.py", "app/zen/fleet.py"):
        parts.append((pkg / rel).read_text())
    return "\n".join(parts)


def test_server_frame_union_matches_backend_literals():
    # The ServerFrame union spans types.ts and types-swarm.ts (the swarm frame
    # interfaces are re-exported from the latter).
    src = TS_PATH.read_text() + (TS_PATH.parent / "types-swarm.ts").read_text()
    ts = set(re.findall(r"type:\s*'([a-z_]+)'", src))
    assert SERVER_FRAME_TYPES <= ts, f"TS union missing {sorted(SERVER_FRAME_TYPES - ts)}"
    backend = _backend_source()
    for lit in SERVER_FRAME_TYPES:
        assert f'"type": "{lit}"' in backend or f'type="{lit}"' in backend, (
            f"backend never emits {lit}"
        )


def test_swarm_frames_carry_swarm_flag():
    """Every swarm_* emission sets swarm=True so the desktop diverts it ahead of
    the session filter. Match both spellings: a dict literal (``"type": "x"``)
    and the frame-helper keyword form (``record.update(type="x", swarm=True)``)."""
    backend = _backend_source()
    for lit in ("swarm_message", "swarm_task", "swarm_agent", "swarm_notice", "swarm_event"):
        sites = list(re.finditer(rf'(?:"type":\s*"{lit}"|type="{lit}")', backend))
        assert sites, f"no emission site found for {lit}"
        for m in sites:
            window = backend[m.start(): m.start() + 240]
            assert "swarm=True" in window or '"swarm": True' in window, f"{lit} missing swarm flag"


async def test_every_swarm_frame_names_the_project_of_its_room(tmp_path, monkeypatch):
    """A post drives every frame kind the desktop routes: each one must carry
    ``project`` (the room it belongs to), not just the hand-made ones."""
    from conftest import text_stream

    from zenagent.app.runtime import RuntimeOptions
    from zenagent.app.swarm.manager import SwarmManager as RealSwarmManager
    from zenagent.cli.rpc.host import RpcHost
    from zenagent.project import find_project_root

    role_dir = tmp_path / ".zenagent" / "agents" / "research"
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.json").write_text(
        json.dumps({"id": "research", "name": "Research"}), encoding="utf-8"
    )
    (role_dir / "prompt.md").write_text("You research.", encoding="utf-8")

    class ScriptedSwarmManager(RealSwarmManager):
        def __init__(self, **kwargs):
            kwargs["stream_fn_factory"] = lambda role: text_stream("@you done")
            super().__init__(**kwargs)

    monkeypatch.setattr(
        "zenagent.cli.rpc.ops_swarm.SwarmManager", ScriptedSwarmManager
    )

    writes: list[dict] = []
    host = RpcHost(
        initial=None,
        options=RuntimeOptions(root=tmp_path),
        read_line=None,
        write_line=lambda line: writes.append(json.loads(line)),
    )
    project = str(find_project_root(tmp_path))
    try:
        await host._handle_line(
            json.dumps(
                {
                    "type": "swarm",
                    "op": "post",
                    "text": "@research hi",
                    "id": "s1",
                    "project": str(tmp_path),
                }
            )
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if any(
                frame.get("type") == "swarm_task" and frame.get("state") == "done"
                for frame in writes
            ):
                break
            await asyncio.sleep(0.01)

        frames = [
            frame
            for frame in writes
            if str(frame.get("type", "")).startswith("swarm_")
        ]
        assert {frame["type"] for frame in frames} == SERVER_FRAME_TYPES & {
            "swarm_message",
            "swarm_task",
            "swarm_agent",
            "swarm_notice",
            "swarm_event",
            "swarm_work",
        }
        assert all(frame.get("swarm") is True for frame in frames)
        assert all(frame.get("project") == project for frame in frames), [
            (frame.get("type"), frame.get("project")) for frame in frames
        ]
    finally:
        await host.close()
