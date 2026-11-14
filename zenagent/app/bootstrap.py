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

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from zenagent import project
from zenagent.agent import Agent
from zenagent.agent.subagents import SubagentLimits
from zenagent.app import plugin_wiring, skills_recompose, subagent_runs
from zenagent.app.chat import ChatController
from zenagent.app.commands import AppServices, CommandRegistry, build_default_commands
from zenagent.app.journal import attach_journal, journal_for, load_backup_policy
from zenagent.app.metrics import MetricsSession
from zenagent.app.models import ModelManager
from zenagent.app.modes import resolve_mode
from zenagent.app.permissions import (
    PermissionBroker,
    PermissionGate,
    build_broker_for,
    build_permission_gate,
)
from zenagent.app.run_wiring import attach_run_recorder
from zenagent.app.runtime import Runtime, RuntimeOptions
from zenagent.app.safety import GuardPolicy
from zenagent.app.session_meta import (
    SessionMetaRecorder,
    make_retry_recorder,
    mode_record,
)
from zenagent.app.sessions import SessionManager
from zenagent.app.support import (
    api_key_for,
    resolve_model,
    resolve_task_model,
    session_id_for,
)
from zenagent.app.views import last_request_usage
from zenagent.config import (
    ContextGuard,
    apply_user_config,
    load_user_config,
)
from zenagent.extensions import load_extensions
from zenagent.i18n import t
from zenagent.providers.catalog import load_catalog
from zenagent.skills import default_skill_roots, discover_skills
from zenagent.templates import default_template_roots, discover_templates
from zenagent.tools import base_tools
from zenagent.tools.task import build_task_tool
from zenagent.tools.todo import build_todo_tool
from zenagent.trace import TraceRecorder

if TYPE_CHECKING:
    from zenagent.providers.registry import StreamFn

__all__ = ["PreparedFrontend", "assemble", "prepare"]


