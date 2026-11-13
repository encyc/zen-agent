# Run 账本

## 为什么需要它

会话文件记录的是**消息**：谁说了什么、工具返回了什么。它不记录**一次运行**的边界与归属。
于是每个编排层都自己补了一本账：coding 用 `ChatController.RunStatus`，zen 用
`WorkerHandle.status`，swarm 用 `TaskRecord` + `SwarmAgent.state`。同一件事——谁在跑、
跑到哪、花了多少、为什么停——有三个答案，且不能跨模式查询。

`RunLedger` 是这本共同账：一次 `Agent.prompt` 就是一条 run，带稳定 `runId`、归属
（模式 / agent / session / work item）和终态（termination / turns / cost / error）。
它是**投影**，不是替代：swarm 的 `tasks.jsonl` 仍是任务状态的权威，zen 的 roster 仍是
worker 状态的权威；账本负责把它们放到同一张时间线上。

## 存储

```
~/.zenagent/projects/<project-key>/runs.jsonl
```

一行一条记录，`type: "run"`。同一 `id` 最后一条胜出；坏行/坏 JSON 跳过并记 warning。
写入是 best-effort：磁盘满或目录不可写时只记 warning，绝不打断 agent run。

```json
{
  "type": "run",
  "version": 1,
  "id": "r_18f...",
  "kind": "zen_worker",
  "status": "done",
  "startedAt": 1762...,
  "updatedAt": 1762...,
  "endedAt": 1762...,
  "durationMs": 8123,
  "agentId": "worker-a",
  "session": "/Users/me/.zenagent/projects/demo-1a2b3c4d/sessions/...jsonl",
  "workItemId": null,
  "parentRunId": "r_18e...",
  "taskId": null,
  "label": "tests",
  "termination": "stop",
  "turns": 4,
  "cost": 0.0123,
  "error": null
}
```

## 写入点

`app/run_wiring.py::attach_run_recorder(runtime)` 在 `runtime.agent` 上订阅一个
`RunRecorder`：

- `agent_start`：读 `runtime.run_scope` 与当前 `runtime.session.path`，写 `running`；
- `agent_end`：从 `event.new_messages` 汇总 `cost`，读 `event.turns` / `event.termination`，
  写终态；`max_turns` / `max_cost` / `error` / `no_stream` / `length` 记为 `failed`，
  `aborted` 记为 `aborted`，其余正常结束记为 `done`。

接线点：

- `app/bootstrap.py::prepare`：coding、REPL、TUI、RPC slot、zen worker 都走这里；
- `app/swarm/manager.py::_build_agent`：swarm 绕过 `prepare`，单独接；
- `app/subagent_runs.py::SubagentRunSink`：`task` 工具的每次 child dispatch 由
  `build_task_tool(run_sink=...)` 观察，记 `kind=subagent`。

## scope

`runtime.run_scope` 是编排层在 prompt 前写入的普通 dict：

| 模式 | kind | agentId | 额外字段 |
|---|---|---|---|
| coding | `coding` | `main` | `label` 缺省取 session stem |
| zen router | `zen_router` | `router` | — |
| zen worker | `zen_worker` | worker 标题/stem | `parentRunId` = 派工时 router 的 `current_run_id`，`label` = focus |
| swarm | `swarm` | role id | `workItemId`、`taskId`（每轮写入） |
| subagent | `subagent` | task name | `session` = 父 session，`parentRunId` = 派发时的父 run |

`app/zen/fleet.py::_build_worker`、`app/swarm/manager.py::_build_agent`、
`app/swarm/schedule.py::_execute` 分别填这些字段；`cli/rpc/ops_zen.py` 在 fleet
attach/detach 时切换 router/coding。

## 查询

RPC（host 级，跨模式）：

```json
{"type": "runs", "op": "list", "project": "/repo", "kind": "swarm", "limit": 50}
{"type": "runs", "op": "get", "project": "/repo", "runId": "r_..."}
```

`list` 支持 `kind` / `agent` / `workItem` / `session` / `status` / `since` / `limit`，
返回 newest-first。字段名与磁盘记录一致（camelCase），但去掉 `type` / `version` 信封。

Python 侧可直接用 `RunLedger(path).list(...)` / `.get(...)`；`RunLedger.load()` 是完整重放。

## 崩溃对账

`attach_run_recorder` 在挂 recorder 之前先收口崩溃残留：

- `RunLedger.reconcile_session(path)`：当前 runtime 已持有该 session 的 writer lease，
  同路径旧的 `running` 行不可能属于活进程，直接追加 `interrupted`；
- `RunLedger.reconcile_stale(session_is_live)`：每个进程每个账本扫一次，用 lease 探针
  判断其他 session 是否还有 writer；探针失败按“活着”处理，避免误杀；
- 收口保留原归属字段，只追加 `status=interrupted`、`termination=interrupted`、
  `endedAt`、`durationMs` 和 `error` 原因；
- 无 session 的行不碰：没有 lease 就没有死亡证据。

## 当前边界

- **subagent 用父 session 归属**：孩子没有独立 session，账本行记录父 run 和父
  session；父 session 恢复时由 `reconcile_session` 一并收口。
- **swarm 的终态仍以 task 为准**：账本记的是 agent run 的终止原因；用户取消的任务在
  `tasks.jsonl` 里是 `cancelled`，账本里可能仍是 `aborted`。房间启动时
  `SwarmStore.reconcile` 会把未终态 task 标 `failed`，账本再独立收口 run。
- **没有 retention/compaction**：`runs.jsonl` 只追加。
- **对账是 best-effort**：文件系统不支持 advisory lock 时 lease 本身已降级，探针按
  进程内 registry 判断。

## 源码对照

| 关注点 | 文件 |
|---|---|
| record / ledger / 重放 / 过滤 | `zenagent/app/runs.py` |
| Runtime 接线 / scope 更新 / 成本汇总 / 崩溃对账 | `zenagent/app/run_wiring.py` |
| writer lease / liveness 探针 | `zenagent/app/session_lease.py` |
| coding 接线 | `zenagent/app/bootstrap.py::prepare` |
| zen scope | `zenagent/app/zen/fleet.py::_build_worker`、`zenagent/cli/rpc/ops_zen.py` |
| swarm scope | `zenagent/app/swarm/manager.py::_build_agent`、`zenagent/app/swarm/schedule.py::_execute` |
| RPC | `zenagent/cli/rpc/ops_runs.py`、`zenagent/cli/rpc/host.py` |
| 测试 | `tests/test_run_ledger.py`、`tests/test_subagent_runs.py`、`tests/test_run_scopes.py`、`tests/test_rpc_runs.py` |
