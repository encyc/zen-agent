# 编排底座 Phase 1：Run 账本 · 查询 · Swarm 回收

> 日期：2026-11-12 · 分支：`feat/orchestration-foundation` · 基线：`b9adc5c`
>
> 上游诊断：`docs/reviews/2026-11-12-architecture-diagnosis.md`（对话产出，尚未入库）。
> 本计划只做 Phase 1，不重写内核，不迁移 zen/swarm 的全部语义。

## 1. 为什么要先做这一层

当前 coding / zen / swarm 各自维护一套“这次运行发生了什么”的记账：

- coding：`ChatController` 的 `RunStatus`；
- zen：`WorkerHandle.status` + `last_report`；
- swarm：`TaskRecord` + `SwarmAgent.state`；
- 每个 session 里还有 `run_start` / `run_end` 侧信道记录，但没有 `run_id`、没有跨模式查询。

结果是同一件事——谁在跑、跑到哪、花了多少、为什么停——有三个答案，且没有一个能跨模式查询。
更底层的缺口是：**内核把消息当一等公民，没有把 Run 当一等公民。**

Phase 1 的目标不是立刻统一三种编排器，而是先补一个**所有模式都能写入、任何前端都能查询**的
运行账本，并用它证明“统一运行模型”是可行的。之后 Supervisor / AgentProfile 才有地基。

## 2. 范围（今晚必须交付）

### A. RunLedger（项目级 append-only 账本）

新文件 `zenagent/app/runs.py`：

- `RunRecord`：`id`、`kind`、`status`、`started_at`、`ended_at`、`duration_ms`、
  `agent_id`、`session`、`work_item_id`、`parent_run_id`、`task_id`、`label`、
  `termination`、`turns`、`cost`、`error`。
- `RunLedger(path)`：`start(...)`、`finish(record, ...)`、`list(...)`、`get(...)`。
- 存储：`~/.zenagent/projects/<key>/runs.jsonl`，一行一条记录；同一 `id` 最后一条胜出；
  坏行跳过并记 warning；写入 best-effort，账本故障不能中断 agent run。
- kind 首版：`coding` / `zen_router` / `zen_worker` / `swarm` / `subagent`（subagent 先预留）。
- status 首版：`running` / `done` / `failed` / `aborted` / `cancelled` / `interrupted`。

### B. RunRecorder（Runtime 级接线）

新文件 `zenagent/app/run_wiring.py`：

- `attach_run_recorder(runtime)`：创建/绑定 `runtime.run_ledger`、`runtime.run_scope`、
  `runtime.current_run_id`，并向 `runtime.agent` 订阅 `RunRecorder`。
- `RunRecorder` 监听 `agent_start` / `agent_end`：
  - 开始时读取 `runtime.run_scope` 与当前 `runtime.session.path`，写 `running`；
  - 结束时从 `event.new_messages` 汇总 `cost`、`turns`、`termination`、`error`，写终态。
- 接线点：
  - `app/bootstrap.py::prepare`（coding / REPL / TUI / RPC slot / zen worker 都走这里）；
  - `app/swarm/manager.py::_build_agent`（swarm 绕过 prepare，单独接）。
- 不给 `Runtime.__init__` 加字段，避免 `runtime.py` 超出 1061 行 allowlist。

### C. 三种模式的 scope

- coding：默认 `kind="coding"`、`agent_id="main"`。
- zen router：`ops_zen._attach_fleet` 把 `runtime.run_scope` 改为 `zen_router`；
  `_detach_fleet` 恢复 `coding`。
- zen worker：`ZenFleet._build_worker` 在 `prepare` 后设置
  `kind="zen_worker"`、`agent_id=worker 标题/stem`、`parent_run_id=router 当前 run`。
- swarm：`SwarmManager._build_agent` 设置 `kind="swarm"`、`agent_id=role.id`、
  `work_item_id`；`SwarmSchedule._execute` 在每轮开始前写 `task_id`。

### D. `runs` RPC 查询

新文件 `zenagent/cli/rpc/ops_runs.py`，host 级（和 swarm/zen 一样）：

- `{"type":"runs","op":"list","project":...,"kind":...,"agent":...,"workItem":...,
  "session":...,"status":...,"since":...,"limit":...}`
- `{"type":"runs","op":"get","runId":...}`
- 返回 `{"type":"result","ok":true,"runs":[...],"total":N}`；`get` 返回 `{"run": {...}|null}`。
- `RpcHost` 分发；`desktop/src/rpc/types.ts` 的 `REQUEST_KINDS` 加 `'runs'`；
  `tests/test_rpc_contract.py` 自动钉住。

### E. Swarm 空闲实例回收

当前 `IdleTracker.idle()` 没有调用者，`(role, work_item)` 的 Runtime 只建不关。

