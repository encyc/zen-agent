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
from zenagent.mcp.builtin import BUILTIN_SERVERS, builtin_row, set_builtin_enabled
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

    def _builtin_mcp_rows(self) -> list[dict]:
        """The builtin servers (definitions pinned by this zen release).

        Separate from ``_mcp_rows`` on purpose: those come from the two
        ``mcp.json`` layers and carry edit/remove affordances. A builtin row has
        only one verb — switch it on or off — and no command line crosses the
        wire, since the user never authored it.
        """
        statuses = {}
        if self.runtime is not None and getattr(self.runtime, "mcp", None) is not None:
            statuses = {item.name: item for item in self.runtime.mcp.status()}
        rows = []
        for name in BUILTIN_SERVERS:
            row = builtin_row(name)
            status = statuses.get(name)
            rows.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "description": row["description"],
                    "version": row["version"],
                    "requires_runtime": row["requires_runtime"] or None,
                    "proxy_tool": row["proxy_tool"],
                    "enabled": row["enabled"],
                    "state": status.state if status is not None else "off",
                    "tools": list(status.tools) if status is not None else [],
                    "error": status.error if status is not None else None,
                }
            )
        return rows

    def _builtin_mcp_rows_safe(self) -> list[dict]:
        """Builtin rows never take the page down: a broken pref is "off"."""
        try:
            return self._builtin_mcp_rows()
        except ValueError as exc:
            logger.warning("builtin mcp rows failed: %s", exc, exc_info=True)
            return []

    def _builtin_connect_failure(self, target: str) -> str | None:
        """Why a just-enabled builtin server is not connected, or ``None``."""
        row = next(
            (item for item in self._builtin_mcp_rows() if item["id"] == target), None
        )
        if row is None or row["state"] == "connected":
            return None
        return f"{row['label']} did not connect: {row['error'] or 'unknown error'}"

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
            "builtin": self._builtin_mcp_rows_safe(),
            "problem": problem,
        }

    async def _mcp_builtin(self, request: dict, request_id) -> None:
        """Switch one builtin server on/off, then re-apply the runtime.

        Enabling only writes the preference: the actual connection happens when
        the manager is rebuilt below, so a failing ``npx`` (no Node, no network)
        reports through the row's ``error`` instead of a half-enabled state. The
        write is reverted when the rebuild cannot connect, because a switch that
        says "on" while nothing is connected is a lie the next session repeats.
        """
        target = request.get("id")
        if not isinstance(target, str) or target not in BUILTIN_SERVERS:
            self._send(
                {"type": "error", "id": request_id, "message": f"unknown builtin MCP server: {target}"}
            )
            return
        enabled = bool(request.get("enabled"))
        try:
            set_builtin_enabled(target, enabled)
        except (ValueError, OSError) as exc:
            self._send(
                failure_frame(
                    request_id,
                    code="mcp_failed",
                    message="mcp failed",
                    exc=exc,
                    op="builtin",
                )
            )
            return

        # Both directions re-assemble: disabling must drop the live connection
        # too, or the row would keep saying "connected" for a server the user
        # just switched off. ``_apply_or_defer`` lives on the plugin mixin; the
        # composed host always has it, a bare SkillsOps in a test may not.
        apply_now = getattr(self, "_apply_or_defer", None)
        pending = False if apply_now is None else not await apply_now()
        failure = self._builtin_connect_failure(target) if enabled and not pending else None
        if failure is not None:
            # Enabling wrote a preference the runtime could not honour; leaving
            # it on would make the next session repeat a false claim.
            set_builtin_enabled(target, False)
            self._send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": True,
                    "servers": self._mcp_rows(),
                    "builtin": self._builtin_mcp_rows(),
                    "problem": failure,
                    "apply_pending": False,
                }
            )
            return
        self._send(
            {
                "type": "result",
                "id": request_id,
                "ok": True,
                "servers": self._mcp_rows(),
                "builtin": self._builtin_mcp_rows(),
                "problem": None,
                "apply_pending": pending,
            }
        )

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

        if op == "builtin":
            await self._mcp_builtin(request, request_id)
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
