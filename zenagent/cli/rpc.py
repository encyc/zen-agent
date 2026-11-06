"""JSONL RPC mode: one request per stdin line, one JSON object per stdout line.

Designed for embedding (editors, desktop shells, tests): stdout carries protocol
only, every line parses as JSON, and logs stay on stderr.

Protocol v2 (v1 messages keep their shape):

    requests  prompt, cancel, status, shutdown, steer, tools, model, mode,
              usage, approval, session, command
    responses {"type":"result"|"error", "id": ..., ...}
    events    {"type":"event","event":{...}} plus ``approval_request`` events

A server built with a Runtime exposes sessions, MCP/skills tools, commands and
an interactive approval round-trip. A server built from a bare Agent keeps the
v1 surface and answers the newer requests with an error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from zenagent.agent import Agent
from zenagent.events import AgentEvent
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

__all__ = ["RpcServer", "main", "serialize_event"]

ReadLine = Callable[[], Awaitable[str | None]]
WriteLine = Callable[[str], None]

#: How much of a running tool's streamed output an RPC event may carry.
_PARTIAL_RESULT_CHARS = 4000
#: Bound for one JSON-encoded tool argument blob in an event.
_ARGS_CHARS = 4000
#: Default approval timeout, matching the TUI modal.
_APPROVAL_TIMEOUT_S = 120.0


def _text_of(result) -> str:
    return "".join(
        block.text
        for block in getattr(result, "content", [])
        if isinstance(block, TextContent)
    )


def _message_text(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return _text_of(message)


def _message_projection(message) -> dict:
    """A bounded, client-friendly view of one message (never the raw object)."""
    if isinstance(message, AssistantMessage):
        role = "assistant"
    elif isinstance(message, ToolResultMessage):
        role = "toolResult"
    elif isinstance(message, UserMessage):
        role = "user"
    else:
        role = "message"
    payload: dict = {"role": role, "text": _message_text(message)[-_PARTIAL_RESULT_CHARS:]}
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason:
        payload["stop_reason"] = stop_reason
    tool_name = getattr(message, "tool_name", None)
    if tool_name:
        payload["tool_name"] = tool_name
    is_error = getattr(message, "is_error", None)
    if is_error is not None:
        payload["is_error"] = is_error
    if getattr(message, "steering", False):
        payload["steering"] = True
    return payload


def _bounded_json(value) -> str:
    """One bounded JSON string: a huge blob cannot blow up a protocol line."""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:_ARGS_CHARS]
    except (TypeError, ValueError):
        return str(value)[:_ARGS_CHARS]


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
        if assistant_event.type == "error":
            error = getattr(assistant_event.partial, "error_message", None)
            if error:
                payload["assistant_event"]["error"] = str(error)[:_PARTIAL_RESULT_CHARS]
    message = getattr(event, "message", None)
    if message is not None:
        # The canonical turn content: clients that do not reassemble deltas
        # (or providers that only send the terminal message) still get it.
        payload["message"] = _message_projection(message)
    new_messages = getattr(event, "new_messages", None)
    if new_messages:
        payload["new_messages"] = [
            _message_projection(item) for item in new_messages[:20]
        ]
    args = getattr(event, "args", None)
    if args is not None:
        # JSON text rather than a nested object: one bounded string keeps a
        # huge tool call from blowing up the line, and the client can parse it.
        payload["args"] = _bounded_json(args)
    result = getattr(event, "result", None)
    if result is not None:
        text = _text_of(result)
        if text:
            payload["result_text"] = text[-_PARTIAL_RESULT_CHARS:]
        details = getattr(result, "details", None)
        if details:
            # Structured tool data (diff, exit_code, task_id, todo phases,
            # subagent cost/tokens, ...) the TUI cards and HUD render.
            payload["details"] = _bounded_json(details)
    duration = getattr(event, "duration_ms", None)
    if duration is not None:
        payload["duration_ms"] = duration
    tool_stats = getattr(event, "tool_stats", None)
    if tool_stats:
        payload["tool_stats"] = tool_stats
    partial = getattr(event, "partial_result", None)
    if partial is not None:
        # A running tool's streamed output (bash progress, subagent text). The
        # tail is what an embedding UI can render; the full result follows in
        # ``tool_execution_end``. Never let one update carry unbounded bytes.
        text = _text_of(partial)
        if text:
            payload["partial_result"] = text[-_PARTIAL_RESULT_CHARS:]
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

    def __init__(
        self,
        agent: Agent | None = None,
        read_line: ReadLine | None = None,
        write_line: WriteLine | None = None,
        *,
        runtime=None,
        chat=None,
        models=None,
        sessions=None,
        commands=None,
        approval_timeout_s: float = _APPROVAL_TIMEOUT_S,
    ) -> None:
        self.runtime = runtime
        self._agent = agent
        self.chat = chat
        self.models = models
        self.sessions = sessions
        self.commands = commands
        self.approval_timeout_s = approval_timeout_s
        self._read_line = read_line
        self._write_line = write_line
        self._task: asyncio.Task | None = None
        self._text: list[str] = []
        self._approvals: dict[str, asyncio.Future] = {}
        self._approval_seq = 0
        self._closed = False
        self.agent.subscribe(self._on_event)

    # -- wire ---------------------------------------------------------------

    @property
    def agent(self) -> Agent:
        if self.runtime is not None:
            return self.runtime.agent
        if self._agent is None:  # pragma: no cover - construction guard
            raise RuntimeError("RpcServer needs an agent or a runtime")
        return self._agent

    def _send(self, payload: dict) -> None:
        if self._write_line is not None:
            self._write_line(json.dumps(payload, ensure_ascii=False, default=str))

    def _on_event(self, event: AgentEvent) -> None:
        if (
            event.type == "message_update"
            and event.assistant_event is not None
            and event.assistant_event.type == "text_delta"
            and event.assistant_event.delta
        ):
            self._text.append(event.assistant_event.delta)
        self._send({"type": "event", "event": serialize_event(event)})

    def _final_text(self) -> str:
        """The run's answer: streamed deltas, else the canonical final message.

        Deltas are the happy path, but a provider (or a buffered retry path)
        may only publish the terminal message; the TUI canonicalizes from
        ``message_end`` for exactly that reason.
        """
        text = "".join(self._text).strip()
        if text:
            return text
        for message in reversed(self.agent.messages):
            if isinstance(message, AssistantMessage):
                return _message_text(message).strip()
        return ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def busy(self) -> bool:
        """True while a run owns the agent, however it was started.

        ``prompt`` owns ``_task``; ``session op=retry`` and queued follow-ups
        start runs on the ChatController instead, so ``running`` alone would
        misreport the server as idle.
        """
        return self.running or self.agent.is_streaming

    # -- approval round-trip ------------------------------------------------

    async def confirm(self, tool_name: str, arguments: dict, verdict=None) -> str:
        """Ask the client to approve one tool call; ``deny`` on timeout."""
        request_id = f"a{self._approval_seq}"
        self._approval_seq += 1
        try:
            args = json.dumps(arguments, ensure_ascii=False, default=str)[
                :_ARGS_CHARS
            ]
        except (TypeError, ValueError):  # pragma: no cover - defensive
            args = str(arguments)[:_ARGS_CHARS]
        event: dict = {
            "type": "approval_request",
            "request_id": request_id,
            "tool_name": tool_name,
            "args": args,
        }
        if verdict is not None:
            event["kind"] = verdict.kind
            event["risk"] = verdict.risk
            event["target"] = verdict.target
            event["reason"] = verdict.reason
        self._send({"type": "event", "event": event})

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approvals[request_id] = future
        try:
            return await asyncio.wait_for(future, self.approval_timeout_s)
        except TimeoutError:
            return "deny"
        finally:
            self._approvals.pop(request_id, None)

    def _resolve_approval(self, request: dict) -> bool:
        request_id = request.get("request_id")
        decision = request.get("decision")
        if decision not in ("allow", "always", "deny"):
            self._send(
                {
                    "type": "error",
                    "id": request.get("id"),
                    "message": "decision must be allow|always|deny",
                }
            )
            return True
        future = self._approvals.get(str(request_id))
        if future is None or future.done():
            self._send(
                {
                    "type": "error",
                    "id": request.get("id"),
                    "message": f"no pending approval: {request_id}",
                }
            )
            return True
        future.set_result(decision)
        self._send({"type": "result", "id": request.get("id"), "ok": True})
        return True

    # -- loop ---------------------------------------------------------------

    async def run(self) -> int:
        while True:
            line = await self._read_line()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if not await self._handle_line(line):
                await self._shutdown()
                return 0
        await self._shutdown()
        return 0

    async def _handle_line(self, line: str) -> bool:
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
            images, image_error = self._read_prompt_images(request.get("images"))
            if image_error is not None:
                self._send(
                    {"type": "error", "id": request_id, "message": image_error}
                )
                return True
            if self.busy:
                self._send({"type": "error", "id": request_id, "message": "busy"})
                return True
            self._text = []
            self._task = asyncio.create_task(
                self._run_prompt(text, request_id, images)
            )
            return True
        if kind == "cancel":
            # A run may have been started by a retry session op or a queued
            # follow-up, in which case ``_task`` is None but the agent is busy.
            if self.busy:
                self.agent.abort()
                self._send({"type": "result", "id": request_id, "ok": True, "cancelled": True})
            else:
                self._send({"type": "result", "id": request_id, "ok": True, "cancelled": False})
            return True
        if kind == "status":
            self._send(self._status_result(request_id))
            return True
        if kind == "shutdown":
            self._send({"type": "result", "id": request_id, "ok": True})
            return False
        if kind == "steer":
            return self._steer(request, request_id)
        if kind == "tools":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "approval": getattr(tool, "approval", "exec"),
                        }
                        for tool in self.agent.tools
                    ],
                }
            )
            return True
        if kind == "models":
            self._send(self._models_result(request_id))
            return True
        if kind == "commands":
            self._send(self._commands_result(request_id))
            return True
        if kind == "transcript":
            self._send(self._transcript_result(request_id))
            return True
        if kind == "model":
            return self._model(request, request_id)
        if kind == "mode":
            return self._mode(request, request_id)
        if kind == "usage":
            self._send(
                {"type": "result", "id": request_id, "ok": True, "usage": _usage(self.agent)}
            )
            return True
        if kind == "approval":
            return self._resolve_approval(request)
        if kind == "session":
            await self._session(request, request_id)
            return True
        if kind == "command":
            return await self._command(request, request_id)
        self._send({"type": "error", "id": request_id, "message": f"unknown type: {kind}"})
        return True

    def _status_result(self, request_id) -> dict:
        payload = {
            "type": "result",
            "id": request_id,
            "ok": True,
            "running": self.busy,
            "messages": len(self.agent.messages),
            "model": self.agent.model.id,
            "usage": _usage(self.agent),
            "termination": self.agent.termination,
        }
        if self.runtime is not None:
            payload["session"] = str(self.runtime.session.path)
            payload["mode"] = self.runtime.mode
            payload["tools"] = len(self.agent.tools)
        if self.chat is not None:
            status = self.chat.status
            payload["busy"] = self.chat.busy
            payload["follow_ups"] = status.follow_ups
            payload["steering"] = status.steering
            payload["cost"] = status.cost
            # The reducer the TUI renders: context composition, thinking,
            # todos, live jobs/subagents and the pending lanes. A client that
            # only reads `status` no longer has to guess.
            payload["thinking"] = status.thinking
            payload["context_used"] = status.context_used
            payload["context_window"] = status.context_window
            payload["cache_hit_ratio"] = status.cache_hit_ratio
            payload["turn_ms"] = status.turn_ms
            payload["todos"] = status.todos
            payload["last_error"] = status.last_error
            payload["pending"] = [asdict(item) for item in status.pending]
            payload["jobs"] = [asdict(view) for view in status.jobs]
            payload["subagents"] = [asdict(view) for view in status.subagents]
            payload["active_subagent"] = status.active_subagent
            payload["subagent_cost"] = status.subagent_cost
            payload["subagent_tokens"] = status.subagent_tokens
        return payload

    def _steer(self, request: dict, request_id) -> bool:
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            self._send({"type": "error", "id": request_id, "message": "steer needs text"})
            return True
        if self.chat is not None:
            self.chat.steer(text)
        else:
            self.agent.steer(text)
        self._send({"type": "result", "id": request_id, "ok": True})
        return True

    def _transcript_result(self, request_id) -> dict:
        """The current transcript as view models (the TUI reducer's output).

        A GUI that resumes or switches sessions has no event journal to replay:
        this snapshot is its baseline, and live events then apply on top of it.
        """
        from dataclasses import asdict

        views: list = []
        notices: list = []
        if self.chat is not None:
            views = [asdict(view) for view in self.chat.transcript]
            notices = [asdict(notice) for notice in self.chat.notices_snapshot]
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "transcript": views,
            "notices": notices,
        }

    def _models_result(self, request_id) -> dict:
        """The model catalog the TUI picker renders from, over the wire."""
        entries = []
        if self.models is not None:
            from dataclasses import asdict

            entries = [asdict(entry) for entry in self.models.entries()]
        return {"type": "result", "id": request_id, "ok": True, "models": entries}

    def _commands_result(self, request_id) -> dict:
        """The built-in slash-command catalog (name + description)."""
        catalog = []
        if self.commands is not None:
            catalog = [
                {"name": command.name, "description": command.description}
                for command in self.commands.names()
            ]
        return {"type": "result", "id": request_id, "ok": True, "commands": catalog}

    def _model(self, request: dict, request_id) -> bool:
        model_id = request.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            self._send({"type": "error", "id": request_id, "message": "model needs model_id"})
            return True
        if self.models is None:
            self._send(
                {"type": "error", "id": request_id, "message": "model switching needs a runtime"}
            )
            return True
        result = self.models.switch(model_id)
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": result.ok,
                "model": self.agent.model.id,
                "reason": result.reason,
            }
        )
        return True

    def _mode(self, request: dict, request_id) -> bool:
        mode = request.get("mode")
        if self.runtime is None:
            self._send(
                {"type": "error", "id": request_id, "message": "mode switching needs a runtime"}
            )
            return True
        try:
            applied = self.runtime.set_mode(str(mode or ""))
        except ValueError as exc:
            self._send({"type": "result", "id": request_id, "ok": False, "reason": str(exc)})
            return True
        self._send({"type": "result", "id": request_id, "ok": True, "mode": applied})
        return True

    # -- run ----------------------------------------------------------------

    def _read_prompt_images(self, raw) -> tuple[list | None, str | None]:
        """Load ``prompt.images`` paths; (blocks, None) or (None, error)."""
        if raw is None:
            return None, None
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            return None, "images must be a list of file paths"
        from zenagent.attachments import read_image_argument

        if not self.agent.model.supports_vision:
            return None, f"{self.agent.model.id} does not accept images"
        blocks = []
        for path in raw:
            try:
                blocks.append(read_image_argument(Path(path)))
            except ValueError as exc:
                return None, str(exc)
        return blocks, None

    async def _run_prompt(self, text: str, request_id, images=None) -> None:
        try:
            if self.chat is not None:
                await self.chat.submit(text, images=images)
                await self.chat.wait_for_idle()
            else:
                await self.agent.prompt(text, images=images)
        except Exception as exc:  # noqa: BLE001 - report, never kill the server
            self._send({"type": "error", "id": request_id, "message": str(exc)})
            return
        termination = self.agent.termination
        error = self.agent.error_message
        ok = error is None and termination in ("stop", "tool_terminate")
        payload = {
            "type": "result",
            "id": request_id,
            "ok": ok,
            "text": self._final_text(),
            "usage": _usage(self.agent),
            "termination": termination,
        }
        if error is not None:
            payload["error"] = error
        elif not ok:
            payload["error"] = f"run stopped early ({termination})"
        if self.runtime is not None:
            payload["session"] = str(self.runtime.session.path)
        self._send(payload)

    # -- sessions and commands ---------------------------------------------

    async def _session(self, request: dict, request_id) -> None:
        op = request.get("op")
        try:
            result = await self._session_op(op, request)
        except Exception as exc:  # noqa: BLE001 - a bad session op is a result, not a crash
            self._send({"type": "error", "id": request_id, "message": str(exc)})
            return
        self._send({"type": "result", "id": request_id, "ok": True, **(result or {})})

    async def _session_op(self, op: str, request: dict) -> dict:
        if self.runtime is None or self.sessions is None:
            raise RuntimeError("session requests need a runtime")
        if op == "current":
            path = self.runtime.session.path
            return {"path": str(path), "name": path.stem}
        if op == "list":
            scope = "all" if request.get("scope") == "all" else "project"
            return {
                "scope": scope,
                "sessions": [
                    {
                        "path": str(entry.path),
                        "name": entry.path.stem,
                        "title": entry.title,
                        "project": entry.project,
                        "messages": entry.messages,
                        "current": entry.current,
                        "pinned": entry.pinned,
                    }
                    for entry in self.sessions.entries(scope=scope)
                ],
            }
        if op == "new":
            path = await self.sessions.new()
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "fork":
            path = await self.sessions.fork()
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "resume":
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("resume needs path")
            await self.sessions.resume(Path(target))
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(self.runtime.session.path)}
        if op == "import":
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("import needs path")
            path = Path(target).expanduser()
            if not path.is_file():
                raise ValueError(f"no such session: {path}")
            await self.runtime.switch_session(path)
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(path)}
        if op == "undo":
            if self.busy:
                raise RuntimeError("busy: cancel the run before undoing")
            result = self.sessions.undo()
            if result is None:
                return {"dropped": 0, "path": None}
            dropped, path = result
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"dropped": dropped, "path": str(path)}
        if op == "delete":
            if self.busy:
                raise RuntimeError("busy: cancel the run before deleting a session")
            target = request.get("path")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("delete needs path")
            trashed = self.sessions.delete(Path(target))
            if self.chat is not None:
                self.chat.reload_transcript(drop_pending=True)
            return {"path": str(trashed)}
        if op == "compact":
            if self.chat is None:
                raise RuntimeError("compaction needs a runtime")
            notice = await self.chat.compact_now()
            if notice is None:
                return {"compacted": False}
            return {"compacted": True, "path": str(self.runtime.session.path)}
        if op == "retry":
            if self.chat is None:
                raise RuntimeError("retry needs a runtime")
            text = self.chat.retry()
            if text is None:
                return {"retried": False}
            return {"retried": True, "text": text}
        if op == "export":
            fmt = request.get("format") or "md"
            target = request.get("path") or self.runtime.session.path
            exported = self.sessions.export(Path(target), fmt)
            return {"path": str(exported)}
        raise ValueError(f"unknown session op: {op}")

    async def _command(self, request: dict, request_id) -> bool:
        """Dispatch one slash command; False means ``quit`` asked to shut down."""
        name = request.get("name")
        if not isinstance(name, str) or not name.strip():
            self._send({"type": "error", "id": request_id, "message": "command needs name"})
            return True
        if self.commands is None:
            self._send(
                {"type": "error", "id": request_id, "message": "commands need a runtime"}
            )
            return True
        if self.commands.get(name) is None:
            self._send(
                {"type": "error", "id": request_id, "message": f"unknown command: {name}"}
            )
            return True
        try:
            result = await self.commands.dispatch(name, str(request.get("argument") or ""))
        except Exception as exc:  # noqa: BLE001 - a command must not kill the server
            self._send({"type": "error", "id": request_id, "message": str(exc)})
            return True
        applied_model = None
        if result.model and self.models is not None:
            switch = self.models.switch(result.model)
            applied_model = self.agent.model.id if switch.ok else None
        if result.language:
            # Mirror TUI/REPL: the server's own subsequent messages (command
            # results, approval reasons, notices) follow the requested locale.
            from zenagent.i18n import set_locale

            set_locale(result.language)
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "command": name,
                "message": result.message,
                "screen": result.screen,
                "target": result.target,
                "show_help": result.show_help,
                "reset_transcript": result.reset_transcript,
                "quit": result.quit,
                "appearance": result.appearance,
                "language": result.language,
                "model": applied_model,
            }
        )
        if result.quit:
            self._closed = True
            return False
        return True

    async def _shutdown(self) -> None:
        # A client that disconnects mid-approval will never answer: deny the
        # outstanding requests first so the run cannot stall for the full
        # approval timeout during shutdown.
        for future in list(self._approvals.values()):
            if not future.done():
                future.set_result("deny")
        if self.running:
            self.agent.abort()
            await asyncio.gather(self._task, return_exceptions=True)
        elif self.agent.is_streaming:
            # A retry/queued run owns a ChatController task, not ``_task``.
            self.agent.abort()
        if self.chat is not None:
            await self.chat.wait_for_idle()


async def _stdin_line() -> str | None:
    line = await asyncio.to_thread(sys.stdin.readline)
    return None if line == "" else line


def _stdout_line(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


async def _run(args) -> int:
    from zenagent.app.chat import ChatController
    from zenagent.app.commands import AppServices, build_default_commands
    from zenagent.app.models import ModelManager
    from zenagent.app.modes import resolve_mode
    from zenagent.app.permissions import PermissionBroker, build_permission_gate
    from zenagent.app.runtime import Runtime, RuntimeOptions
    from zenagent.app.safety import GuardPolicy
    from zenagent.app.sessions import SessionManager
    from zenagent.config import load_user_config

    # Resolve the mode exactly like the other frontends: an explicit --mode
    # wins, then --read-only (plan), then --yes (zen). Passing the raw
    # ``args.mode`` (None when the flag is omitted) into the gate used to
    # raise "unknown mode: None" for every documented ``zen rpc`` invocation.
    user_config = load_user_config()
    mode = resolve_mode(
        explicit=args.mode, assume_yes=args.yes, read_only=args.read_only
    )
    policy = GuardPolicy.from_config(
        user_config,
        cwd=Path.cwd(),
        allow_outside=args.allow_outside or [],
        allow_destructive=args.allow_destructive,
        allow_protected=args.allow_protected,
    )
    broker = PermissionBroker.wrap(
        build_permission_gate(
            assume_yes=args.yes,
            interactive=False,
            allow_dangerous=args.allow_dangerous,
            allow=user_config.tool_allow,
            deny=user_config.tool_deny,
            mode=mode,
            read_only=args.read_only,
            policy=policy,
        )
    )
    options = RuntimeOptions(
        model_id=args.model,
        thinking=args.thinking,
        resume=args.resume,
        mcp=not args.no_mcp,
        mcp_file=Path(args.mcp_file) if args.mcp_file else None,
        mcp_servers=args.mcp_servers,
        skills=not args.no_skills,
        templates=not args.no_templates,
        extension_paths=args.extension_paths or [],
        context_files=not args.no_context_files,
        auto_compact=not args.no_auto_compact,
        read_only=args.read_only,
        allow_dangerous=args.allow_dangerous,
        allow_outside=args.allow_outside or [],
        allow_destructive=args.allow_destructive,
        allow_protected=args.allow_protected,
        mode=mode,
        retries=args.retries,
        max_turns=args.max_turns,
        max_cost=args.max_cost,
        trace=args.trace,
        metrics=args.metrics,
    )
    try:
        runtime = await Runtime.bootstrap(
            options,
            gate=broker,
            assume_yes=args.yes,
            allow_missing_key=False,
        )
    except KeyError as exc:
        print(f"zenagent: unknown model: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, LookupError) as exc:
        print(f"zenagent: {exc}", file=sys.stderr)
        return 2
    for warning in runtime.warnings:
        print(f"zenagent: {warning}", file=sys.stderr)

    chat = ChatController(runtime)
    services = AppServices(
        runtime=runtime,
        chat=chat,
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )
    server = RpcServer(
        runtime=runtime,
        read_line=_stdin_line,
        write_line=_stdout_line,
        chat=chat,
        models=services.models,
        sessions=services.sessions,
        commands=build_default_commands(services),
    )
    broker.set_confirm(server.confirm)
    try:
        return await server.run()
    finally:
        await runtime.close()


def main(argv: list[str] | None = None) -> int:
    from zenagent.app.modes import MODES
    from zenagent.cli.main import THINKING_CHOICES

    parser = argparse.ArgumentParser(prog="zenagent rpc", description=__doc__.splitlines()[0])
    parser.add_argument("-m", "--model", help="model id")
    parser.add_argument("--yes", action="store_true", help="run everything without asking (zen mode)")
    parser.add_argument("--mode", choices=MODES, help="runtime mode: plan|ask|zen")
    parser.add_argument("--read-only", action="store_true", help="refuse write tools")
    parser.add_argument("--thinking", choices=THINKING_CHOICES, help="reasoning level")
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--max-cost", type=float)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--resume", nargs="?", const="latest", help="resume a session")
    parser.add_argument("--no-mcp", action="store_true", help="do not connect MCP servers")
    parser.add_argument("--mcp-file", help="MCP config file")
    parser.add_argument(
        "--mcp-server", action="append", dest="mcp_servers", help="only this MCP server"
    )
    parser.add_argument("--no-skills", action="store_true", help="do not load skills")
    parser.add_argument(
        "--no-templates", action="store_true", help="do not load prompt templates"
    )
    parser.add_argument(
        "--no-auto-compact", action="store_true", help="never compact automatically"
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extension_paths",
        metavar="PATH",
        help="load an extension module (repeatable)",
    )
    parser.add_argument(
        "--no-context-files", action="store_true", help="do not read AGENTS.md"
    )
    parser.add_argument("--trace", metavar="PATH", help="write a JSONL trace")
    parser.add_argument("--metrics", metavar="PATH", help="append per-run metrics")
    parser.add_argument("--cwd", metavar="PATH", help="working directory")
    parser.add_argument(
        "--allow-dangerous", action="store_true", help="allow catastrophic commands"
    )
    parser.add_argument(
        "--allow-outside", action="append", dest="allow_outside", metavar="PATH"
    )
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--allow-protected", action="store_true")
    args = parser.parse_args(argv)
    if args.cwd:
        import os

        target = Path(args.cwd).expanduser()
        if not target.is_dir():
            print(f"zenagent: --cwd is not a directory: {target}", file=sys.stderr)
            return 1
        os.chdir(target.resolve())
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        # A bad programmatic value (e.g. an invalid mode supplied by an
        # embedder rather than through argparse choices) must be a clean
        # stderr line + exit 2, not a traceback.
        print(f"zenagent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