- `SwarmSchedule` 增加 `_reap_idle()`：对无在飞 run、无 pending round、无 pending_apply、
  未在删除中的实例，超过 TTL 后 `runtime.close()` 并从索引移除。
- `_schedule` 在无待办时按 `reap_interval` 唤醒一次；`ttl<=0` 表示关闭回收。
- 新配置键 `[swarm] runtime_idle_s`（默认 600，非负；0 关闭），经 `ops_swarm` 传给 manager。
- 不改变任何 run 的语义；session 文件是事实源，下次任务 resume 重建实例。

## 3. 非目标（本 Phase 不做）

- 不做 Supervisor / AgentProfile 的大迁移。
- 不合并 zen 与 swarm 的 scheduler / roster / approval。
- 不把 `workspaceMode` 收到后端。
- 不做 session writer lease（这是 Phase 2 的第一项；本 Phase 先把“谁在跑”记下来）。
- 不做桌面 UI；`runs` 先作为 RPC 面存在。
- 不记录 `task` 工具子代理的 run（scope 预留 `subagent`，Phase 2 接）。

## 4. 任务清单

### Task 1：RunLedger

- [x] `tests/test_run_ledger.py`：start/finish 终态、同 id 最后一条胜出、坏行跳过、
  list 过滤、get、cost/duration。
- [x] 实现 `zenagent/app/runs.py`。
- [x] 跑 `python -m pytest tests/test_run_ledger.py -q`。

### Task 2：RunRecorder 与 coding 接线

- [x] `tests/test_run_ledger.py` 增加 `prepare()` 集成：一次 coding run 落账，
  含 session、kind、status、turns、cost。
- [x] 实现 `zenagent/app/run_wiring.py`。
- [x] `bootstrap.prepare` 接线；不增加 `runtime.py` 行数。
- [x] 跑目标测试。

### Task 3：zen / swarm scope

- [x] `ops_zen._attach_fleet` / `_detach_fleet` 设置与恢复 router scope。
- [x] `ZenFleet._build_worker` 设置 worker scope + parent run。
- [x] `SwarmManager._build_agent` 设置 role/work item scope；
  `SwarmSchedule._execute` 写入 task_id。
- [x] 测试：zen worker `_build_worker` 的 scope；swarm 一次 task 后账本出现 `kind=swarm`。
- [x] 保持 `manager.py` ≤ 841 行 allowlist（用等量删减换行）。

### Task 4：`runs` RPC

- [x] `tests/test_rpc_runs.py`：host 级 list/get、过滤、未知 op 拒绝。
- [x] 实现 `zenagent/cli/rpc/ops_runs.py`，接进 `RpcHost`。
- [x] `desktop/src/rpc/types.ts` 加 `'runs'`。
- [x] 跑 `tests/test_rpc_contract.py`。

### Task 5：Swarm 回收

- [ ] `tests/test_swarm_reclaim.py`：TTL 后实例被关闭并移除；有 pending/在飞 run 时不回收；
  `ttl=0` 不回收。
- [ ] `SwarmSchedule._reap_idle` + 定时唤醒。
- [ ] `config.py` 加 `[swarm] runtime_idle_s`，`ops_swarm` 传入。
- [ ] 跑 swarm 全量目标测试。

### Task 6：文档与门禁

- [ ] `docs/internals/run-ledger.md`：字段、写入点、查询方式、当前边界。
- [ ] `CHANGELOG.md` Unreleased 条目。
- [ ] `python scripts/check_architecture.py`、`ruff check`、目标 pytest 全绿。
- [ ] 提交（每个 Task 一次 commit，最后汇总）。

## 5. 验收标准

1. 跑一次 coding、一次 zen worker、一次 swarm task，项目 `runs.jsonl` 出现三条可区分记录，
   都带 `runId`、`kind`、`session`、`status`、`turns`、`cost`。
2. `runs op=list` 能按 `kind` / `agent` / `workItem` 过滤并返回上述记录。
3. swarm 实例在 TTL 后消失，`manager.agents` 为空，runtime 已 close；有 pending 任务时不回收。
4. `scripts/check_architecture.py` 通过；`runtime.py` 不超过 1061 行；
   `manager.py` 不超过 841 行。
5. 全部新增测试通过，既有相关测试不回归。

## 6. 后续 Phase（不在今晚）

- Phase 2：Session writer lease（一个 session 同时一个 writer；router 槽位独占）。
- Phase 3：Supervisor + AgentProfile，把 `RuntimeOptions` 里的 role 字段移出 `Runtime`。
- Phase 4：`workspaceMode` 收到后端，模式切换变成幂等事务。
- Phase 5：统一 zen/swarm 的 scheduler / approval / roster，RPC 收敛到 `supervisor` 请求族。
