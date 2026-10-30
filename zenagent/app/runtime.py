"""Frontend-agnostic assembly of an agent run.

REPL, TUI and RPC all build their runtime here instead of duplicating the
catalog → model → tools → MCP → skills → extensions → Agent → Session wiring.
Nothing in this module imports a UI library or the CLI package.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from zenagent.agent import Agent
from zenagent.agent.session import Session, latest_session, session_path
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
    allow_dangerous: bool = False
    retries: int = 0
    max_turns: int | None = None
    max_cost: float | None = None


class Runtime:
    """Owns the agent, session, guard, MCP servers and discovery results."""

    def __init__(self) -> None:
        self.agent: Agent
        self.session: Session
        self.model = None
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
        self._base_tools: list = []
        self._extra_prompt = ""

    # -- construction -------------------------------------------------------

    @classmethod
    async def bootstrap(
        cls,
        options: RuntimeOptions,
        *,
        stream_fn: StreamFn | None = None,
        assume_yes: bool = False,
        gate: PermissionGate | None = None,
    ) -> Runtime:
        runtime = cls()
        runtime.user_config = load_user_config()
        runtime.catalog = apply_user_config(
            load_catalog(), runtime.user_config, runtime.warnings
        )
        model = resolve_model(
            runtime.catalog, options.model_id or runtime.user_config.default_model
        )
        api_key = api_key_for(runtime.catalog, model)
        if not api_key:
            provider = runtime.catalog.get(model.provider)
            env_name = provider.api_key_env if provider else "the provider's API key"
            raise RuntimeError(f"missing API key for {model.id} (set {env_name})")
        runtime.model = model

        if gate is None:
            gate = PermissionGate(
                assume_yes=assume_yes,
                interactive=False,
                allow_dangerous=options.allow_dangerous,
                allow=runtime.user_config.tool_allow,
                deny=runtime.user_config.tool_deny,
            )

        tools = list(ALL_TOOLS)
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

        runtime._base_tools = list(tools)
        runtime._extra_prompt = "\n\n".join(
            part for part in (skills_index, extension_prompt) if part
        )
        runtime.guard = ContextGuard(model.context_window)
        prompt = runtime._build_prompt(tools)
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
        runtime.session = runtime._open_session(options.resume)

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
                return Session(Path(path))
            self.warnings.append(f"no session to resume ({resume})")
        return Session(session_path())

    def _build_prompt(self, tools: list) -> str:
        text = system_prompt([tool.name for tool in tools])
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
            self.agent.system_prompt = self._build_prompt(tools)

    async def switch_session(self, path: Path | None) -> None:
        """Abort any run, then load `path` (or a fresh session) into the agent."""
        if self.before_switch is not None and not await self.before_switch(path):
            return
        self.agent.abort()
        await self.agent.wait_for_idle()
        session = Session(path) if path is not None else Session(session_path())
        self.agent.replace_messages(session.load())
        self.session = session
        self.guard.last_usage = None
