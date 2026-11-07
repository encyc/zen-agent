"""The taiji worker rpc client, exercised over an in-process RpcServer.

No subprocess is spawned anywhere here: a loopback transport feeds the
client's lines to a real ``RpcServer`` driven by a fake streaming agent, so
the client's framing, correlation and event delivery are tested end to end.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from zenagent.agent import Agent
from zenagent.app.sessions import SessionManager
from zenagent.app.taiji.worker_client import WorkerRpcClient, worker_command
from zenagent.cli.rpc import RpcServer
from zenagent.events import AssistantEvent
from zenagent.providers.catalog import Model
from zenagent.providers.compat import CompatFlags
from zenagent.types import AssistantMessage, Cost, TextContent, Usage


def model() -> Model:
    return Model(
        id="m",
        provider="p",
        api="openai-completions",
        base_url="https://x/v1",
        context_window=1000,
        max_tokens=100,
        reasoning=False,
        cost=Cost(),
        compat=CompatFlags(),
    )


def stream(text: str):
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
        for chunk in (text, " world"):
            block.text += chunk
            yield AssistantEvent(
                type="text_delta", partial=partial, content_index=0, delta=chunk
            )
        partial.stop_reason = "stop"
        yield AssistantEvent(type="done", partial=partial, reason="stop")

    return fn


class LoopbackTransport:
    """Feeds client lines to an in-process RpcServer and its answers back."""

    def __init__(self, server: RpcServer, out: asyncio.Queue[str | None]) -> None:
        self.server = server
        self.out = out
        self._on_line: Callable[[str], Awaitable[None]] | None = None
        self._pump: asyncio.Task[None] | None = None

    async def start(self, on_line: Callable[[str], Awaitable[None]]) -> None:
        self._on_line = on_line
        self._pump = asyncio.create_task(self._pump_out())

    async def _pump_out(self) -> None:
        while True:
            line = await self.out.get()
            if line is None:
                return
            assert self._on_line is not None
            await self._on_line(line)

    async def send_line(self, line: str) -> None:
        assert await self.server._handle_line(line) is not False

    async def stop(self) -> None:
        self.out.put_nowait(None)
        if self._pump is not None:
            try:
                await asyncio.wait_for(self._pump, 1.0)
            except (TimeoutError, asyncio.CancelledError):
                self._pump.cancel()


def make_client(text: str = "worker done") -> tuple[WorkerRpcClient, LoopbackTransport]:
    agent = Agent(model=model(), system_prompt="s", api_key="k", stream_fn=stream(text))
    out: asyncio.Queue[str | None] = asyncio.Queue()
    server = RpcServer(agent, read_line=None, write_line=out.put_nowait)  # type: ignore[arg-type]
    transport = LoopbackTransport(server, out)
    return WorkerRpcClient(transport=transport), transport


async def test_start_handshakes_prompt_round_trips_and_streams_events() -> None:
    events: list[dict] = []
    client, _transport = make_client()

    await client.start(Path("/nowhere"), on_event=events.append)
    result = await client.prompt("fix it", timeout_s=10.0)
    await client.stop()

    assert result["ok"] is True
    assert result["text"] == "worker done world"
    kinds = [event["type"] for event in events]
    assert "agent_start" in kinds
    assert "agent_end" in kinds


async def test_resume_targets_an_existing_session(runtime_factory) -> None:
    runtime = await runtime_factory()
    out: asyncio.Queue[str | None] = asyncio.Queue()
    server = RpcServer(
        runtime=runtime,
        sessions=SessionManager(runtime),
        write_line=out.put_nowait,
    )
    client = WorkerRpcClient(transport=LoopbackTransport(server, out))

    await client.start(Path("/nowhere"), on_event=lambda event: None)
    result = await client.resume(str(runtime.session.path))
    await client.stop()

    assert result["ok"] is True
    assert result["path"] == str(runtime.session.path)


async def test_prompt_timeout_raises() -> None:
    client, transport = make_client()

    class Stall(LoopbackTransport):
        async def send_line(self, line: str) -> None:
            if json.loads(line).get("type") == "prompt":
                return  # never answers the prompt
            await self.server._handle_line(line)

    client.transport = Stall(transport.server, transport.out)  # type: ignore[assignment]
    await client.start(Path("/nowhere"), on_event=lambda event: None)
    try:
        await client.prompt("fix it", timeout_s=0.05)
        raised = False
    except TimeoutError:
        raised = True
    assert raised is True
    await client.stop()


def test_worker_command_prefers_zen_on_path(monkeypatch) -> None:
    monkeypatch.setattr(
        "zenagent.app.taiji.worker_client.shutil.which",
        lambda name: "/usr/local/bin/zen",
    )
    assert worker_command(Path("/wt")) == [
        "/usr/local/bin/zen",
        "rpc",
        "--cwd",
        "/wt",
        "--yes",
    ]

    monkeypatch.setattr(
        "zenagent.app.taiji.worker_client.shutil.which", lambda name: None
    )
    assert worker_command(Path("/wt")) == [
        sys.executable,
        "-m",
        "zenagent",
        "rpc",
        "--cwd",
        "/wt",
        "--yes",
    ]


async def test_stop_fails_a_pending_prompt_instead_of_hanging() -> None:
    client, transport = make_client()

    class Stall(LoopbackTransport):
        async def send_line(self, line: str) -> None:
            if json.loads(line).get("type") == "prompt":
                return  # never answers
            await self.server._handle_line(line)

    client.transport = Stall(transport.server, transport.out)  # type: ignore[assignment]
    await client.start(Path("/nowhere"), on_event=lambda event: None)
    prompt_task = asyncio.create_task(client.prompt("go", timeout_s=30.0))
    await asyncio.sleep(0.05)
    await client.stop()

    try:
        await asyncio.wait_for(prompt_task, 2.0)
        raised = False
    except (RuntimeError, TimeoutError):
        raised = True
    assert raised is True


def test_worker_command_carries_the_model_flag() -> None:
    assert "--model" not in worker_command(Path("/wt"))

    command = worker_command(Path("/wt"), model="glm-5.3")
    assert command[-2:] == ["--model", "glm-5.3"]
