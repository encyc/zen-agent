"""JSONL RPC mode: one request per stdin line, one JSON object per stdout line.

Designed for embedding (editors, desktop shells, tests): stdout carries protocol
only, every line parses as JSON, and logs stay on stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable

from zenagent.agent import Agent
from zenagent.events import AgentEvent
from zenagent.types import AssistantMessage

__all__ = ["RpcServer", "main", "serialize_event"]

ReadLine = Callable[[], Awaitable[str | None]]
WriteLine = Callable[[str], None]


def serialize_event(event: AgentEvent) -> dict:
    """A small, stable projection of an AgentEvent (no prompts, no key material)."""
    payload: dict = {"type": event.type}
    for field in ("tool_name", "tool_call_id"):
        value = getattr(event, field, None)
        if value:
            payload[field] = value
    if getattr(event, "is_error", None) is not None:
        payload["is_error"] = event.is_error
    assistant_event = getattr(event, "assistant_event", None)
    if assistant_event is not None:
        payload["assistant_event"] = {"type": assistant_event.type}
        if assistant_event.delta:
            payload["assistant_event"]["delta"] = assistant_event.delta
    return payload


def _usage(agent: Agent) -> dict:
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    for message in agent.messages:
        if not isinstance(message, AssistantMessage):
            continue
        usage = message.usage
        totals["input"] += usage.input
        totals["output"] += usage.output
        totals["cache_read"] += usage.cache_read
        totals["cache_write"] += usage.cache_write
        totals["cost"] += usage.cost.total
    return totals


class RpcServer:
    """Reads requests, streams agent events, writes one result per request."""

    def __init__(self, agent: Agent, read_line: ReadLine, write_line: WriteLine) -> None:
        self.agent = agent
        self._read_line = read_line
        self._write_line = write_line
        self._task: asyncio.Task | None = None
        self._text: list[str] = []
        self.agent.subscribe(self._on_event)

    # -- wire ---------------------------------------------------------------

    def _send(self, payload: dict) -> None:
        self._write_line(json.dumps(payload, ensure_ascii=False))

    def _on_event(self, event: AgentEvent) -> None:
        if (
            event.type == "message_update"
            and event.assistant_event is not None
            and event.assistant_event.type == "text_delta"
            and event.assistant_event.delta
        ):
            self._text.append(event.assistant_event.delta)
        self._send({"type": "event", "event": serialize_event(event)})

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- loop ---------------------------------------------------------------

    async def run(self) -> int:
        while True:
            line = await self._read_line()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if not self._handle_line(line):
                await self._shutdown()
                return 0
        await self._shutdown()
        return 0

    def _handle_line(self, line: str) -> bool:
        """Handle one request line; False means shutdown."""
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError(  # noqa: TRY004 - bad wire data is an error, not a type
                    "request must be a JSON object"
                )
        except ValueError as exc:
            self._send({"type": "error", "id": None, "message": f"bad request: {exc}"})
            return True

        kind = request.get("type")
        request_id = request.get("id")
        if kind == "prompt":
            text = request.get("text")
            if not isinstance(text, str) or not text.strip():
                self._send({"type": "error", "id": request_id, "message": "prompt needs text"})
                return True
            if self.running:
                self._send({"type": "error", "id": request_id, "message": "busy"})
                return True
            self._text = []
            self._task = asyncio.create_task(self._run_prompt(text, request_id))
            return True
        if kind == "cancel":
            if self.running:
                self.agent.abort()
                self._send({"type": "result", "id": request_id, "ok": True, "cancelled": True})
            else:
                self._send({"type": "result", "id": request_id, "ok": True, "cancelled": False})
            return True
        if kind == "status":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "running": self.running,
                    "messages": len(self.agent.messages),
                    "model": self.agent.model.id,
                }
            )
            return True
        if kind == "shutdown":
            self._send({"type": "result", "id": request_id, "ok": True})
            return False
        self._send({"type": "error", "id": request_id, "message": f"unknown type: {kind}"})
        return True

    async def _run_prompt(self, text: str, request_id) -> None:
        try:
            await self.agent.prompt(text)
        except Exception as exc:  # noqa: BLE001 - report, never kill the server
            self._send({"type": "error", "id": request_id, "message": str(exc)})
            return
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "text": "".join(self._text).strip(),
                "usage": _usage(self.agent),
                "termination": self.agent.termination,
            }
        )

    async def _shutdown(self) -> None:
        if self.running:
            self.agent.abort()
            await asyncio.gather(self._task, return_exceptions=True)


async def _stdin_line() -> str | None:
    line = await asyncio.to_thread(sys.stdin.readline)
    return None if line == "" else line


def _stdout_line(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


async def _run(args) -> int:
    from zenagent.cli.main import _api_key_for, _build_agent, resolve_model
    from zenagent.cli.permissions import PermissionGate
    from zenagent.config import apply_user_config, load_user_config
    from zenagent.providers.catalog import load_catalog
    from zenagent.tools import ALL_TOOLS
    from zenagent.tools.todo import build_todo_tool

    user_config = load_user_config()
    catalog = apply_user_config(load_catalog(), user_config)
    model = resolve_model(catalog, args.model)
    api_key = _api_key_for(catalog, model)
    gate = PermissionGate(assume_yes=args.yes, interactive=False)
    agent = _build_agent(
        model,
        api_key,
        "off" if not model.reasoning else "high",
        gate,
        tools=[*list(ALL_TOOLS), build_todo_tool(None)],
        max_turns=args.max_turns,
        max_cost=args.max_cost,
    )
    server = RpcServer(agent, _stdin_line, _stdout_line)
    return await server.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zenagent rpc", description=__doc__.splitlines()[0])
    parser.add_argument("-m", "--model", help="model id")
    parser.add_argument("--yes", action="store_true", help="approve non-read tools")
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--max-cost", type=float)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
