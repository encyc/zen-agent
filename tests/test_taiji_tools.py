"""The Router's worker tools, the session gate, and the real worker runner."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from zenagent.agent import Agent
from zenagent.app.taiji.tools import (
    ROUTER_EXCLUDED_TOOLS,
    RpcRunner,
    build_taiji_tools,
    router_tool_gate,
    runtime_taiji_provider,
    taiji_tools_for,
)
from zenagent.app.taiji.worker_client import WorkerRpcClient
from zenagent.cli.rpc import RpcServer
from zenagent.events import AssistantEvent
from zenagent.types import AssistantMessage, TextContent, Usage

TOOL_NAMES = {
    "spawn_worker",
    "message_worker",
    "kill_worker",
    "merge_worker",
    "list_workers",
}


def make_tools() -> list:
    return build_taiji_tools(lambda: None)


def test_router_tool_set_shapes() -> None:
    tools = make_tools()
    assert {tool.name for tool in tools} == TOOL_NAMES
    for tool in tools:
        assert tool.approval == "read"
        assert tool.execution_mode == "sequential"
    schemas = {tool.name: tool.parameters for tool in tools}
    assert schemas["spawn_worker"]["required"] == ["prompt"]
    assert schemas["message_worker"]["required"] == ["name", "text"]
    assert schemas["kill_worker"]["required"] == ["name"]
    assert schemas["merge_worker"]["required"] == ["name"]
    assert schemas["list_workers"].get("required") in (None, [])


def test_session_gate_composes_only_for_the_taiji_session(tmp_path: Path) -> None:
    assert len(taiji_tools_for(tmp_path / "taiji.jsonl", lambda: None)) == 5
    assert taiji_tools_for(tmp_path / "20260101-010101.jsonl", lambda: None) == []
    assert taiji_tools_for(None, lambda: None) == []


class _NamedTool:
    """The gate only reads ``name``; a stub keeps the test off tool plumbing."""

    def __init__(self, name: str) -> None:
        self.name = name


def test_router_gate_strips_the_execution_tools() -> None:
    tools = [
        _NamedTool("read_file"),
        _NamedTool("edit_file"),
        _NamedTool("bash"),
        _NamedTool("write_file"),
        _NamedTool("grep"),
    ]
    gated = router_tool_gate(tools)
    assert [tool.name for tool in gated] == ["read_file", "bash", "grep"]
    # Pure: the caller's list is untouched (recompose rebuilds from scratch).
    assert [tool.name for tool in tools] == [
        "read_file",
        "edit_file",
        "bash",
        "write_file",
        "grep",
    ]


def test_router_gate_is_exactly_the_write_pair() -> None:
    # bash stays: the Router still inspects (git plumbing, read-only checks);
    # only the file-mutation tools are worker territory.
    assert ROUTER_EXCLUDED_TOOLS == {"edit_file", "write_file"}


def test_python_dash_m_zenagent_runs() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "zenagent", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0
    assert "zenagent" in (completed.stdout + completed.stderr).lower()


class LoopbackTransport:
    def __init__(self, server: RpcServer, out: asyncio.Queue) -> None:
        self.server = server
        self.out = out
        self._on_line = None

    async def start(self, on_line) -> None:
        self._on_line = on_line
        asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while True:
            line = await self.out.get()
            if line is None:
                return
            await self._on_line(line)

    async def send_line(self, line: str) -> None:
        assert await self.server._handle_line(line) is not False

    async def stop(self) -> None:
        self.out.put_nowait(None)


async def test_rpc_runner_drives_a_turn_over_the_loopback() -> None:
    from test_taiji_worker_client import (  # sibling test module, prompt_fixtures style
        model,
        stream,
    )

    agent = Agent(
        model=model(), system_prompt="s", api_key="k", stream_fn=stream("did the work")
    )
    out: asyncio.Queue = asyncio.Queue()
    server = RpcServer(agent, read_line=None, write_line=out.put_nowait)  # type: ignore[arg-type]
    runner = RpcRunner(
        client_factory=lambda: WorkerRpcClient(transport=LoopbackTransport(server, out))
    )

    result = await runner.run_turn("/tmp/wt", None, "fix it")

    assert result.status == "ok"
    assert "did the work" in result.summary


async def test_runtime_provider_binds_current_project(
    runtime_factory, monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    await asyncio.to_thread(
        subprocess.run,
        ["git", "init", "-q", "-b", "main"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repo)
    runtime = await runtime_factory()

    provider = runtime_taiji_provider(runtime)
    supervisor = provider()
    try:
        assert supervisor.repo_root == repo.resolve()
        assert supervisor.mode_of() == runtime.mode
        assert supervisor.chat is None  # no host attached the Router chat yet
    finally:
        from zenagent.app.taiji import close_taiji

        await close_taiji()


async def test_rpc_runner_maps_a_failed_turn_to_error() -> None:
    """A graceful ok:false result frame (provider error, max turns, abort)
    must surface as an error turn — never as a mergeable success."""
    from test_taiji_worker_client import model

    def stream_fail(text: str):
        async def fn(model_, messages, tools, options):
            partial = AssistantMessage(
                content=[],
                provider="p",
                model="m",
                usage=Usage(),
                stop_reason="pending",
                timestamp=0,
            )
            yield AssistantEvent(type="start", partial=partial)
            block = TextContent(text="")
            partial.content.append(block)
            block.text += text
            partial.stop_reason = "error"
            yield AssistantEvent(type="done", partial=partial, reason="error")

        return fn

    agent = Agent(
        model=model(),
        system_prompt="s",
        api_key="k",
        stream_fn=stream_fail("half done"),
    )
    out: asyncio.Queue = asyncio.Queue()
    server = RpcServer(agent, read_line=None, write_line=out.put_nowait)  # type: ignore[arg-type]
    runner = RpcRunner(
        client_factory=lambda: WorkerRpcClient(transport=LoopbackTransport(server, out))
    )

    result = await runner.run_turn("/tmp/wt", None, "fix it")

    assert result.status == "error"
    assert result.summary


async def test_chat_controller_wires_the_router_injection_target(
    runtime_factory,
) -> None:
    """Every face drives the runtime through a ChatController, so the taiji
    milestone injection works in TUI/headless too, not just the rpc host."""
    from zenagent.app.chat import ChatController

    runtime = await runtime_factory()

    chat = ChatController(runtime)

    assert runtime.taiji_chat is chat
