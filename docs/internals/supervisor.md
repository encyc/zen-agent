# Supervisor、WorkspaceState 与 ApprovalRouter

这一层把“谁在跑、项目处于哪个模式、审批回给谁”从 UI 和字符串约定里收回到后端。

## AgentProfile

`zenagent/app/agent_profile.py::AgentProfile` 是 role-side 装配：

- identity：`id` / `kind` / `label`；
- capability：`tools_allow`、`extra_tools`、`extra_prompt`、`plugins`、`skills`、
  `templates`、`mcp`、`context_files`、`project_sessions`。

`RuntimeOptions` 只保留通用 run 旋钮和 `profile`；`Runtime` 不再持有一组 role flags。
swarm 的 `RoleManifest`、zen worker 与 CLI 三个前端都显式构造 profile。

## Supervisor

`Supervisor` 是项目级 live-agent registry，同时挂载当前模式的 dispatcher：

- `register(key, profile, runtime, session, **scope)`：登记并写入 runtime 的 run scope；
- `unregister` / `handles` / `snapshots` / `close`；
- `attach_dispatcher` / `detach_dispatcher` / `dispatch(mode, **payload)`：一个派活入口。

接线：

- `RpcHost` 每个 project key 一个 supervisor；slot 创建/关闭时注册/注销；
- `ZenOps` 把 router slot 的 profile 切成 `zen_router`，离开时切回 coding；
- `SwarmManager` 每个 `(role, work item)` 实例注册，reap/delete/rebuild/close 时注销；
- `ZenFleet` 每个 worker runtime 注册，run 结束关闭时注销。

`supervisor op=status` 返回 `mode`、live agents、running 计数和 pending approvals。
它是跨模式的“现在有什么在跑”的唯一查询面；zen/swarm 自己的 roster 仍是展示投影。

## Dispatch

`zenagent/app/dispatch.py` 把各模式的派活策略收成一个小协议：

```python
class Dispatcher(Protocol):
    mode: str
    async def dispatch(self, **payload) -> dict: ...
```

- `ZenDispatcher(fleet)`：`prompt + session` -> `fleet.dispatch`，返回 worker 状态；
- `SwarmDispatcher(manager)`：`prompt + role?` -> `manager.post`，返回 tasks/taskIds；
  未知 role 在进入房间前拒绝；
- swarm 的 `ref` / `images` 原样转发，引用和附件不因切换入口丢失；
- coding 不挂 dispatcher：单 session 用 `session prompt`，不是编排派活。

`supervisor op=dispatch` 的 `mode` 省略时读 workspace；zen 的 target 可以是 `session`
或 Supervisor 里 live 的 `agent` key（两者同时给且不一致时拒绝）。fleet 关闭、room
驱逐/项目关闭会 detach；配置重载关闭的 room 由 dispatcher 的 `started` 守卫拒绝。
之后 dispatch 返回 `dispatch_unavailable`，不会打到已关闭对象。策略拒绝（worker busy、
fleet closing、未知 role）映射为 `dispatch_rejected`。

dispatch 回执只确认“已交给编排器”（worker status / taskIds），不伪造 `runId`：
真正的 run 记录由 runner 在开始执行时写入 `runs.jsonl`，用 `runs` 查询面追踪。

## WorkspaceState

`zenagent/app/workspace_state.py` 存 `~/.zenagent/projects/<key>/workspace.json`：

```json
{"mode": "coding|zen|swarm", "updatedAt": 1762..., "routerSession": "..."}
```

- `workspace op=get/set` 是 host 级 RPC；`set` 幂等并广播 `workspace` 帧；
- zen enable/disable 会同步写入 mode（routerSession 只在 zen 时写）；
- desktop 启动/切项目时读后端校准，`switchWorkspaceMode` 再把选择写回；localStorage
  仍是离线缓存，不再是唯一真相。

## ApprovalRouter

`zenagent/app/approval_router.py` 保存 pending approval 的 `requestId -> entry`：

- entry 带 `project` / `agent_id` / `run_id` / `work_item_id` / `task_id` / future；
- slot 的 `RpcServer.confirm` 和 swarm 的 `SwarmApprovals` 都把 future 注册进来；
- host 收到 `approval` 先查 router，查不到才回退到旧的 prefix/slot 路由；
- `supervisor op=approve` 是同一入口的类型化包装；旧 `approval` 请求保持兼容。

## 当前边界

- `supervisor` 提供 status / dispatch / approve / cancel；`cancel` 对 live handle 的
  agent 发 `abort()`，具体终态仍由 zen/swarm 的 runner 映射。
- dispatch 只是入口统一：zen 的 router 调度和 swarm 的 room 调度仍是两套策略，
  `SwarmDispatcher` 不伪造工单归属，zen 也不自动挑选 worker。
- `workspace` 只记录期望模式；启动/停止 fleet/room 仍是各编排器的动作。
- 桌面 swarm composer 已切到 `supervisor op=dispatch`；zen/coding 仍走自己的
  `session prompt`。旧 `zen_fleet` / `swarm` / `session` / `approval` 请求族保留。

## 源码对照

| 关注点 | 文件 |
|---|---|
| profile / registry / dispatch mount | `zenagent/app/agent_profile.py` |
| dispatch strategies | `zenagent/app/dispatch.py` |
| workspace state | `zenagent/app/workspace_state.py` |
| approval router | `zenagent/app/approval_router.py` |
| supervisor RPC | `zenagent/cli/rpc/ops_supervisor.py` |
| workspace RPC | `zenagent/cli/rpc/ops_workspace.py` |
| host wiring | `zenagent/cli/rpc/host.py` |
| zen/swarm registration | `zenagent/cli/rpc/ops_zen.py`、`zenagent/app/swarm/manager.py`、`zenagent/app/zen/fleet.py` |
| tests | `tests/test_agent_profile.py`、`tests/test_dispatch.py`、`tests/test_rpc_supervisor.py`、`tests/test_workspace_state.py`、`tests/test_approval_router.py` |
