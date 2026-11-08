"""The Router's worker tools — how the taiji session drives its fleet.

Five tools, all orchestration (no heavy execution lives here: workers do the
work in their own worktrees). They compose only while the active session is
the project's taiji session; :func:`taiji_tools_for` is that gate.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zenagent.agent.protocol import AgentTool, ToolResultAgent
from zenagent.app.taiji.session import is_taiji_session
from zenagent.app.taiji.supervisor import RpcRunner
from zenagent.prompts import tool_description
from zenagent.types import TextContent

if TYPE_CHECKING:
    from zenagent.app.taiji.supervisor import TaijiSupervisor

SUPERVISOR_PROVIDER = Callable[[], "TaijiSupervisor"]


def _text(payload: str) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=payload)],
        details={},
        is_error=False,
        terminate=False,
        error_kind="",
    )


def _error(payload: str) -> ToolResultAgent:
    return ToolResultAgent(
        content=[TextContent(text=payload)],
        details={},
        is_error=True,
        terminate=False,
        error_kind="taiji",
    )


def build_taiji_tools(supervisor_provider: SUPERVISOR_PROVIDER) -> list[AgentTool]:
    """The five worker tools, bound to a lazy supervisor provider."""

    async def spawn_worker(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ) -> ToolResultAgent:
        del tool_call_id, abort, on_update
        try:
            record = await supervisor_provider().spawn(
                args["prompt"], name=args.get("name")
            )
        except Exception as exc:  # noqa: BLE001 - tool errors are results, not crashes
            return _error(f"spawn failed: {exc}")
        return _text(
            f"worker {record.name} 已启动：分支 {record.branch}，worktree {record.worktree}。"
            "里程碑会自动汇报；用 message_worker 转达后续指示。"
        )

    async def message_worker(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ) -> ToolResultAgent:
        del tool_call_id, abort, on_update
        try:
            record = await supervisor_provider().message(args["name"], args["text"])
        except Exception as exc:  # noqa: BLE001
            return _error(f"message failed: {exc}")
        return _text(f"已投递给 {record.name}，它正在处理。")

    async def kill_worker(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ) -> ToolResultAgent:
        del tool_call_id, abort, on_update
        try:
            record = await supervisor_provider().kill(args["name"])
        except Exception as exc:  # noqa: BLE001
            return _error(f"kill failed: {exc}")
        return _text(
            f"worker {record.name} 已终止，worktree 已清理，分支 {record.branch} 保留。"
        )

    async def merge_worker(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ) -> ToolResultAgent:
        del tool_call_id, abort, on_update
        try:
            merged = await supervisor_provider().merge(args["name"])
        except Exception as exc:  # noqa: BLE001
            return _error(f"merge failed: {exc}")
        if merged:
            return _text(f"已合并 {args['name']} 的分支进基分支，worktree 已清理。")
        return _text(
            f"合并 {args['name']} 未执行：任务未完成、基分支不在当前 checkout、或工作区不干净。"
            "先让 worker 完成或清理主工作区。"
        )

    async def list_workers(
        tool_call_id: str, args: dict, abort: Any = None, on_update: Any = None
    ) -> ToolResultAgent:
        del tool_call_id, abort, on_update
        records = supervisor_provider().list_workers()
        if not records:
            return _text("当前没有 worker。用 spawn_worker 开一个。")
        lines = [
            f"{record.name}: {record.branch} · {record.status}"
            + (f" · {record.summary}" if record.summary else "")
            for record in records
        ]
        return _text("\n".join(lines))

    return [
        AgentTool(
            name="spawn_worker",
            description=tool_description("spawn_worker"),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The task for the worker to run.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional short task name; defaults from the prompt.",
                    },
                },
                "required": ["prompt"],
            },
            execution_mode="sequential",
            approval="read",
            execute=spawn_worker,
        ),
        AgentTool(
            name="message_worker",
            description=tool_description("message_worker"),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The worker's name."},
                    "text": {
                        "type": "string",
                        "description": "The follow-up instruction.",
                    },
                },
                "required": ["name", "text"],
            },
            execution_mode="sequential",
            approval="read",
            execute=message_worker,
        ),
        AgentTool(
            name="kill_worker",
            description=tool_description("kill_worker"),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The worker's name."}
                },
                "required": ["name"],
            },
            execution_mode="sequential",
            approval="read",
            execute=kill_worker,
        ),
        AgentTool(
            name="merge_worker",
            description=tool_description("merge_worker"),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The worker's name."}
                },
                "required": ["name"],
            },
            execution_mode="sequential",
            approval="read",
            execute=merge_worker,
        ),
        AgentTool(
            name="list_workers",
            description=tool_description("list_workers"),
            parameters={"type": "object", "properties": {}},
            execution_mode="sequential",
            approval="read",
            execute=list_workers,
        ),
    ]


def taiji_tools_for(
    session_path: Path | None, supervisor_provider: SUPERVISOR_PROVIDER
) -> list[AgentTool]:
    """The Router's tools only while the taiji session is the active one."""
    if not is_taiji_session(session_path):
        return []
    return build_taiji_tools(supervisor_provider)


def taiji_supervisor_for(runtime: Any) -> TaijiSupervisor:
    """This host's supervisor for ``runtime``'s project, mode and chat.

    The public seam for both the Router's tool provider and the RPC panel
    ops; the first call wins the process singleton, so serve() may construct
    it eagerly at boot and later calls (tools, ops) just rebind.
    """
    from zenagent import project
    from zenagent.app.taiji.supervisor import get_taiji

    root = project.find_project_root(Path.cwd())
    import os

    worker_model = os.environ.get("ZENAGENT_WORKER_MODEL") or None
    return get_taiji(
        repo_root=root,
        sessions_dir=project.project_sessions_dir(root=root),
        registry_path=project.project_dir(root=root) / "taiji.json",
        runner=RpcRunner(worker_model=worker_model),
        chat=getattr(runtime, "taiji_chat", None),
        mode_of=lambda: runtime.mode,
    )


def runtime_taiji_provider(runtime: Any) -> SUPERVISOR_PROVIDER:
    """Bind a lazy supervisor to ``runtime``'s project, mode and host chat."""
    return lambda: taiji_supervisor_for(runtime)
