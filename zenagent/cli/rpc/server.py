"""The RpcServer core: child state, dispatch, and the chat/run wire handlers.

Composed from the domain mixins: FilesOps, TaijiOps, SessionOps, ConfigOps.
Pure relocation from the pre-split ``cli/rpc.py`` — zero behavior change.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from zenagent.agent import Agent
from zenagent.app.skill_commands import (
    parse_skill_display,
)
from zenagent.app.views import usage_total
from zenagent.app.workspace import (
    worktree_overview,
)
from zenagent.cli.rpc.errors import failure_frame
from zenagent.cli.rpc.ops_config import ConfigOps
from zenagent.cli.rpc.ops_files import FilesOps
from zenagent.cli.rpc.ops_plugins import PluginsOps
from zenagent.cli.rpc.ops_session import SessionOps
from zenagent.cli.rpc.ops_taiji import TaijiOps
from zenagent.events import AgentEvent
from zenagent.limits import LimitsWriteError
from zenagent.types import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

ReadLine = Callable[[], Awaitable[str | None]]
WriteLine = Callable[[str], None]

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 2

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
    if role == "user":
        # An injected /skill: turn is displayed as a chip, not as the raw body.
        display = parse_skill_display(_message_text(message))
        if display is not None:
            return {
                "role": "user",
                "text": display.name,
                "skill": True,
                "skill_args": display.args,
                "skill_body": display.body,
            }
    payload: dict = {
        "role": role,
        "text": _message_text(message)[-_PARTIAL_RESULT_CHARS:],
    }
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


def _answers_from_selection(questions: list[dict], raw: object) -> list[dict]:
    """Turn per-question option lists into the ask tool's answer dicts.

    Values that are not one of the question's labels are the card's custom
    text; the tool keeps them in ``custom`` so the model still sees them.
    """
    if not isinstance(raw, list):
        return []
    answers: list[dict] = []
    for index, question in enumerate(questions):
        if index >= len(raw):
            break
        values = raw[index]
        if not isinstance(values, list):
            continue
        labels = [option["label"] for option in question["options"]]
        selected = [
            value for value in values if isinstance(value, str) and value in labels
        ]
        custom = ", ".join(
            value
            for value in values
            if isinstance(value, str) and value not in labels and value.strip()
        )
        if not selected and not custom:
            continue
        answer: dict = {"id": question["id"], "selected": selected}
        if custom:
            answer["custom"] = custom
        answers.append(answer)
    return answers


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
    total = usage_total(agent.messages)
    return {
        "input": total.input,
        "output": total.output,
        "cache_read": total.cache_read,
        "cache_write": total.cache_write,
        "cost": total.cost.total,
    }


class RpcServer(TaijiOps, FilesOps, SessionOps, PluginsOps, ConfigOps):
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
        self._asks: dict[str, asyncio.Future] = {}
        self._ask_seq = 0
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
            args = json.dumps(arguments, ensure_ascii=False, default=str)[:_ARGS_CHARS]
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

    # -- ask round-trip -----------------------------------------------------

    async def ask(self, questions: list[dict]) -> list[dict]:
        """Ask the client the model's structured questions; ``[]`` when dismissed.

        Blocks the tool call exactly like an approval: the read loop keeps
        serving lines while the client renders the card and answers with
        ``{"type": "ask", "answers": [["label", ...] | null, ...]}``.
        """
        request_id = f"q{self._ask_seq}"
        self._ask_seq += 1
        self._send(
            {
                "type": "event",
                "event": {
                    "type": "ask_request",
                    "request_id": request_id,
                    "questions": questions,
                },
            }
        )
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._asks[request_id] = future
        try:
            raw = await future
        except asyncio.CancelledError:
            # The run was aborted while the card was open: drop the stale
            # request instead of leaving a card that answers into the void.
            self._send(
                {
                    "type": "event",
                    "event": {"type": "ask_cancel", "request_id": request_id},
                }
            )
            raise
        finally:
            self._asks.pop(request_id, None)
        return _answers_from_selection(questions, raw)

    def _resolve_ask(self, request: dict) -> bool:
        request_id = request.get("request_id")
        raw = request.get("answers")
        if not isinstance(raw, list) or not all(
            item is None
            or (
                isinstance(item, list) and all(isinstance(value, str) for value in item)
            )
            for item in raw
        ):
            self._send(
                {
                    "type": "error",
                    "id": request.get("id"),
                    "message": "answers must be a list of string lists or null",
                }
            )
            return True
        future = self._asks.get(str(request_id))
        if future is None or future.done():
            self._send(
                {
                    "type": "error",
                    "id": request.get("id"),
                    "message": f"no pending ask: {request_id}",
                }
            )
            return True
        future.set_result(raw)
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
        if kind == "hello":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
            return True
        if kind == "prompt":
            text = request.get("text")
            if not isinstance(text, str) or not text.strip():
                self._send(
                    {"type": "error", "id": request_id, "message": "prompt needs text"}
                )
                return True
            images, image_error = self._read_prompt_images(
                request.get("images"), request_id
            )
            if image_error is not None:
                self._send(image_error)
                return True
            if self.busy:
                self._send({"type": "error", "id": request_id, "message": "busy"})
                return True
            self._text = []
            self._task = asyncio.create_task(self._run_prompt(text, request_id, images))
            return True
        if kind == "cancel":
            # A run may have been started by a retry session op or a queued
            # follow-up, in which case ``_task`` is None but the agent is busy.
            if self.busy:
                self.agent.abort()
                self._send(
                    {"type": "result", "id": request_id, "ok": True, "cancelled": True}
                )
            else:
                self._send(
                    {"type": "result", "id": request_id, "ok": True, "cancelled": False}
                )
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
            self._send(self._transcript_result(request_id, request))
            return True
        if kind == "subagent_transcript":
            call_id = request.get("call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": "subagent_transcript needs call_id",
                    }
                )
                return True
            self._send(self._subagent_transcript_result(request_id, call_id, request))
            return True
        if kind == "model":
            return self._model(request, request_id)
        if kind == "mode":
            return self._mode(request, request_id)
        if kind == "usage":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "usage": _usage(self.agent),
                }
            )
            return True
        if kind == "approval":
            return self._resolve_approval(request)
        if kind == "ask":
            return self._resolve_ask(request)
        if kind == "session":
            await self._session(request, request_id)
            return True
        if kind == "command":
            return await self._command(request, request_id)
        if kind == "fs_list":
            return self._fs_request(request, request_id, "list")
        if kind == "fs_read":
            return self._fs_request(request, request_id, "read")
        if kind == "fs_index":
            await self._fs_index(request_id)
            return True
        if kind == "memory":
            self._memory_request(request, request_id)
            return True
        if kind == "git_worktrees":
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "worktree": worktree_overview(Path.cwd()),
                }
            )
            return True
        if kind == "taiji":
            await self._taiji_request(request, request_id)
            return True
        if kind == "secrets":
            await self._secrets(request, request_id)
            return True
        if kind == "skills":
            await self._skills(request, request_id)
            return True
        if kind == "limits":
            # A refused write (bad value, hand-mangled file) is a normal,
            # reportable outcome — the server must survive it and so must the
            # caller, so nothing here raises past the dispatch.
            try:
                await self._limits(request, request_id)
            except (LimitsWriteError, ValueError) as exc:
                self._send(
                    failure_frame(
                        request_id,
                        code="limits_failed",
                        message="limits failed",
                        exc=exc,
                        op=str(request.get("op") or ""),
                    )
                )
            return True
        if kind == "plugins":
            await self._plugins(request, request_id)
            return True
        if kind == "mcp":
            await self._mcp(request, request_id)
            return True
        if kind == "project":
            await self._project(request, request_id)
            return True
        self._send(
            {"type": "error", "id": request_id, "message": f"unknown type: {kind}"}
        )
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
            payload["context"] = asdict(status.context)
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
            # The complete snapshot (every RunStatus field, nested dataclasses
            # included) so a client never has to guess which keys were
            # remembered. The flat keys above stay for older clients.
            payload["status"] = status.to_wire()
        return payload

    def _steer(self, request: dict, request_id) -> bool:
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            self._send(
                {"type": "error", "id": request_id, "message": "steer needs text"}
            )
            return True
        if self.chat is not None:
            self.chat.steer(text)
        else:
            self.agent.steer(text)
        self._send({"type": "result", "id": request_id, "ok": True})
        return True

    def _read_prompt_images(self, raw, request_id) -> tuple[list | None, dict | None]:
        """Load ``prompt.images`` paths; (blocks, None) or (None, error frame).

        An unreadable attachment's ValueError quotes the absolute path, so it
        becomes a sanitized frame (code + basename) instead of wire text.
        """
        if raw is None:
            return None, None
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            return None, {
                "type": "error",
                "id": request_id,
                "message": "images must be a list of file paths",
            }
        from zenagent.attachments import read_image_argument

        if not self.agent.model.supports_vision:
            return None, {
                "type": "error",
                "id": request_id,
                "message": f"{self.agent.model.id} does not accept images",
            }
        blocks = []
        for path in raw:
            try:
                blocks.append(read_image_argument(Path(path)))
            except ValueError as exc:
                return None, failure_frame(
                    request_id,
                    code="image_read_failed",
                    message="image read failed",
                    exc=exc,
                    op="read",
                    path=path,
                )
        return blocks, None

    async def _run_prompt(self, text: str, request_id, images=None) -> None:
        try:
            if self.chat is not None:
                await self.chat.submit(text, images=images)
                await self.chat.wait_for_idle()
            else:
                await self.agent.prompt(text, images=images)
        except Exception:
            # The raw exception may quote absolute paths or provider payloads;
            # it belongs in the server log, not on the wire (I3).
            logger.exception("prompt failed: request=%s", request_id)
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": "prompt failed",
                    "code": "prompt_failed",
                }
            )
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

    # -- project state -----------------------------------------------------

    async def _shutdown(self) -> None:
        # A client that disconnects mid-approval will never answer: deny the
        # outstanding requests first so the run cannot stall for the full
        # approval timeout during shutdown.
        for future in list(self._approvals.values()):
            if not future.done():
                future.set_result("deny")
        for future in list(self._asks.values()):
            if not future.done():
                future.set_result([])
        if self.running:
            self.agent.abort()
            await asyncio.gather(self._task, return_exceptions=True)
        elif self.agent.is_streaming:
            # A retry/queued run owns a ChatController task, not ``_task``.
            self.agent.abort()
        if self.chat is not None:
            await self.chat.wait_for_idle()
