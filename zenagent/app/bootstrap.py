"""One bootstrap sequence for every frontend.

REPL, TUI, RPC and headless all used to copy the same ritual: build a
permission broker from the user config, build ``RuntimeOptions``, call
``Runtime.bootstrap``, then wire ``ChatController``/``AppServices``/
``build_default_commands``. Four copies had already drifted once (only RPC
forwarded ``read_only`` into the gate). This module is the single sequence:

    broker → Runtime.bootstrap → ChatController → AppServices → commands

Interactive callbacks (``broker.set_confirm``, ask handlers) stay with the
frontend that owns the UI surface — they are attached to the returned
:class:`PreparedFrontend`, never built here. Per-mode differences live in the
caller's ``RuntimeOptions``; headless passes ``views=False`` because a one-shot
run needs no view models.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, CommandRegistry, build_default_commands
from zenagent.app.models import ModelManager
from zenagent.app.permissions import PermissionBroker, build_broker_for
from zenagent.app.run_wiring import attach_run_recorder
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.sessions import SessionManager
from zenagent.config import load_user_config

if TYPE_CHECKING:
    from zenagent.providers.registry import StreamFn

__all__ = ["PreparedFrontend", "prepare"]


@dataclass
class PreparedFrontend:
    """What every frontend needs before it builds its UI surface.

    ``chat``/``services``/``commands`` are ``None`` for headless runs
    (``views=False``): a one-shot prompt subscribes its own output printer
    and never renders view models.
    """

    runtime: Runtime
    broker: PermissionBroker
    chat: ChatController | None = None
    services: AppServices | None = None
    commands: CommandRegistry | None = None


async def prepare(
    options: RuntimeOptions,
    *,
    stream_fn: StreamFn | None = None,
    assume_yes: bool = False,
    allow_missing_key: bool = False,
    views: bool = True,
) -> PreparedFrontend:
    """Broker + bootstrap + (optional) view stack, in one documented order.

    ``read_only``/``allow_*``/``mode`` are read from ``options`` and forwarded
    to both the broker and the runtime — one source, no per-frontend drift.
    Bootstrap warnings stay with the caller (each frontend formats them its
    own way).
    """
    project_cwd = Path(options.root) if options.root is not None else None
    broker = build_broker_for(
        user_config=load_user_config(cwd=project_cwd),
        mode=options.mode or "ask",
        read_only=options.read_only,
        assume_yes=assume_yes,
        allow_dangerous=options.allow_dangerous,
        allow_outside=options.allow_outside,
        allow_destructive=options.allow_destructive,
        allow_protected=options.allow_protected,
        cwd=project_cwd,
    )
    runtime = await Runtime.bootstrap(
        options,
        stream_fn=stream_fn,
        assume_yes=assume_yes,
        gate=broker,
        allow_missing_key=allow_missing_key,
    )
    attach_run_recorder(runtime)
    if not views:
        return PreparedFrontend(runtime=runtime, broker=broker)

    chat = ChatController(runtime)
    services = AppServices(
        runtime=runtime,
        chat=chat,
        models=ModelManager(runtime),
        sessions=SessionManager(runtime),
    )
    commands = build_default_commands(services)
    return PreparedFrontend(
        runtime=runtime, broker=broker, chat=chat, services=services, commands=commands
    )
