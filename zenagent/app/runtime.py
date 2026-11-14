"""Frontend-agnostic assembly of an agent run.

REPL, TUI and RPC all build their runtime here instead of duplicating the
catalog → model → tools → MCP → skills → extensions → Agent → Session wiring.
Nothing in this module imports a UI library or the CLI package.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import project
from zenagent.agent import Agent
from zenagent.agent.artifacts import build_artifact_sink
from zenagent.agent.memory_store import MemoryStore, format_memory_index
from zenagent.agent.registry import ToolRegistry
from zenagent.agent.session import Session, latest_session, unique_session_path
from zenagent.agent.subagents_runtime import SubagentObserver
from zenagent.app import (
    limits_apply,
    plugin_wiring,
    records,
    skills_recompose,
)
from zenagent.app import session_lease as leases
from zenagent.app.agent_profile import AgentProfile
from zenagent.app.metrics import MetricsSession
from zenagent.app.modes import (
    DEFAULT_MODE,
    mode_prompt_note,
    normalize_mode,
)
from zenagent.app.permissions import PermissionGate
from zenagent.app.rewind import RewindState
from zenagent.app.session_meta import (
    mode_record,
)
from zenagent.app.support import (
    api_key_for,
)
from zenagent.app.views import last_request_usage
from zenagent.config import (
    ContextGuard,
    UserConfig,
)
from zenagent.context_files import (
    discover_context_files,
    load_context_files,
)
from zenagent.i18n import t

_log = logging.getLogger(__name__)
from zenagent.limits import Limits
from zenagent.mcp.manager import McpManager
from zenagent.prompts import (
    PromptContext,
    RenderedBlock,
    RenderedPrompt,
    compose_system,
)
from zenagent.providers.catalog import ProviderConfig
from zenagent.providers.limits import estimate_text_tokens
from zenagent.providers.registry import StreamFn
from zenagent.templates import (
    PromptTemplate,
)
from zenagent.tools.ask import AskHandler, build_ask_tool
from zenagent.tools.lsp import release_clients
from zenagent.tools.todo import (
    TodoTool,
    load_todo_phases,
    save_todo_phases,
)
from zenagent.trace import TraceRecorder

__all__ = ["Runtime", "RuntimeOptions"]

SwitchHook = Callable[[Path | None], Awaitable[bool]]


@dataclass
class RuntimeOptions:
    """Everything bootstrap needs; plain values so any frontend can build it."""

    #: Working/project directory this runtime is scoped to. ``None`` keeps the
    #: historical behaviour: discover from the process cwd.
    root: Path | str | None = None
    model_id: str | None = None
    thinking: str | None = None
    resume: Path | str | None = None
    #: Role-side assembly: identity and capability policy. Generic run knobs
    #: stay on this dataclass; the profile owns what used to be role flags.
    profile: AgentProfile = field(default_factory=AgentProfile)
    mcp_file: Path | None = None
    mcp_servers: list[str] | None = None
    extension_paths: list[str] = field(default_factory=list)
    read_only: bool = False
    allow_dangerous: bool = False
    allow_outside: list[str] = field(default_factory=list)
    allow_destructive: bool = False
    allow_protected: bool = False
    mode: str | None = None
    retries: int = 0
    max_turns: int | None = None
    max_cost: float | None = None
    #: Explicit key override (REPL/SDK embedders); skips the catalog lookup.
    api_key: str | None = None
    #: JSONL trace of turns/tool calls (``--trace``).
    trace: Path | str | None = None
    #: Per-run metrics JSONL (``--metrics``).
    metrics: Path | None = None
    #: Automatic compaction toggle (``--no-auto-compact``).
    auto_compact: bool = True
    #: Explicit tool list (embedding/test seam); ``None`` = compose the default.
    tools: list | None = None
    #: Retry cadence for late MCP servers.
    mcp_retry_interval_s: float = 10.0


class Runtime:
    """Owns the agent, session, guard, MCP servers and discovery results."""

    def __init__(self) -> None:
        self.agent: Agent
        self._session: Session
        self.model = None
        #: Directory tools resolve relative paths against and status reports.
        self.cwd: Path = Path.cwd()
        #: Project identity root (nearest ``.git``): sessions, config, MCP,
        #: skills, templates, memory and the permission policy hang off it.
        self.root: Path = self.cwd
        self.profile = AgentProfile()
        #: Env var the current model needs when the app started without a key.
        self.missing_api_key: str | None = None
        self.catalog: dict[str, ProviderConfig] = {}
        self.user_config = UserConfig()
        self.guard: ContextGuard
        self.mcp: McpManager | None = None
        self.warnings: list[str] = []
        self.skills: list = []
        self.templates: list[PromptTemplate] = []
        #: Plugin id → ready / needs_credentials / disabled (Settings → Plugins).
        self.plugin_statuses: dict[str, str] = {}
        self._plugin_skill_roots: list[Path] = []
        self._plugin_extension_files: list[str] = []
        self.hooks: dict[str, list] = {}
        self.extension_files: list[str] = []
        self.before_switch: SwitchHook | None = None
        self.gate: PermissionGate | None = None
        #: Pre-write snapshot log; set by bootstrap when backups are enabled.
        self.journal = None
        #: The per-session todo tool; ``switch_session`` re-points its sidecar.
        self.todo_tool: TodoTool | None = None
        #: Spill sink for oversized tool results; follows the session.
        self.artifact_sink: Callable[[str, str], Path | None] | None = None
        #: Whether bootstrap acquired a background-job-manager reference.
        self._jobs_acquired = False
        #: Whether bootstrap acquired an LSP-client-cache reference.
        self._lsp_acquired = False
        #: Session files this process opened fresh (nothing was resumed). An
        #: unused one is removed at close instead of left in the listings.
        self._created_sessions: set[Path] = set()
        #: Per-session task/subagent tool (read-only children share this model).
        self.task_tool = None
        #: The frontend that answers ``ask`` calls, installed after bootstrap
        #: (REPL reader, RPC server, the mounted TUI). None degrades gracefully.
        self.ask_handler: AskHandler | None = None
        #: The live subagent viewer (the TUI's subagent view); None for headless.
        self.subagent_observer: SubagentObserver | None = None
        #: The ask tool instance bound to this Runtime's handler slot; the
        #: task tool's children receive it so subagents ask through the same
        #: frontend.
        self.ask_tool = build_ask_tool(lambda: self.ask_handler)
        self._mode: str = DEFAULT_MODE
        self._base_prompt: str = ""
        #: Block provenance of the last composed system prompt (ctx report).
        self._base_prompt_report: RenderedPrompt | None = None
        self.prompt_report: RenderedPrompt | None = None
        self._base_tools: list = []
        #: Desired set + activation policy for the model-visible tool list.
        self.tool_registry = ToolRegistry()
        #: Zen mode fleet when enabled; None in coding mode. The kernel never
        #: names the zen package (that cycle ran through bootstrap, audit P0-3):
        #: the orchestrator registers its surface via the two provider slots
        #: below when it attaches, and unregisters nothing (providers read the
        #: live attribute and go inert when it clears).
        self.zen_fleet = None
        #: Orchestrator tool sources: Callable[[Runtime], list[AgentTool]],
        #: consulted on every recompose. Registered by zen wiring; empty in
        #: coding mode.
        self.tool_providers: list[Callable[[Runtime], list]] = []
        #: Extra system-prompt notes sourced the same way (e.g. the zen router
        #: note); each returns the note text or None when inactive.
        self.prompt_note_providers: list[Callable[[], str | None]] = []
        #: Transcript write failures (audit P1-8): the in-memory transcript and
        #: the JSONL must not diverge silently. Listeners (chat) surface it.
        self.persist_failures = 0
        self.last_persist_error: str = ""
        self.error_notice_listeners: list[Callable[[str], None]] = []
        #: Checkpoint/rewind markers (context projection; transcript stays intact).
        self.rewind = RewindState()
        #: Extra workspace roots tracked by /add-dir (display only for now).
        self.extra_dirs: list[Path] = []
        #: JSONL trace path when the frontend started with --trace.
        self.trace_path: str | None = None
        #: JSONL trace recorder; closed by :meth:`close`.
        self.tracer: TraceRecorder | None = None
        #: Automatic compaction toggle read by ChatController.
        self.auto_compact = True
        #: Per-run metrics JSONL and its live recorder.
        self.metrics_path: Path | None = None
        self._metrics: MetricsSession | None = None
        #: Explicit tool list (embedding/test seam); ``None`` = compose.
        self._custom_tools: list | None = None
        self._skill_entries = ""
        #: Project memory library (<root>/.zenagent/memory/); set per-recompose.
        self.memory_store: MemoryStore | None = None
        self._extension_prompt = ""
        self._extension_tools: list = []
        #: Slash commands contributed by extensions (name → ExtensionCommand).
        self.extension_commands: dict = {}
        self._hook_unsubscribes: list = []
        self._options: RuntimeOptions | None = None

    @property
    def limits(self) -> Limits:
        """``[limits]`` in force: prompt/loop budgets from ``config.toml``."""
        return self.user_config.limits

    @property
    def session(self) -> Session:
        """The session in context; assigning one retargets the spill sink."""
        return self._session

    @session.setter
    def session(self, session: Session) -> None:
        leases.bind_session_lease(self, session.path)
        self._session = session
        # A new transcript invalidates checkpoint/rewind indexes.
        self.rewind.clear()
        # Artifacts belong to the session in context. Hooking the assignment
        # (not just switch_session) keeps the TUI's retry/compaction paths,
        # which set ``runtime.session`` directly, spilling to the right place.
        self.artifact_sink = build_artifact_sink(session.path)
        if self.todo_tool is not None:
            self.todo_tool.bind_session(session.path)
        task_tool = getattr(self, "task_tool", None)
        if task_tool is not None:
            task_tool.bind_session(session.path)
        agent = getattr(self, "agent", None)
        if agent is not None:
            agent.artifact_sink = self.artifact_sink

    @property
    def mode(self) -> str:
        """The active plan/ask/auto mode."""
        return self._mode

    def set_mode(self, mode: str) -> str:
        """Switch mode; takes effect for the next tool call without a restart."""
        normalized = normalize_mode(mode)
        if normalized is None:
            raise ValueError(t("mode.unknown", mode=mode))
        self._mode = normalized
        # Side-channel: mode governs what tools may do, so the session keeps
        # an audit line for every switch.
        self.session.append_meta(mode_record(normalized))
        if self.gate is not None and hasattr(self.gate, "set_mode"):
            self.gate.set_mode(normalized)
        if hasattr(self, "agent") and self._base_prompt:
            self.agent.system_prompt = self._prompt_with_mode()
        return normalized

    def _prompt_with_mode(self) -> str:
        note = mode_prompt_note(self._mode)
        for provider in self.prompt_note_providers:
            extra = provider()
            if extra:
                note = f"{note}\n\n{extra}" if note else extra
        report = self._base_prompt_report
        if report is not None:
            blocks = report.blocks
            if note:
                blocks = blocks + (
                    RenderedBlock(
                        id="mode_note",
                        chars=len(note),
                        cache_class="session",
                        version=report.version,
                        tokens=estimate_text_tokens(note),
                    ),
                )
            text = report.text if not note else f"{report.text}\n\n{note}"
            self.prompt_report = RenderedPrompt(
                text=text, blocks=blocks, version=report.version
            )
        if not note:
            return self._base_prompt
        return f"{self._base_prompt}\n\n{note}"

    def reload_api_key(self) -> bool:
        """Adopt a key the user saved from the app; true when the model has one.

        The Agent is handed its key at bootstrap, so a key pasted into the TUI's
        settings screen must be pushed into the live agent for the next turn to
        use it without a restart.
        """
        if self.model is None or not hasattr(self, "agent"):
            return False
        api_key = api_key_for(self.catalog, self.model)
        if not api_key:
            return False
        self.agent.api_key = api_key
        self.missing_api_key = None
        return True

    # -- construction -------------------------------------------------------

    @classmethod
    async def bootstrap(
        cls,
        options: RuntimeOptions,
        *,
        stream_fn: StreamFn | None = None,
        assume_yes: bool = False,
        gate: PermissionGate | None = None,
        allow_missing_key: bool = False,
    ) -> Runtime:
        from zenagent.app.bootstrap import (
            assemble,  # 循环回避：bootstrap 顶层 import 本模块
        )

        runtime = cls()
        await assemble(
            runtime,
            options,
            stream_fn=stream_fn,
            assume_yes=assume_yes,
            gate=gate,
            allow_missing_key=allow_missing_key,
        )
        return runtime

    def _open_session(self, resume: Path | str | None) -> Session:
        if resume is not None:
            path = (
                latest_session(root=self.root)
                if resume == "latest"
                else Path(resume)
            )
            if path is not None and Path(path).is_file():
                self._note_session_scope(Path(path))
                return records.open_session(Path(path), cwd=self.root)
            self.warnings.append(t("runtime.no_resume", resume=resume))
        self._touch_project()
        return self.use_new_session()

    def use_new_session(self) -> Session:
        """Attach a fresh, lazy session and remember that this process made it.

        Frontends that start a new conversation (bootstrap, /new, deleting the
        current session) go through here. The file appears only when the first
        message is written, so an idle launch or an abandoned switch leaves no
        ``(empty)`` transcript behind; :meth:`close` still cleans up for
        frontends that materialized one without messaging.
        """
        self.session = records.open_session(
            unique_session_path(root=self.root), lazy=True, cwd=self.root
        )
        # The project's session directory is cheap and keeps exports, task
        # sidecars and journals working before the first message; the file
        # itself stays absent until there is something to write.
        with contextlib.suppress(OSError):
            self.session.path.parent.mkdir(parents=True, exist_ok=True)
        self._created_sessions.add(self.session.path)
        return self.session

    def _touch_project(self) -> None:
        """Record the project root/last_used; never fail a run over metadata."""
        with contextlib.suppress(OSError):
            project.write_project_meta(root=self.root)

    def _note_session_scope(self, path: Path) -> None:
        """Warn when a resumed session belongs to another project (or legacy).

        This slice deliberately keeps the tools' cwd unchanged; omp switches
        project directories on cross-project resume, which we will layer on with
        config/context re-discovery rather than chdir'ing mid-run.

        Embedders that own their sessions (swarm roles) set
        ``project_sessions=False``: their transcripts live outside the project
        tree, so there is no owner to compare and no warning to raise.
        """
        if not self.profile.project_sessions:
            return
        owner = project.project_key_for_session(path)
        current = project.project_key(self.root)
        if owner is not None and owner != current:
            self.warnings.append(
                f"session belongs to project {owner}; tools still run in {self.cwd}"
            )
        elif owner is None:
            self.warnings.append(
                f"legacy (unscoped) session; tools run in {self.cwd}"
            )

    def _build_prompt(self, tools: list) -> str:
        context_text = (
            load_context_files(
                discover_context_files(self.cwd),
                max_bytes=self.limits.max_context_bytes,
            )
            if self.profile.context_files
            else ""
        )
        memory_index = (
            format_memory_index(self.memory_store)
            if self.memory_store is not None
            else ""
        )
        context = PromptContext(
            cwd=self.cwd,
            tool_names=tuple(tool.name for tool in tools),
            context_text=context_text,
            skill_entries=self._skill_entries,
            memory_index=memory_index,
            extension_fragments=tuple(
                fragment
                for fragment in (self._extension_prompt, self.profile.extra_prompt)
                if fragment
            ),
            include_context=self.profile.context_files,
            include_mode=False,
        )
        rendered = compose_system(context)
        self._base_prompt_report = rendered
        self.prompt_report = rendered
        return rendered.text

    async def _transform_context(self, messages: list) -> list:
        """Per-request context projection: an armed rewind, then overflow pruning."""
        return await self.guard.transform(self.rewind.transform(messages))

    def _recompose(self) -> None:
        skills_recompose.recompose(self)

    def reconcile(self) -> list[str]:
        return skills_recompose.reconcile(self)

    async def reconcile_async(self) -> list[str]:
        return await skills_recompose.reconcile_async(self)

    def _subscribe_hooks(self) -> None:
        """Attach the current extension hooks, replacing the previous generation.

        Reloading re-executes extension files, so without dropping the old
        listeners every reload would deliver each event one extra time.
        """
        for unsubscribe in self._hook_unsubscribes:
            unsubscribe()
        self._hook_unsubscribes = []
        for event_type, callbacks in self.hooks.items():

            def hook(event, _type=event_type, _callbacks=callbacks) -> None:
                if event.type != _type:
                    return
                for callback in _callbacks:
                    try:
                        callback(event)
                    except Exception as exc:  # noqa: BLE001 - extension hooks are isolated
                        self._note(t("runtime.hook_failed", error=exc))

            self._hook_unsubscribes.append(self.agent.subscribe(hook))

    def _note(self, message: str) -> None:
        """Record a warning once; repeated reloads must not stack duplicates."""
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def skills_enabled(self) -> bool:
        """Whether discovery ran with skills turned on (``--no-skills`` aside)."""
        return self.profile.skills

    def refresh_skills(self) -> list[str]:
        return skills_recompose.refresh_skills(self)

    def limit_overrides(self) -> dict[str, str]:
        return limits_apply.overrides(self)

    def apply_limits(self) -> None:
        return limits_apply.apply(self)

    def rediscover(self) -> list[str]:
        return skills_recompose.rediscover(self)

    async def reconfigure_plugins(self) -> list[str]:
        """Re-apply plugin contributions to the live runtime."""
        return await plugin_wiring.reconfigure(self)

    # -- lifecycle ----------------------------------------------------------

    def note_persist_failure(self, exc: OSError) -> None:
        """A transcript write failed: count it and surface it (audit P1-8).

        Best-effort logging hides this exact failure class — the run "succeeds"
        while the transcript silently loses the turn. The notice goes to every
        listener (chat wires ``set_last_error``) instead of stderr.
        """
        self.persist_failures += 1
        self.last_persist_error = t(
            "runtime.persist_failed", error=str(exc)
        )
        _log.warning("session persist failed: %s", exc)
        for listener in self.error_notice_listeners:
            with contextlib.suppress(Exception):
                listener(self.last_persist_error)

    async def close(self) -> None:
        if self.tracer is not None:
            tracer, self.tracer = self.tracer, None
            tracer.close()
        # Refcounted like the job manager: closing a sibling slot must not stop a
        # live runtime's language-server clients (last close wins).
        if self._lsp_acquired:
            self._lsp_acquired = False
            await release_clients()
        if self.mcp is not None:
            manager, self.mcp = self.mcp, None
            await manager.close()
        # Background bash jobs belong to this app/session: do not leave orphan
        # process groups behind when the last runtime shuts down.
        if self._jobs_acquired:
            self._jobs_acquired = False
            from zenagent.tools.jobs import release_job_manager

            await release_job_manager()
        self._drop_unused_session()
        leases.release_session_lease(self)
        # A mid-switch close (abort during session switch) may have left the
        # stashed pre-switch lease held; discard the pair symmetrically.
        leases.discard_switch_lease(self)

    @property
    def unused_session(self) -> bool:
        """True when close() will remove the session in context (nothing written)."""
        return self.session.path in self._created_sessions and not self.session.messages

    def _drop_unused_session(self) -> None:
        """Leave no file behind for a launch that produced no conversation.

        A file this process created and never appended a message to is not a
        transcript; a resumed session is never ours to remove.
        """
        for path in list(self._created_sessions):
            try:
                if path == self.session.path:
                    empty = not self.session.messages
                else:
                    empty = not Session(path).messages
                if empty:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    async def refresh_mcp(self, *, force: bool = False) -> list[str]:
        """Reconnect failed MCP servers and swap refreshed tools into the agent.

        Rebuilt through :meth:`_recompose` rather than appended to
        ``_base_tools``: that list already contains the MCP tools, so extending
        it would list every server tool twice.

        Raises ``RuntimeError`` while a run is streaming -- the same invariant
        as :meth:`rediscover`, since reconnecting may change ``agent.tools``
        under a request the model already composed.
        """
        if self.mcp is None:
            return []
        if hasattr(self, "agent") and self.agent.is_streaming:
            raise RuntimeError("cannot refresh tools while the agent is working")
        notes = await self.mcp.retry_failed(force=force)
        for warning in notes:
            self._note(warning)
        self._recompose()
        return notes

    async def switch_session(
        self, path: Path | None, *, carry_todos: bool = False
    ) -> None:
        """Abort any run, then load `path` (or a fresh session) into the agent.

        ``carry_todos`` copies the checklist: compaction continues the same
        work, while fork/new are new branches and start empty.
        """
        # Hold, don't peek: a busy target must be refused while this run is
        # still alive (audit P2). The stash is adopted by the session setter.
        leases.preflight_switch_lease(self, path)
        carried: list[dict] = []
        if carry_todos:
            carried = load_todo_phases(self.session.path)
        try:
            await self._switch_into(path, carry_todos, carried)
        finally:
            leases.discard_switch_lease(self)

    async def _switch_into(
        self, path: Path | None, carry_todos: bool, carried: list[dict]
    ) -> None:
        if self.before_switch is not None and not await self.before_switch(path):
            return
        self.agent.abort()
        await self.agent.wait_for_idle()
        if path is not None:
            self._note_session_scope(Path(path))
            session = records.open_session(path, cwd=self.root)
        else:
            self._touch_project()
            session = self.use_new_session()
        messages = session.load()
        self.agent.replace_messages(messages)
        self.session = session
        if self.todo_tool is not None:
            if carried:
                save_todo_phases(session.path, carried)
            # Todo state follows the session: the sidecar next to the file now
            # in context is the one the HUD shows (and the tool updates).
            self.todo_tool.bind_session(session.path)
        # Reseed from the loaded transcript: the last assistant turn is the
        # request that sized the context, and compaction/pruning read it.
        self.guard.last_usage = last_request_usage(messages)
        # A switch rebinds the loaded transcript and the per-session sidecars,
        # so rebuild rather than patch the previous binding.
        self._recompose()
