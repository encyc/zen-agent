"""model / mode / project / command wire handlers (mixin of RpcServer).

The domain neighbours live in their own mixins: secrets and limits in
``ops_secrets.py``, skills and mcp in ``ops_skills.py``. This module keeps
the model catalog, mode switching, project relocation and the slash-command
dispatch.
"""

from __future__ import annotations

import logging

from zenagent.app.skill_commands import SKILL_COMMAND_PREFIX, build_skill_submission
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.project import (
    project_key,
    project_key_for_session,
    relocate_project,
)

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)

__all__ = ["ConfigOps"]


class ConfigOps:
    """Mixin: see server.py for the composed RpcServer."""

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
            self._send(
                {"type": "error", "id": request_id, "message": "model needs model_id"}
            )
            return True
        if self.models is None:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": "model switching needs a runtime",
                }
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
                {
                    "type": "error",
                    "id": request_id,
                    "message": "mode switching needs a runtime",
                }
            )
            return True
        try:
            applied = self.runtime.set_mode(str(mode or ""))
        except ValueError as exc:
            # Mode validation is authored text, so keep the original result
            # shape. The raw exception (which may quote a path) is logged;
            # the reason is re-rendered from the request instead.
            logger.warning("mode switch refused: %s", exc, exc_info=True)
            from zenagent.i18n import t

            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": False,
                    "reason": t("mode.unknown", mode=str(mode or "")),
                }
            )
            return True
        self._send({"type": "result", "id": request_id, "ok": True, "mode": applied})
        return True

    # -- run ----------------------------------------------------------------

    async def _project(self, request: dict, request_id) -> None:
        op = request.get("op")
        label = str(op or "")
        try:
            if op != "relocate":
                raise Refusal(f"unknown project op: {op}")
            old_root = request.get("from")
            if not isinstance(old_root, str) or not old_root.strip():
                raise Refusal("relocate needs from")
            new_root = request.get("to")
            if not isinstance(new_root, str) or not new_root.strip():
                raise Refusal("relocate needs to")
            if self.runtime is not None and project_key_for_session(
                self.runtime.session.path
            ) == project_key(old_root):
                raise Refusal(
                    "cannot relocate the open project; switch to another project first",
                    code="project_busy",
                )
            relocation = relocate_project(old_root, new_root)
        except Refusal as exc:
            # Authored validation text is safe; the code separates a busy
            # open project from a malformed relocate request.
            self._send(
                error_frame(
                    request_id,
                    str(exc),
                    exc.code or "project_invalid",
                    {"op": label},
                )
            )
            return
        except Exception as exc:  # noqa: BLE001 - a bad project op is a result, not a crash
            self._send(
                failure_frame(
                    request_id,
                    code="project_failed",
                    message="project failed",
                    exc=exc,
                    op=label,
                )
            )
            return
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "old_root": relocation.old_root,
                "new_root": relocation.new_root,
                "old_key": relocation.old_key,
                "new_key": relocation.new_key,
                "sessions": relocation.sessions,
                "moved": relocation.moved,
            }
        )

    # -- sessions and commands ---------------------------------------------

    async def _command(self, request: dict, request_id) -> bool:
        """Dispatch one slash command; False means ``quit`` asked to shut down."""
        name = request.get("name")
        if not isinstance(name, str) or not name.strip():
            self._send(
                {"type": "error", "id": request_id, "message": "command needs name"}
            )
            return True
        if name.startswith(SKILL_COMMAND_PREFIX):
            await self._skill_command(
                name, str(request.get("argument") or ""), request_id
            )
            return True
        if self.commands is None:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": "commands need a runtime",
                }
            )
            return True
        if self.commands.get(name) is None:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown command: {name}",
                }
            )
            return True
        try:
            result = await self.commands.dispatch(
                name, str(request.get("argument") or "")
            )
        except Exception as exc:  # noqa: BLE001 - a command must not kill the server
            self._send(
                failure_frame(
                    request_id,
                    code="command_failed",
                    message="command failed",
                    exc=exc,
                    op="command",
                )
            )
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

    async def _skill_command(self, name: str, argument: str, request_id) -> None:
        """Inject ``/skill:<name>`` as the next user turn, like the TUI does."""
        if self.runtime is None or self.chat is None:
            self._send(
                {"type": "error", "id": request_id, "message": "skills need a runtime"}
            )
            return
        templates = {template.name for template in (self.runtime.templates or [])}
        extensions = set(self.runtime.extension_commands or {})
        resolution = build_skill_submission(
            f"/{name} {argument}".strip(),
            list(self.runtime.skills or []),
            reserved_names={*templates, *extensions},
            max_body_bytes=self.runtime.limits.max_body_bytes,
        )
        if resolution is None:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown command: {name}",
                }
            )
            return
        if resolution.error is not None:
            self._send({"type": "error", "id": request_id, "message": resolution.error})
            return
        submission = resolution.submission
        if submission is None:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown command: {name}",
                }
            )
            return
        await self.chat.submit(submission.prompt)
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "command": name,
                "message": None,
            }
        )
