"""Frontend-agnostic assembly of an agent run.

REPL, TUI and RPC all build their runtime here instead of duplicating the
catalog → model → tools → MCP → skills → extensions → Agent → Session wiring.
Nothing in this module imports a UI library or the CLI package.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from zenagent import project
from zenagent.agent import Agent
from zenagent.agent.session import Session, latest_session, unique_session_path
from zenagent.app.modes import (
    DEFAULT_MODE,
    mode_prompt_note,
    normalize_mode,
    resolve_mode,
)
from zenagent.app.permissions import PermissionGate
from zenagent.app.support import (
    api_key_for,
    resolve_model,
    session_id_for,
    system_prompt,
)
from zenagent.config import (
    ContextGuard,
    UserConfig,
    apply_user_config,
    load_user_config,
)
from zenagent.context_files import (
    discover_context_files,
    load_context_files,
    system_prompt_with_context,
)
from zenagent.extensions import load_extensions
from zenagent.mcp.config import load_mcp_config
from zenagent.mcp.manager import McpManager
from zenagent.providers.catalog import ProviderConfig, load_catalog
from zenagent.providers.registry import StreamFn
from zenagent.skills import (
    build_skill_tool,
    default_skill_roots,
    discover_skills,
    skills_prompt_index,
)
from zenagent.templates import (
    PromptTemplate,
    default_template_roots,
    discover_templates,
)
from zenagent.tools import ALL_TOOLS
from zenagent.tools.todo import TodoTool, build_todo_tool

__all__ = ["Runtime", "RuntimeOptions"]

SwitchHook = Callable[[Path | None], Awaitable[bool]]


@dataclass
class RuntimeOptions:
    """Everything bootstrap needs; plain values so any frontend can build it."""

    model_id: str | None = None
    thinking: str | None = None
    resume: Path | str | None = None
    mcp: bool = True
    mcp_file: Path | None = None
    mcp_servers: list[str] | None = None
    skills: bool = True
    templates: bool = True
    extension_paths: list[str] = field(default_factory=list)
    context_files: bool = True
    read_only: bool = False
    allow_dangerous: bool = False
    mode: str | None = None
    retries: int = 0
    max_turns: int | None = None
    max_cost: float | None = None


class Runtime:
    """Owns the agent, session, guard, MCP servers and discovery results."""

    def __init__(self) -> None:
        self.agent: Agent
        self.session: Session
        self.model = None
        #: Env var the current model needs when the app started without a key.
        self.missing_api_key: str | None = None
        self.catalog: dict[str, ProviderConfig] = {}
        self.user_config = UserConfig()
        self.guard: ContextGuard
        self.mcp: McpManager | None = None
        self.warnings: list[str] = []
        self.skills: list = []
        self.templates: list[PromptTemplate] = []
        self.hooks: dict[str, list] = {}
        self.extension_files: list[str] = []
        self.before_switch: SwitchHook | None = None
        self.gate: PermissionGate | None = None
        #: The per-session todo tool; ``switch_session`` re-points its sidecar.
        self.todo_tool: TodoTool | None = None
        self._mode: str = DEFAULT_MODE
        self._base_prompt: str = ""
        self._base_tools: list = []
        self._extra_prompt = ""
        self._context_files = True

    @property
    def mode(self) -> str:
        """The active plan/ask/full mode."""
        return self._mode

    def set_mode(self, mode: str) -> str:
        """Switch mode; takes effect for the next tool call without a restart."""
        normalized = normalize_mode(mode)
        if normalized is None:
            raise ValueError(f"unknown mode: {mode!r} (choose plan|ask|full)")
        self._mode = normalized
        if self.gate is not None and hasattr(self.gate, "set_mode"):
            self.gate.set_mode(normalized)
        if hasattr(self, "agent") and self._base_prompt:
            self.agent.system_prompt = self._prompt_with_mode()
        return normalized

    def _prompt_with_mode(self) -> str:
        note = mode_prompt_note(self._mode)
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
        runtime = cls()
        runtime.user_config = load_user_config()
        runtime.catalog = apply_user_config(
            load_catalog(), runtime.user_config, runtime.warnings
        )
        model = resolve_model(
            runtime.catalog, options.model_id or runtime.user_config.default_model
        )
        runtime.model = model
        api_key = api_key_for(runtime.catalog, model)
        if not api_key:
            provider = runtime.catalog.get(model.provider)
            env_name = provider.api_key_env if provider else "the provider's API key"
            if not allow_missing_key:
                raise RuntimeError(f"missing API key for {model.id} (set {env_name})")
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

        if gate is None:
            gate = PermissionGate(
                assume_yes=assume_yes,
                interactive=False,
                allow_dangerous=options.allow_dangerous,
                allow=runtime.user_config.tool_allow,
                deny=runtime.user_config.tool_deny,
                mode=mode,
            )
        elif hasattr(gate, "set_mode"):
            gate.set_mode(mode)
        runtime.gate = gate

        # Open the session before assembling tools: the todo tool is per-session
        # state bound to the sidecar next to the JSONL, so its factory needs the
        # final path (resume picks that path here).
        runtime.session = runtime._open_session(options.resume)

        tools = list(ALL_TOOLS)
        runtime.todo_tool = build_todo_tool(runtime.session.path)
        tools.append(runtime.todo_tool)
        if options.mcp:
            runtime.mcp = await runtime._start_mcp(options)
            if runtime.mcp is not None:
                tools.extend(runtime.mcp.tool_defs())

        skills_index = ""
        if options.skills:
            runtime.skills, warnings = discover_skills(
                default_skill_roots(Path.cwd())
            )
            runtime.warnings.extend(f"skills {warning}" for warning in warnings)
            if runtime.skills:
                tools.append(build_skill_tool(runtime.skills))
                skills_index = skills_prompt_index(runtime.skills)

        if options.templates:
            runtime.templates, warnings = discover_templates(
                default_template_roots(Path.cwd())
            )
            runtime.warnings.extend(f"templates {warning}" for warning in warnings)

        extension_prompt = ""
        runtime.extension_files = [
            *runtime.user_config.extensions,
            *options.extension_paths,
        ]
        if runtime.extension_files:
            loaded = load_extensions(runtime.extension_files)
            runtime.warnings.extend(f"extension {warning}" for warning in loaded.warnings)
            tools.extend(loaded.tools)
            runtime.hooks = loaded.hooks
            extension_prompt = loaded.prompt_text

        runtime._context_files = options.context_files
        runtime._base_tools = list(tools)
        runtime._extra_prompt = "\n\n".join(
            part for part in (skills_index, extension_prompt) if part
        )
        runtime.guard = ContextGuard(model.context_window)
        runtime._base_prompt = runtime._build_prompt(tools)
        prompt = runtime._prompt_with_mode()
        thinking = options.thinking
        if thinking is None:
            thinking = "high" if model.reasoning else "off"

        agent = Agent(
            model=model,
            system_prompt=prompt,
            tools=tools,
            api_key=api_key,
            thinking=thinking,
            session_id=session_id_for(model),
            stream_fn=stream_fn,
            before_tool_call=gate.before_tool_call,
            transform_context=runtime.guard.transform,
            max_turns=options.max_turns,
            max_cost=options.max_cost,
            retries=options.retries,
        )
        agent.subscribe(runtime.guard.on_event)
        for event_type, callbacks in runtime.hooks.items():
            def hook(event, _type=event_type, _callbacks=callbacks) -> None:
                if event.type != _type:
                    return
                for callback in _callbacks:
                    try:
                        callback(event)
                    except Exception as exc:  # noqa: BLE001 - extension hooks are isolated
                        runtime.warnings.append(f"extension hook failed: {exc}")

            agent.subscribe(hook)

        runtime.agent = agent

        def persist(event) -> None:
            if event.type == "agent_end" and event.new_messages:
                for message in event.new_messages:
                    runtime.session.append(message)

        agent.subscribe(persist)
        if runtime.session.messages:
            agent.replace_messages(runtime.session.messages)
        runtime.warnings.extend(
            f"session {warning}" for warning in runtime.session.warnings
        )
        return runtime

    async def _start_mcp(self, options: RuntimeOptions) -> McpManager | None:
        configs = load_mcp_config(options.mcp_file)
        if options.mcp_servers:
            configs = {
                name: config
                for name, config in configs.items()
                if name in options.mcp_servers
            }
        if not configs:
            return None
        manager = McpManager(configs)
        self.warnings.extend(f"mcp {warning}" for warning in await manager.start())
        return manager

    def _open_session(self, resume: Path | str | None) -> Session:
        if resume is not None:
            path = latest_session() if resume == "latest" else Path(resume)
            if path is not None and Path(path).is_file():
                self._note_session_scope(Path(path))
                return Session(Path(path))
            self.warnings.append(f"no session to resume ({resume})")
        self._touch_project()
        return Session(unique_session_path())

    def _touch_project(self) -> None:
        """Record the project root/last_used; never fail a run over metadata."""
        try:
            project.write_project_meta()
        except OSError:
            pass

    def _note_session_scope(self, path: Path) -> None:
        """Warn when a resumed session belongs to another project (or legacy).

        This slice deliberately keeps the tools' cwd unchanged; omp switches
        project directories on cross-project resume, which we will layer on with
        config/context re-discovery rather than chdir'ing mid-run.
        """
        owner = project.project_key_for_session(path)
        current = project.project_key(project.find_project_root())
        if owner is not None and owner != current:
            self.warnings.append(
                f"session belongs to project {owner}; tools still run in {Path.cwd()}"
            )
        elif owner is None:
            self.warnings.append(
                f"legacy (unscoped) session; tools run in {Path.cwd()}"
            )

    def _build_prompt(self, tools: list) -> str:
        text = system_prompt([tool.name for tool in tools])
        if self._context_files:
            text = system_prompt_with_context(
                text, load_context_files(discover_context_files(Path.cwd()))
            )
        if self._extra_prompt:
            text = f"{text}\n\n{self._extra_prompt}"
        return text

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        if self.mcp is not None:
            manager, self.mcp = self.mcp, None
            await manager.close()

    async def refresh_mcp(self) -> None:
        """Reconnect failed MCP servers and swap refreshed tools into the agent."""
        if self.mcp is None:
            return
        self.warnings.extend(await self.mcp.retry_failed())
        tools = [*self._base_tools, *self.mcp.tool_defs()]
        if [tool.name for tool in tools] != [tool.name for tool in self.agent.tools]:
            self.agent.tools = tools
            self._base_prompt = self._build_prompt(tools)
            self.agent.system_prompt = self._prompt_with_mode()

    async def switch_session(self, path: Path | None) -> None:
        """Abort any run, then load `path` (or a fresh session) into the agent."""
        if self.before_switch is not None and not await self.before_switch(path):
            return
        self.agent.abort()
        await self.agent.wait_for_idle()
        if path is not None:
            self._note_session_scope(Path(path))
            session = Session(path)
        else:
            self._touch_project()
            session = Session(unique_session_path())
        self.agent.replace_messages(session.load())
        self.session = session
        if self.todo_tool is not None:
            # Todo state follows the session: the sidecar next to the file now
            # in context is the one the HUD shows (and the tool updates).
            self.todo_tool.bind_session(session.path)
        self.guard.last_usage = None
