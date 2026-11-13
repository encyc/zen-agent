# 编排底座 Phase 3–5：Supervisor · 后端模式 · 统一入口

> 日期：2026-11-13 · 分支：`feat/orchestration-foundation` · 上游：Phase 2 `7ef3ba7`
>
> 诊断：[`2026-11-12-architecture-diagnosis.md`](../reviews/2026-11-12-architecture-diagnosis.md)
> Phase 1/2：见同目录计划。

## Phase 3：Supervisor + AgentProfile

### 3a `AgentProfile`（已落）

- 新增 `zenagent/app/agent_profile.py`：`AgentProfile` 承载 role-side 装配
  （identity、tools allowlist、extra tools/prompt、plugins/skills/templates/MCP、
  context files、session ownership）。
- `RuntimeOptions` 只保留通用 run 旋钮 + `profile`；`Runtime` 不再持有
  `_tools_allow` / `_extra_tools` / `_extra_prompt` / `context_files` /
  `project_sessions` 字段。
- swarm 的 `RoleManifest`、zen worker、CLI 三个前端都改为显式构造 profile。
- 测试 fixture 把旧的 role kwargs 折叠成 profile，避免测试重复装配细节。

### 3b `Supervisor` 注册表

- `Supervisor` 是**项目级 live-agent registry**，不调度、不 prompt：
  - `register(key, profile, runtime, session, **scope)`；
  - `unregister(key)`、`handles()`、`snapshots()`、`close()`。
- 接线：
  - `RpcHost` 按 project key 持有 `Supervisor`；slot 创建/关闭时注册/注销；
  - `ZenOps._attach_fleet` 把 router slot 的 profile 更新为 `zen_router`；
  - `SwarmManager` 接收可选 supervisor，`_build_agent` 注册、`_reap_one` /
    `delete_role` / `close` 注销；
  - `ZenFleet` 接收可选 supervisor，worker 构建时注册、run 结束关闭时注销。
- 新增 host 级 `supervisor op=status`：返回 `snapshots()`；这是 Phase 5 的统一查询面。
- 测试：registry 单测、host slot 注册/注销、swarm task 后 snapshot、zen worker 注册。

## Phase 4：后端 WorkspaceState

### 4a 状态

- 新增 `zenagent/app/workspace_state.py`：
  - `WorkspaceState { mode: coding|zen|swarm, updatedAt, routerSession? }`；
  - 存 `~/.zenagent/projects/<key>/workspace.json`，原子写；
  - `load(root)` / `save(root, state)`；缺失/损坏回落 `coding`。
- 约束：mode 是后端事实源；UI 只读/请求切换。

### 4b RPC

- host 级 `workspace`：
  - `get {project}` → `{mode, updatedAt, routerSession}`；
  - `set {project, mode}` → 幂等，校验；拒绝与运行态冲突的切换（例如 busy 时不强行
    abort，返回 `workspace_busy`，由调用方先停）；
  - 服务端主动帧 `workspace`（project + mode）。
- `ops_zen` enable/disable 与 swarm room 首次启动时同步写入 mode；以显式 `workspace.set`
  为准，不靠对象存在反推。

### 4c Desktop

- `workspaceMode` 仍保留本地乐观值，但 boot/项目切换时以 `workspace get` 校准；
- `switchWorkspaceMode` 先 `workspace set`，成功后执行 zen enable/disable；
- 冲突回滚到后端返回的 mode；不再用 localStorage 作为唯一真相。
- 测试：store/actions、workspace RPC、断线回退。

## Phase 5：统一 approval / roster / RPC

### 5a ApprovalRouter

- 新增 `zenagent/app/approvals.py`（或 `supervisor` 内）：
  - 请求 id 带 `run_id` / `agent_id` / `work_item_id` / `project`；
  - 类型化路由，不靠 `<project>:swarm:` 字符串前缀；
  - `ApprovalRouter.resolve(request_id, decision)` 统一入口。
- swarm 的 `SwarmApprovals` 改为向 router 注册；zen/slot 的 approval 保持兼容，
  但帧上补 `runId`/`agentId`。
- 测试：跨项目/跨模式回复、超时、未知 id。

### 5b Roster

- `supervisor status` 提供 live handles；zen roster 与 swarm roster 作为**投影**读它 +
  各自配置，不再各自维护一份“谁活着”。
- swarm 的 `agents_state` 从 supervisor snapshot 派生（保留 role/workItem 字段）；
  zen 的 roster 状态从 handle 的 run 状态派生。
- 测试：两个模式同时有 live agent 时，一个查询能看到全部。

### 5c RPC 收敛

- 新 host 级 `supervisor` 请求族：
  - `status`（live agents + mode + counts）；
  - `dispatch` / `cancel` / `approve` 先作为适配器委托 zen/swarm；
  - `runs`、`workspace`、`approvals` 保持独立子域。
- 旧 `zen_fleet` / `swarm` 请求族保留为兼容适配器一个版本；桌面逐步切到 supervisor。
- 契约测试：Python dispatch、TS `REQUEST_KINDS`、新帧字段三方同步。

## 非目标

- 不重写 `Agent` / `Session` / provider 内核。
- 不做跨机器分布式调度。
- 不删除旧 RPC 入口；先加统一面，再在后续版本迁移。

## 验收

1. `AgentProfile` 之外，`Runtime` 不再有 role-specific 字段；
2. host 能为每个项目返回一份 live-agent snapshot，zen/swarm 的 agent 都在里面；
3. `workspace get/set` 是模式事实源；UI 启动后与后端一致；
4. approval 回复按 `project + run_id` 路由，跨项目不再靠字符串前缀；
5. 旧 RPC 入口仍可用；新 `supervisor` 入口的契约测试全绿；
6. 全量 pytest / desktop test / ruff / architecture gate 全绿。
