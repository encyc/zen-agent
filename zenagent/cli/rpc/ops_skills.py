"""skills / mcp wire handlers (mixin of RpcServer).

Skills come from the discovered roots (``_discover``) and toggling writes the
disabled set, never a copy of the skill. MCP servers live in the global or
project layer files; the handlers edit those files and report rows, never
the credentials.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

from zenagent import settings
from zenagent.cli.rpc.errors import failure_frame
from zenagent.mcp.config import load_mcp_layers, remove_mcp_server, save_mcp_server
from zenagent.skills import (
    MAX_SKILL_BODY_BYTES,
    default_skill_roots,
    discover_skills,
)

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)

__all__ = ["SkillsOps"]


class SkillsOps:
    """Mixin: the skills/mcp ops of the composed RpcServer."""

    # -- skills -------------------------------------------------------------

    def _discover(self):
        roots = default_skill_roots(self.root)
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
        global_layer, project_layer = load_mcp_layers(cwd=self.root)
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
        global_layer, project_layer = load_mcp_layers(cwd=self.root)
        return project_layer.get(name) or global_layer.get(name)

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
                    cwd=self.root,
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
                remove_mcp_server(name, layer=self._mcp_layer(request), cwd=self.root)
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
