"""The ``plugins`` wire handlers (mixin of RpcServer).

Rows never carry a credential value, an env value or a URL filled from one:
only keys, labels and whether each credential is set. Writes re-apply the
live runtime, deferring when a turn is streaming.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from zenagent.cli.rpc.errors import failure_frame
from zenagent.plugins.catalog import builtin_catalog, install_builtin
from zenagent.plugins.discovery import discover_plugins
from zenagent.plugins.install import install_plugin, uninstall_plugin
from zenagent.plugins.state import (
    credentials_for,
    is_enabled,
    set_credentials,
    set_enabled,
)

#: Server-side diagnostics; raw exception detail never crosses the wire.
logger = logging.getLogger(__name__)


def _status(enabled: bool, missing: list[str], servers: list[dict]) -> str:
    if not enabled:
        return "disabled"
    if missing:
        return "needs_credentials"
    states = {row["state"] for row in servers}
    if "failed" in states:
        return "failed"
    if "connected" in states:
        return "connected"
    return "ready"


class PluginsOps:
    """Mixin: see server.py for the composed RpcServer."""

    def _plugins_rows(self) -> list[dict]:
        manifests, _warnings = discover_plugins()
        statuses = {}
        if self.runtime is not None and getattr(self.runtime, "mcp", None) is not None:
            statuses = {item.name: item for item in self.runtime.mcp.status()}
        rows = []
        for manifest in manifests:
            enabled = is_enabled(manifest.id)
            credentials = credentials_for(manifest.id)
            missing = [
                spec.key
                for spec in manifest.credentials
                if not credentials.get(spec.key)
            ]
            servers = []
            for key in manifest.mcp_servers:
                name = f"{manifest.id}.{key}"
                status = statuses.get(name)
                servers.append(
                    {
                        "name": name,
                        "state": status.state if status is not None else "off",
                        "tools": list(status.tools) if status is not None else [],
                        "error": status.error if status is not None else None,
                    }
                )
            rows.append(
                {
                    "id": manifest.id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "enabled": enabled,
                    "status": _status(enabled, missing, servers),
                    "credentials": [
                        {
                            "key": spec.key,
                            "label": spec.label,
                            "secret": spec.secret,
                            "help": spec.help,
                            "url": spec.url,
                            "set": bool(credentials.get(spec.key)),
                        }
                        for spec in manifest.credentials
                    ],
                    "servers": servers,
                }
            )
        return rows

    def _catalog_rows(self) -> list[dict]:
        installed = {manifest.id for manifest in discover_plugins()[0]}
        return [
            {
                "id": entry.id,
                "name": entry.name,
                "description": entry.description,
                "requires_runtime": entry.requires_runtime,
                "installed": entry.id in installed,
            }
            for entry in builtin_catalog()
        ]

    def _plugins_result(self, request_id, *, apply_pending: bool = False) -> dict:
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "plugins": self._plugins_rows(),
            "catalog": self._catalog_rows(),
            "problem": None,
            "apply_pending": apply_pending,
        }

    async def _apply_or_defer(self) -> bool:
        try:
            await self.runtime.reconfigure_plugins()
        except RuntimeError:
            task = asyncio.create_task(self._apply_when_idle())
            tasks = self.__dict__.setdefault("_plugin_tasks", set())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return False
        return True

    async def _apply_when_idle(self) -> None:
        if getattr(self, "_closed", False):
            return
        try:
            agent = getattr(self.runtime, "agent", None)
            while agent is not None and agent.is_streaming:
                await asyncio.sleep(0.5)
            if getattr(self, "_closed", False):
                return
            await self.runtime.reconfigure_plugins()
        except Exception as exc:
            logger.warning(
                "plugins deferred apply failed: %s",
                exc,
                exc_info=True,
            )

    async def _plugins(self, request: dict, request_id) -> None:
        op = request.get("op")
        if op == "list":
            self._send(self._plugins_result(request_id))
            return

        if op == "install":
            await self._install(request, request_id)
            return

        name = request.get("plugin")
        if not isinstance(name, str) or not name.strip():
            self._send(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"plugins {op} needs an id",
                }
            )
            return
        name = name.strip()

        try:
            self._require_runtime()
            if op == "uninstall":
                uninstall_plugin(name)
            elif op == "enable":
                set_enabled(name, True)
            elif op == "disable":
                set_enabled(name, False)
            elif op == "configure":
                set_credentials(name, self._plugin_values(request.get("values")))
            else:
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"unknown plugins op: {op}",
                    }
                )
                return
        except (ValueError, OSError) as exc:
            self._send(
                failure_frame(
                    request_id,
                    code="plugin_failed",
                    message="plugin failed",
                    exc=exc,
                    op=str(op or ""),
                )
            )
            return
        await self._finish_write(request_id)

    async def _install(self, request: dict, request_id) -> None:
        path = request.get("path")
        name = request.get("plugin")
        try:
            self._require_runtime()
            if isinstance(path, str) and path.strip():
                install_plugin(Path(path.strip()))
            elif isinstance(name, str) and name.strip():
                install_builtin(name.strip())
            else:
                self._send(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": "plugins install needs an id or path",
                    }
                )
                return
        except (ValueError, OSError) as exc:
            self._send(
                failure_frame(
                    request_id,
                    code="plugin_failed",
                    message="plugin failed",
                    exc=exc,
                    op="install",
                )
            )
            return
        await self._finish_write(request_id)

    async def _finish_write(self, request_id) -> None:
        applied = await self._apply_or_defer()
        self._send(self._plugins_result(request_id, apply_pending=not applied))

    def _require_runtime(self) -> None:
        if self.runtime is None:
            raise ValueError("plugins need a running session")

    @staticmethod
    def _plugin_values(value) -> dict[str, str]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ValueError("plugins configure values must map strings to strings")
        return dict(value)
