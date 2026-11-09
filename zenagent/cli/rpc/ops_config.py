"""model / mode / secrets / skills / mcp / project wire handlers (mixin of RpcServer)."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

from zenagent import settings
from zenagent.app.skill_commands import SKILL_COMMAND_PREFIX, build_skill_submission
from zenagent.cli.rpc.errors import Refusal, error_frame, failure_frame
from zenagent.config import config_path as global_config_path
from zenagent.config import load_user_config
from zenagent.limits import (
    ALL_KNOBS,
    GROUP_ORDER,
    KNOB_BY_KEY,
    LimitsWriteError,
    coerce_value,
    read_managed,
    write_managed,
)
from zenagent.mcp.config import load_mcp_layers, remove_mcp_server, save_mcp_server
from zenagent.project import (
    project_key,
    project_key_for_session,
    relocate_project,
)
from zenagent.skills import (
    MAX_SKILL_BODY_BYTES,
    default_skill_roots,
    discover_skills,
)

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)


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

    # -- secrets ------------------------------------------------------------

    def _model_catalog(self) -> dict:
        catalog = getattr(self.runtime, "catalog", None)
        return catalog if isinstance(catalog, dict) else {}

    def _known_secret_envs(self) -> set[str]:
        from zenagent.tools.web.providers import KEY_ENVS

        return {
            provider.api_key_env for provider in self._model_catalog().values()
        } | set(KEY_ENVS.values())

    def _secrets_result(self, request_id) -> dict:
        """Provider rows for the settings screen; key values never leave here."""
        from zenagent.tools.web.providers import KEY_ENVS, LABELS

        stored = settings.describe()["keys"]
        current = getattr(getattr(self.runtime, "model", None), "provider", None)
        if current is None:
            current = getattr(self.agent.model, "provider", None)

        model_rows = []
        for provider in sorted(
            self._model_catalog().values(), key=lambda item: item.name.lower()
        ):
            key = settings.get_api_key(provider.api_key_env)
            model_rows.append(
                {
                    "id": provider.id,
                    "name": provider.name,
                    "env": provider.api_key_env,
                    "models": len(provider.models),
                    "configured": bool(key),
                    "source": (
                        "stored"
                        if provider.api_key_env in stored
                        else ("env" if key else None)
                    ),
                    "current": provider.id == current,
                }
            )

        search_rows = []
        for provider_id, env in KEY_ENVS.items():
            key = settings.get_api_key(env)
            search_rows.append(
                {
                    "id": provider_id,
                    "name": LABELS.get(provider_id, provider_id),
                    "env": env,
                    "configured": bool(key),
                    "source": "stored" if env in stored else ("env" if key else None),
                }
            )
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "model_providers": model_rows,
            "search_providers": search_rows,
        }

    async def _secrets(self, request: dict, request_id) -> None:
        op = request.get("op")
        if op == "list":
            self._send(self._secrets_result(request_id))
            return
        if op in ("set", "clear"):
            name = request.get("name")
            if not isinstance(name, str) or name not in self._known_secret_envs():
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"unknown key: {name}",
                    }
                )
                return
            if op == "set":
                value = request.get("value")
                if not isinstance(value, str) or not value.strip():
                    self._send(
                        {
                            "type": "error",
                            "id": request_id,
                            "message": f"set needs a value for {name}",
                        }
                    )
                    return
                settings.set_api_key(name, value)
            else:
                settings.set_api_key(name, "")
            self._reload_key(name)
            self._send({"type": "result", "id": request_id, "ok": True})
            return
        if op == "test":
            await self._secrets_test(request, request_id)
            return
        self._send(
            {"type": "error", "id": request_id, "message": f"unknown secrets op: {op}"}
        )

    def _reload_key(self, name: str) -> None:
        """Adopt a set/cleared key in the live agent when it drives the model."""
        runtime = self.runtime
        if runtime is None:
            return
        current = getattr(runtime, "model", None)
        provider = (
            self._model_catalog().get(current.provider) if current is not None else None
        )
        if provider is None or provider.api_key_env != name:
            return
        if not runtime.reload_api_key():
            runtime.agent.api_key = ""
            runtime.missing_api_key = name

    async def _secrets_test(self, request: dict, request_id) -> None:
        from zenagent.app import probes
        from zenagent.tools.web.providers import PROVIDERS

        scope = request.get("scope")
        target = request.get("target")
        if not isinstance(target, str) or not target:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": "secrets test needs a target",
                }
            )
            return
        if scope == "model":
            if target not in self._model_catalog():
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"unknown model provider: {target}",
                    }
                )
                return
            result = await probes.probe_model_provider(self._model_catalog(), target)
        elif scope == "search":
            if target not in PROVIDERS:
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"unknown search provider: {target}",
                    }
                )
                return
            result = await probes.probe_search_provider(target)
        else:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown secrets scope: {scope}",
                }
            )
            return
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "connected": result.connected,
                "message": result.message,
            }
        )

    # -- skills -------------------------------------------------------------

    def _discover(self):
        roots = default_skill_roots(Path.cwd())
        skills, warnings = discover_skills(roots)
        return roots, skills, warnings

    @staticmethod
    def _skill_rows(roots, skills) -> list[dict]:
        project_root = Path(roots[0]).expanduser() if roots else None
        disabled = settings.disabled_skills()
        rows = []
        for skill in sorted(skills, key=lambda item: item.name):
            source = "global"
            if project_root is not None:
                try:
                    skill.path.relative_to(project_root)
                    source = "project"
                except ValueError:
                    pass
            rows.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "path": str(skill.path),
                    "source": source,
                    "enabled": skill.name not in disabled,
                }
            )
        return rows

    def _skills_result(self, request_id, roots, skills, warnings, notes=()) -> dict:
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "skills": self._skill_rows(roots, skills),
            "warnings": list(warnings),
            "notes": list(notes),
        }

    async def _skills(self, request: dict, request_id) -> None:
        op = request.get("op")
        roots, skills, warnings = self._discover()
        if op == "list":
            self._send(self._skills_result(request_id, roots, skills, warnings))
            return
        if op == "rescan":
            notes = []
            if self.runtime is not None and getattr(
                self.runtime, "skills_enabled", False
            ):
                notes = self.runtime.refresh_skills()
            self._send(self._skills_result(request_id, roots, skills, warnings, notes))
            return
        if op not in ("read", "toggle"):
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown skills op: {op}",
                }
            )
            return

        name = request.get("name")
        skill = next((item for item in skills if item.name == name), None)
        if skill is None:
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown skill: {name}",
                }
            )
            return
        if op == "read":
            body = skill.body
            encoded = body.encode("utf-8")
            truncated = len(encoded) > MAX_SKILL_BODY_BYTES
            if truncated:
                body = (
                    encoded[:MAX_SKILL_BODY_BYTES].decode("utf-8", "replace")
                    + "\n[... skill truncated ...]"
                )
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "name": skill.name,
                    "path": str(skill.path),
                    "body": body,
                    "truncated": truncated,
                }
            )
            return
        if op == "toggle":
            enabled = request.get("enabled")
            if not isinstance(enabled, bool):
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": "toggle needs a boolean enabled",
                    }
                )
                return
            disabled = settings.disabled_skills()
            if enabled:
                disabled.discard(skill.name)
            else:
                disabled.add(skill.name)
            settings.set_disabled_skills(disabled)
            notes = []
            if self.runtime is not None and getattr(
                self.runtime, "skills_enabled", False
            ):
                notes = self.runtime.refresh_skills()
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "name": skill.name,
                    "enabled": enabled,
                    "notes": notes,
                }
            )
            return

    # -- mcp ----------------------------------------------------------------

    def _mcp_rows(self) -> list[dict]:
        global_layer, project_layer = load_mcp_layers(cwd=Path.cwd())
        statuses = {}
        if self.runtime is not None and getattr(self.runtime, "mcp", None) is not None:
            statuses = {item.name: item for item in self.runtime.mcp.status()}
        rows = []
        for name in sorted(set(global_layer) | set(project_layer)):
            source = "project" if name in project_layer else "global"
            config = project_layer.get(name) or global_layer[name]
            status = statuses.get(name)
            rows.append(
                {
                    "name": name,
                    "transport": config.transport_kind,
                    "command": " ".join(config.command) if config.command else None,
                    "url": config.url,
                    "env_keys": sorted(config.env),
                    "header_keys": sorted(config.headers),
                    "source": source,
                    "overrides_global": source == "project" and name in global_layer,
                    "state": status.state if status is not None else "off",
                    "tools": list(status.tools) if status is not None else [],
                    "error": status.error if status is not None else None,
                }
            )
        return rows

    def _mcp_result(self, request_id) -> dict:
        try:
            servers = self._mcp_rows()
            problem = None
        except ValueError as exc:
            # A hand-mangled layer file names its absolute path in the parser
            # error; the log keeps it, the wire only says the read failed.
            logger.warning("mcp config read failed: %s", exc, exc_info=True)
            servers = []
            problem = "mcp config could not be read"
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "servers": servers,
            "problem": problem,
        }

    @staticmethod
    def _mcp_layer(request: dict) -> str:
        layer = request.get("layer", "global")
        if layer not in ("global", "project"):
            raise ValueError(f"unknown MCP layer: {layer}")
        return layer

    @staticmethod
    def _string_map(value, label: str) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ValueError(f"{label} must map strings to strings")
        return dict(value)

    def _effective_mcp(self, name: str):
        global_layer, project_layer = load_mcp_layers(cwd=Path.cwd())
        return project_layer.get(name) or global_layer.get(name)

    # -- limits (prompt/loop budgets) ---------------------------------------

    @staticmethod
    def _knob_value(runtime, knob) -> object:
        """The value this build of the session is actually running with.

        ``[limits]`` knobs come from the live runtime's config; ``[task]``
        budgets reach subagents at spawn time, so the *file* is what the next
        subagent will see — a stale in-memory copy must not win.
        """
        config = getattr(runtime, "user_config", None) or load_user_config()
        if knob.table.startswith("limits"):
            return config.limits.value(knob.attr)
        fresh = load_user_config(global_config_path())
        return getattr(fresh, knob.attr)

    def _limits_result(self, request_id) -> dict:
        from zenagent.project import project_config_path

        runtime = self.runtime
        overrides = runtime.limit_overrides() if runtime is not None else {}
        explicit = {
            f"{table}.{name}"
            for table, rows in read_managed(global_config_path()).items()
            for name in rows
        }
        knobs = [
            {
                "key": knob.key,
                "section": knob.section,
                "table": knob.table,
                "name": knob.name,
                "kind": knob.kind,
                "nullable": knob.nullable,
                "min": knob.minimum,
                "max": knob.maximum,
                "default": knob.default,
                "unit": knob.unit,
                "apply": knob.apply,
                "value": self._knob_value(runtime, knob),
                "explicit": knob.key in explicit,
                "override": overrides.get(knob.attr),
            }
            for knob in ALL_KNOBS
        ]
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "path": str(global_config_path()),
            "project_path": str(project_config_path(Path.cwd())),
            "groups": list(GROUP_ORDER),
            "knobs": knobs,
        }

    def _adopt_limits(self) -> None:
        """Push the rewritten file into the running session."""
        runtime = self.runtime
        if runtime is None:
            return
        fresh = load_user_config(global_config_path())
        runtime.user_config.limits = fresh.limits
        runtime.user_config.task_max_turns = fresh.task_max_turns
        runtime.user_config.task_timeout_s = fresh.task_timeout_s
        runtime.user_config.task_max_cost = fresh.task_max_cost
        runtime.user_config.task_keep_transcripts = fresh.task_keep_transcripts
        runtime.user_config.task_max_transcript_bytes = fresh.task_max_transcript_bytes
        runtime.user_config.task_max_transcript_file_bytes = (
            fresh.task_max_transcript_file_bytes
        )
        runtime.apply_limits()

    async def _limits(self, request: dict, request_id) -> None:
        op = request.get("op")
        if op == "get":
            self._send(self._limits_result(request_id))
            return
        if op not in ("set", "reset"):
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"unknown limits op: {op}",
                }
            )
            return
        key = request.get("key")
        knob = KNOB_BY_KEY.get(key) if isinstance(key, str) else None
        if knob is None:
            self._send(
                {"type": "error", "id": request_id, "message": f"unknown limit: {key}"}
            )
            return
        try:
            # Always start from the file: the page may hold a stale snapshot.
            tables = read_managed(global_config_path())
            rows = tables.setdefault(knob.table, {})
            if op == "set":
                rows[knob.name] = coerce_value(
                    knob, request.get("value"), global_config_path()
                )
            else:
                rows.pop(knob.name, None)
            if not rows:
                tables.pop(knob.table, None)
            write_managed(global_config_path(), tables)
        except LimitsWriteError as exc:
            # Unknown keys / unreadable file: refuse, never rewrite. The
            # exception quotes the absolute config path; it stays in the log.
            self._send(
                failure_frame(
                    request_id,
                    code="limits_failed",
                    message="limits failed",
                    exc=exc,
                    op=str(op),
                )
            )
            return
        try:
            self._adopt_limits()
        except ValueError as exc:
            # The rewrite succeeded; adopting it failed because the file as a
            # whole does not parse (hand edit elsewhere). Report the config
            # error — the next session will fail the same way, loudly.
            self._send(
                failure_frame(
                    request_id,
                    code="config_failed",
                    message="config failed",
                    exc=exc,
                    op=str(op),
                )
            )
            return
        self._send(self._limits_result(request_id))

    async def _mcp(self, request: dict, request_id) -> None:
        op = request.get("op")
        if op == "list":
            self._send(self._mcp_result(request_id))
            return

        name = request.get("name")
        if not isinstance(name, str) or not name.strip():
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"mcp {op} needs a name",
                }
            )
            return
        name = name.strip()

        if op == "add":
            try:
                layer = self._mcp_layer(request)
                command_text = request.get("command")
                raw_url = request.get("url")
                has_command = isinstance(command_text, str) and bool(
                    command_text.strip()
                )
                has_url = isinstance(raw_url, str) and bool(raw_url.strip())
                if has_command and has_url:
                    raise ValueError("an MCP server cannot have both command and url")
                transport = request.get("transport") or ("http" if has_url else "stdio")
                command = url = None
                if transport == "stdio":
                    if not has_command:
                        raise ValueError("stdio MCP server needs a command")
                    command = shlex.split(command_text)
                elif transport == "http":
                    if not has_url:
                        raise ValueError("http MCP server needs a url")
                    url = raw_url.strip()
                else:
                    raise ValueError(f"unknown MCP transport: {transport}")
                save_mcp_server(
                    name,
                    command=command,
                    url=url,
                    env=self._string_map(request.get("env"), "env"),
                    headers=self._string_map(request.get("headers"), "headers"),
                    layer=layer,
                    cwd=Path.cwd(),
                )
            except (ValueError, OSError) as exc:
                self._send(
                    failure_frame(
                        request_id,
                        code="mcp_failed",
                        message="mcp failed",
                        exc=exc,
                        op=str(op or ""),
                    )
                )
                return
            self._send(self._mcp_result(request_id))
            return

        if op == "remove":
            try:
                remove_mcp_server(name, layer=self._mcp_layer(request), cwd=Path.cwd())
            except (ValueError, OSError) as exc:
                self._send(
                    failure_frame(
                        request_id,
                        code="mcp_failed",
                        message="mcp failed",
                        exc=exc,
                        op=str(op or ""),
                    )
                )
                return
            self._send(self._mcp_result(request_id))
            return

        if op == "test":
            from zenagent.app import probes

            try:
                config = self._effective_mcp(name)
            except ValueError as exc:
                self._send(
                    failure_frame(
                        request_id,
                        code="mcp_failed",
                        message="mcp failed",
                        exc=exc,
                        op=str(op or ""),
                    )
                )
                return
            if config is None:
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"unknown MCP server: {name}",
                    }
                )
                return
            result = await probes.probe_mcp_server(config)
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "connected": result.connected,
                    "tools": list(result.tools),
                    "message": result.message,
                }
            )
            return

        self._send(
            {"type": "error", "id": request_id, "message": f"unknown mcp op: {op}"}
        )

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
            self._send(
                failure_frame(
                    request_id,
                    code="mode_failed",
                    message="mode failed",
                    exc=exc,
                    op="mode",
                )
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
                    "cannot relocate the open project; switch to another project first"
                )
            relocation = relocate_project(old_root, new_root)
        except Refusal as exc:
            # Authored validation text is safe; only the frame gets a code.
            self._send(error_frame(request_id, str(exc), "project_failed", {"op": label}))
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
