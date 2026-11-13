# Phase 7：Run 账本的崩溃对账

日期：2026-11-13 · 分支：`feat/orchestration-foundation` · 前置：Phase 1–6

## 问题

账本在 `agent_start` 写 `running`、`agent_end` 写终态。进程被 kill、宿主崩溃或 provider
超时跳过 `agent_end` 时，最后一条永远是 `running`：

```text
{"id": "r_...", "status": "running", "startedAt": ...}
```

重启后 `runs list status=running` 会把一个早已不存在的 agent 当成活的；UI 的“谁在跑”
重新变成猜测。Phase 1 明确把这条留为后续对账。

## 设计

对账必须回答“这个 session 现在还有 writer 吗”，而不是“这条记录旧不旧”。Session
writer lease 已经拥有这个事实，所以对账不引入新的心跳或进程表。

### 1. 会话级对账（精确、每次 attach 都做）

`Runtime` 绑定 session 时已经持有该 session 的 lease。`attach_run_recorder` 在挂上
recorder 之前调用：

```python
ledger.reconcile_session(str(runtime.session.path))
```

同一 session 的旧 `running` 行不可能属于另一个活进程——否则 lease 会拒绝本次绑定。
所以这些行可以直接收口为 `interrupted`。

### 2. 项目级清扫（每个进程一次）

只对账“被再次打开的 session”不够：一个崩溃的 worker 可能再也不会被 resume。第一个
runtime attach 到项目时，用 lease 做 liveness probe 扫一遍：

```python
ledger.reconcile_stale(session_is_live)
```

- `session_is_live(path)`：读进程 registry；否则瞬时拿一次文件锁再释放。拿不到 =
  有活 writer；拿得到 = 没有。
- 每个进程每个账本只扫一次（`_SWEPT`），避免 zen/swarm 每次建 worker 都重读全账。
- 无 session 的行不碰：没有 lease 就没有证据证明它死了（`subagent` 目前也没有
  session）。
- liveness probe 抛异常时按“活着”处理：宁可漏收一条，不能误杀一条正在跑的记录。

### 3. 收口记录

```json
{
  "status": "interrupted",
  "termination": "interrupted",
  "endedAt": 1762...,
  "durationMs": 8123,
  "error": "interrupted: the previous agent process ended without finishing this run"
}
```

保留原记录的 `kind` / `agentId` / `session` / `workItemId` / `taskId` / `parentRunId`；
只追加终态，不重写历史行。

## 非目标

- 不做心跳、不引入 supervisor 之外的第二套 liveness。
- 不新增 agent 生命周期；`subagent` 记录用父 session/父 run 归属，随本阶段一起接入。
- 不改变 swarm `tasks.jsonl` 的权威性；`SwarmStore.reconcile` 仍负责把未终态 task 标
  `failed`，账本是第二条独立投影。
- 不做 retention。

## 测试

- `reconcile_session`：只收口目标 session 的 `running`；`done`、别的 session、无 session
  的行不动。
- `reconcile_stale`：live session 跳过、dead session 收口、无 session 跳过。
- liveness probe 抛异常时不收口。
- `attach_run_recorder`：新 runtime attach 时同时收口自己 session 的旧行和项目内其他
  已死 session 的旧行。
- `session_is_live`：有 lease 为真，释放后为假。

## 验收

1. 崩溃后重启，任何被打开项目的 `runs list status=running` 不再包含已死进程；
2. 活着的跨进程 session 不会被误收口；
3. 收口记录可查询、带原因和时长；
4. 全量 Python / desktop / ruff / architecture gate 全绿。

## 交付状态

- [x] `RunLedger.reconcile_session` / `reconcile_stale`
- [x] `session_is_live` lease 探针
- [x] `attach_run_recorder` 接线（会话级 + 进程级清扫）
- [x] `task` 工具的 `subagent` 记账（父 session + 父 run）
- [x] 测试与文档