async def assemble(
    runtime: Runtime,
    options: RuntimeOptions,
    *,
    stream_fn: StreamFn | None = None,
    assume_yes: bool = False,
    gate: PermissionGate | None = None,
    allow_missing_key: bool = False,
) -> None:
    """Assemble one runtime from ``options`` (the body of ``Runtime.bootstrap``).

    Moved here from the god class (audit finding 9, slice 1): the sequence —
    root/config/catalog/model → gate → session → plugins/skills/templates/
    extensions → recompose → Agent → subscribers — is the single assembly
    every frontend inherits. A failure anywhere after the session opened rolls
    the partial runtime down and re-raises untouched.
    """
    requested = (
        Path(options.root).expanduser()
        if options.root is not None
        else Path.cwd()
    )
    runtime.cwd = requested.resolve()
    if not runtime.cwd.is_dir():
        raise RuntimeError(f"project root is not a directory: {runtime.cwd}")
    runtime.root = project.find_project_root(runtime.cwd)
    runtime.profile = options.profile
    runtime.user_config = load_user_config(cwd=runtime.root)
    runtime.catalog = apply_user_config(
        load_catalog(), runtime.user_config, runtime.warnings
    )
    model = resolve_model(
        runtime.catalog, options.model_id or runtime.user_config.default_model
    )
    runtime.model = model
    api_key = options.api_key or api_key_for(runtime.catalog, model)
    if not api_key:
        provider = runtime.catalog.get(model.provider)
        env_name = provider.api_key_env if provider else "the provider's API key"
        if not allow_missing_key:
            raise RuntimeError(
                t("runtime.missing_key", model=model.id, env=env_name)
            )
        # The TUI is a workbench, not a one-shot run: open it even without a
        # key and let the user paste one from /settings instead of refusing
        # to start. reload_api_key() adopts it for the next turn.
        runtime.missing_api_key = env_name
        api_key = ""

    mode = resolve_mode(
        explicit=options.mode,
        assume_yes=assume_yes,
        read_only=options.read_only,
    )
    runtime._mode = mode

    policy = GuardPolicy.from_config(
        runtime.user_config,
        cwd=runtime.root,
        allow_outside=options.allow_outside,
        allow_destructive=options.allow_destructive,
        allow_protected=options.allow_protected,
    )
    if gate is None:
        gate = build_permission_gate(
            assume_yes=assume_yes,
            interactive=False,
            allow_dangerous=options.allow_dangerous,
            allow=runtime.user_config.tool_allow,
            deny=runtime.user_config.tool_deny,
            mode=mode,
            read_only=options.read_only,
            policy=policy,
        )
    elif hasattr(gate, "set_mode"):
        gate.set_mode(mode)
    runtime.gate = gate

    # Open the session before discovery/recompose: the todo tool is
    # per-session state bound to the sidecar next to the JSONL, and the
    # session setter also builds the artifact spill sink next to it.
    runtime.session = runtime._open_session(options.resume)
    try:
        runtime.journal = journal_for(
            runtime.session.path, load_backup_policy(runtime.user_config)
        )
        runtime.todo_tool = build_todo_tool(runtime.session.path)
        # Refcount the process-wide background-job manager: closing one Runtime
        # must not kill another live Runtime's jobs (last close wins). The todo
        # tool is added by _recompose, which now owns the tool list.
        from zenagent.tools.jobs import acquire_job_manager

        runtime._jobs_acquired = True
        acquire_job_manager()
        # Same refcount for the shared LSP clients: several resident slots of
        # one host may sit on the same root and share one language server.
        from zenagent.tools.lsp import acquire_clients

        runtime._lsp_acquired = True
        acquire_clients()
        assembly = None
        if options.tools is None and runtime.profile.plugins:
            assembly = await plugin_wiring.start_plugins(runtime, options)

        if runtime.profile.skills and options.tools is None:
            skills, warnings = discover_skills(
                [*default_skill_roots(runtime.root), *(assembly.skill_roots if assembly else [])]
            )
            runtime.skills = skills_recompose.active_skills(skills)
            runtime.warnings.extend(f"skills {warning}" for warning in warnings)

        if runtime.profile.templates:
            runtime.templates, warnings = discover_templates(
                default_template_roots(runtime.root)
            )
            runtime.warnings.extend(f"templates {warning}" for warning in warnings)

        runtime.extension_files = [
            *runtime.user_config.extensions,
            *options.extension_paths,
            *(assembly.extension_files if assembly else []),
        ]
        if runtime.extension_files:
            loaded = load_extensions(runtime.extension_files)
            runtime.warnings.extend(
                f"extension {warning}" for warning in loaded.warnings
            )
            runtime._extension_tools = list(loaded.tools)
            runtime.extension_commands = dict(loaded.commands)
            runtime.hooks = loaded.hooks
            runtime._extension_prompt = loaded.prompt_text

        runtime._options = options
        runtime.auto_compact = options.auto_compact
        runtime._custom_tools = (
            list(options.tools) if options.tools is not None else None
        )
        runtime.guard = ContextGuard(model.context_window)
        thinking = options.thinking
        if thinking is None:
            thinking = "high" if model.reasoning else "off"
        # The task tool spawns child agents; the child inherits the parent's
        # model/stream, follows the session artifact sink, and mirrors the
        # parent's live tool set and permission mode at each dispatch.
        runtime.task_tool = build_task_tool(
            model=lambda: runtime.agent.model,
            base_tools=base_tools(runtime.ask_tool, cwd=runtime.cwd),
            get_observer=lambda: runtime.subagent_observer,
            run_sink=subagent_runs.SubagentRunSink(runtime),
            api_key=lambda: runtime.agent.api_key,
            stream_fn=stream_fn,
            thinking=lambda: runtime.agent.thinking,
            live_tools=lambda: runtime.tool_registry.active(),
            mode=lambda: runtime.mode,
            resolve_model=lambda raw: resolve_task_model(runtime.catalog, raw),
            max_cost=(
                runtime.user_config.task_max_cost
                if runtime.user_config.task_max_cost is not None
                else options.max_cost
            ),
            # [task]: the child's budgets and transcript retention caps.
            max_turns=runtime.user_config.task_max_turns,
            timeout_s=runtime.user_config.task_timeout_s,
            limits=SubagentLimits.from_user_config(runtime.user_config),
            session_path=runtime.session.path,
            artifact_sink=lambda tool_name, text: (
                runtime.artifact_sink(tool_name, text)
                if runtime.artifact_sink is not None
                else None
            ),
            # The child is not a second user: its tool calls go through the
            # same gate as the parent's, so deny/catastrophic-command policy
            # holds inside subagents.
            before_tool_call=gate.before_tool_call,
        )
        runtime._recompose()
        prompt = runtime._prompt_with_mode()

        agent = Agent(
            model=model,
            system_prompt=prompt,
            tools=runtime._base_tools,
            api_key=api_key,
            thinking=thinking,
            session_id=session_id_for(model),
            stream_fn=stream_fn,
            before_tool_call=gate.before_tool_call,
            transform_context=runtime._transform_context,
            max_turns=(
                options.max_turns
                if options.max_turns is not None
                else runtime.limits.max_turns
            ),
            max_cost=options.max_cost,
            loop_guard_threshold=runtime.limits.loop_guard_threshold,
            max_result_bytes=runtime.limits.max_result_bytes,
            retries=options.retries,
            artifact_sink=runtime.artifact_sink,
        )
        agent.subscribe(runtime.guard.on_event)
        runtime.agent = agent
        runtime._subscribe_hooks()
        if options.trace:
            runtime.trace_path = str(options.trace)
            runtime.tracer = TraceRecorder(options.trace)
            agent.subscribe(runtime.tracer)
        if options.metrics:
            runtime.metrics_path = Path(options.metrics)
            runtime._metrics = MetricsSession(runtime.metrics_path)
            runtime._metrics.start(agent)

        async def persist(event) -> None:
            if event.type != "agent_end":
                return
            if event.new_messages:
                for message in event.new_messages:
                    try:
                        await runtime.session.append(message)
                    except OSError as exc:
                        runtime.note_persist_failure(exc)
            if runtime._metrics is not None:
                await asyncio.to_thread(runtime._metrics.flush, runtime.agent)

        agent.subscribe(persist)
        # Side-channel records: run boundaries land after the run's messages
        # (this subscriber order), retries and mode changes wherever they
        # happen. Best-effort by contract — append_meta swallows failures.
        meta_recorder = SessionMetaRecorder(lambda: runtime.session)
        agent.subscribe(meta_recorder)
        agent.on_retry = make_retry_recorder(lambda: runtime.session)
        runtime.session.append_meta(mode_record(runtime._mode))
        if runtime.journal is not None:
            attach_journal(agent, runtime.journal)
        if runtime.session.messages:
            agent.replace_messages(runtime.session.messages)
            # The resumed transcript sizes the next prompt; without this the
            # guard only knows estimates until the first new request lands.
            runtime.guard.last_usage = last_request_usage(runtime.session.messages)
        runtime.warnings.extend(
            f"session {warning}" for warning in runtime.session.warnings
        )
        return runtime
    except BaseException:
        # Roll the partial runtime down: close() releases the session
        # lease, LSP/job refcounts and MCP in reverse order and is
        # tolerant of fields never assigned (audit P1-3). The original
        # error propagates untouched.
        with contextlib.suppress(Exception):
            await runtime.close()
        raise



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
