"""secrets / limits wire handlers (mixin of RpcServer).

Key storage never echoes a secret back over the wire: ``_secrets_result``
sends provider rows with configured/source flags only, and the ``test`` op
reports connect/disconnect, not the key. Limits are the prompt/loop budget
knobs: the handlers rewrite the managed ``[limits]`` tables and push the
fresh file into the running session.
"""

from __future__ import annotations

import contextlib

from zenagent import settings
from zenagent.cli.rpc.errors import failure_frame
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

__all__ = ["SecretsOps"]


class SecretsOps:
    """Mixin: the secrets/limits ops of the composed RpcServer."""

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

    # -- limits (prompt/loop budgets) ---------------------------------------

    @staticmethod
    def _knob_value(runtime, knob) -> object:
        """The value this build of the session is actually running with.

        ``[limits]`` knobs come from the live runtime's config; ``[task]``,
        ``[zen]`` and ``[swarm]`` budgets are read when a subagent, fleet or
        room starts, so the *file* is what the next one will see — a stale
        in-memory copy must not win.
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
                "choices": list(knob.choices),
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
            "project_path": str(project_config_path(self.root)),
            "groups": list(GROUP_ORDER),
            "knobs": knobs,
        }

    async def _adopt_limits(self) -> None:
        """Push the rewritten file into the running session."""
        runtime = self.runtime
        if runtime is None:
            await self._drop_idle_swarm_rooms()
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
        runtime.user_config.zen_max_concurrent = fresh.zen_max_concurrent
        runtime.user_config.zen_batch_window_s = fresh.zen_batch_window_s
        runtime.user_config.zen_report_limit = fresh.zen_report_limit
        runtime.user_config.zen_worker_mode = fresh.zen_worker_mode
        runtime.user_config.swarm_max_concurrent = fresh.swarm_max_concurrent
        runtime.user_config.swarm_max_chain_depth = fresh.swarm_max_chain_depth
        runtime.user_config.swarm_max_chain_cost = fresh.swarm_max_chain_cost
        runtime.user_config.swarm_runtime_idle_s = fresh.swarm_runtime_idle_s
        runtime.apply_limits()
        await self._drop_idle_swarm_rooms()

    async def _drop_idle_swarm_rooms(self) -> None:
        """Close idle rooms so the next request rebuilds with the new policy.

        A room with a run in flight keeps its manager (its semaphore and router
        cannot be resized safely mid-run); the change reaches it on the next
        rebuild, same as the settings page explains.
        """
        rooms = getattr(self, "_swarm", None)
        if not rooms:
            return
        for key, manager in list(rooms.items()):
            if manager.has_active():
                continue
            rooms.pop(key, None)
            with contextlib.suppress(Exception):
                await manager.close()

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
            await self._adopt_limits()
        except ValueError as exc:
            # The rewrite succeeded but the file no longer parses elsewhere:
            # report the config error; the next session will fail the same way.
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
