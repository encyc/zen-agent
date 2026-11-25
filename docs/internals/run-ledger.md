# Run 账本

## 为什么需要它

会话文件记录的是**消息**：谁说了什么、工具返回了什么。它不记录**一次运行**的边界与归属。
`ChatController.RunStatus` 给出当前会话的一次运行；`RunLedger` 把它放进项目级的时间线：
一次 `Agent.prompt` 就是一条 run，带稳定 `runId`、归属（kind / agent / session /
work item）和终态（termination / turns / cost / error）。

它是**投影**，不是替代：会话文件仍是消息的唯一权威，账本只回答「哪些 run 跑过、
各自怎么结束」。

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
  写终态；`max_turns` / `max_cost`（历史值）/ `error` / `no_stream` / `length` 记为
  `failed`，`aborted` 记为 `aborted`，`loop_detected`（守卫打断，连续重复同一批调用）
  记为 `interrupted`，其余正常结束记为 `done`。`event.detail` 只随 `agent_end` 存在，
  不进账本——账本记状态，说明在会话的 `run_end` 记录里。

接线点：

- `app/bootstrap.py::prepare`：coding、REPL、TUI、RPC slot 都走这里；
- `app/subagent_runs.py::SubagentRunSink`：`task` 工具的每次 child dispatch 由
  `build_task_tool(run_sink=...)` 观察，记 `kind=subagent`。

## scope

`runtime.run_scope` 是拥有者在 prompt 前写入的普通 dict：

| 来源 | kind | agentId | 额外字段 |
|---|---|---|---|
| coding | `coding` | `main` | `label` 缺省取 session stem |
| 历史值 | `zen_router` / `zen_worker` / `swarm` | — | 0.15 起不再产生，旧账本仍可读 |
| subagent | `subagent` | task name | `session` = 父 session，`parentRunId` = 派发时的父 run |

`app/run_wiring.py` 的 recorder 填这些字段；历史 kind 只作读取兼容。

## 查询

RPC（host 级，跨模式）：

```json
{"type": "runs", "op": "list", "project": "/repo", "kind": "subagent", "limit": 50}
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
| RPC | `zenagent/cli/rpc/ops_runs.py`、`zenagent/cli/rpc/host.py` |
| 测试 | `tests/test_run_ledger.py`、`tests/test_subagent_runs.py`、`tests/test_run_scopes.py`、`tests/test_rpc_runs.py` |
