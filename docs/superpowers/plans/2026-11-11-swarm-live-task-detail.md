# Swarm 任务详情实时化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** swarm 任务详情在 run 期间实时流入步骤（打字机/工具卡实时态），与 coding 事件流同构；消灭"run 中空白、结束突然全出"。

**Architecture:** 角色 Agent 事件经 manager 订阅龙头 → host 层 `swarm_event` 全局帧 → `swarmRun.ts` 视图级 reducer → `TaskDetailPanel` 实时渲染；终态以文件 transcript 对账。后端不动落盘路径。

**Tech Stack:** Python asyncio（agent.subscribe / codec.serialize_event）+ React/Zustand + node:test + Playwright。

**Spec:** `docs/superpowers/specs/2026-11-11-swarm-live-task-detail-design.md`（执行者须同读）

## Global Constraints

- 工作区：worktree `/Users/mac/zen-agent/.worktrees/merge-zen-swarm`，分支 `merge/zen-swarm`（追加提交）。
- 分层：`app/` 不得 import `cli/`；serialize_event 只在 ops_swarm（cli 层）调用。
- `stream_fn_factory` 语义不动（模型 provider 口，非事件口）。
- 门禁：`scripts/check.sh` + desktop `pnpm test && pnpm lint:tokens && pnpm build` + `pnpm e2e`；LOC 预算只降不升。
- 事件订阅者 best-effort：listener 内异常自吞，不得影响 run。

## Review Focus

1. run 中途打开面板：从打开时刻起 live 流入（缺开头、终态补全）——Task 4 面板测试 + e2e。
2. `new_messages` 权威覆盖：message_end 用带 id 的视图替换流式行，不产生重复行——Task 4 reducer 测试。
3. 无 sink / 无 active task（rebuild）时事件安全丢弃、run 不受影响——Task 1 测试。
4. `swarm_event` 帧全局无 project/session 标签、经 applySwarmFrame 在过滤前消费——Task 3 测试。
5. 终态对账后 `clearRun` 释放内存（不留无界增长）——Task 4 测试。

---

### Task 1: manager 事件龙头（active-task 标注 + sink）

**Files:**
- Modify: `zenagent/app/swarm/manager.py`
- Test: `tests/test_swarm_manager.py`

**Interfaces:**
- Produces: `SwarmManager(event_sink: Callable[[str, str, AgentEvent], None] | None = None)`；`_active_tasks: dict[role_id, task_id]`；`_tap_role_event(role_id, event)` 内部方法。Task 2 的 ops 层消费 event_sink 签名。

- [ ] **Step 1: 失败测试**：用既有 fake 模型 harness（`stream_fn_factory=text_stream(...)`）+ `event_sink=collector`：派任务 → collector 收到 (role_id, task_id, 首事件 turn_start…)；任务结束后 `_active_tasks` 清空；`event_sink=None` 时正常跑完不崩；非 run 期事件（手动 `manager._tap_role_event` 调用）无 active task 不外发。
- [ ] **Step 2: 跑测试确认失败**（TypeError: unexpected keyword 'event_sink'）。
- [ ] **Step 3: 实现**：`__init__` 存 sink + `self._active_tasks = {}`；`_build_agent` bootstrap 成功后 `runtime.agent.subscribe(...)`（sync lambda → `_tap_role_event`，整体 try/except 吞异常）；`_execute` 首 finally 模式 set/pop；`_tap_role_event` 查 map→sink。
- [ ] **Step 4: 绿 + `uv run pytest tests/test_swarm_manager.py -q` 全绿。**
- [ ] **Step 5: Commit** `feat(swarm): role agent events surface through a task-stamped sink`

### Task 2: host 层 swarm_event 帧

**Files:**
- Modify: `zenagent/cli/rpc/ops_swarm.py`（import serialize_event；`_swarm_manager` 传 sink；新增 `_send_role_event`）
- Test: `tests/test_rpc_swarm.py`

**Interfaces:**
- Consumes: Task 1 的 `event_sink(role_id, task_id, event)`。
- Produces: 帧 `{"type":"swarm_event","swarm":true,"agent":…,"task":…,"event":serialize_event(event)}`（Task 3 前端消费此形状）。

- [ ] **Step 1: 失败测试**：构造 host + fake manager，调 `host._send_role_event("r1","t_1",AgentEvent(type="turn_start"))` → 收到帧 dict 断言形状（swarm:true、无 project/session 键、event.type 序列化正确）；再加集成路径断言 `_swarm_manager()` 构造时把 sink 接到 `_send_role_event`。
- [ ] **Step 2: RED 确认（AttributeError）。**
- [ ] **Step 3: 实现**（serialize_event 从 `.codec` import）。
- [ ] **Step 4: 绿 + `uv run pytest tests/test_rpc_swarm.py -q`。**
- [ ] **Step 5: Commit** `feat(swarm): broadcast role run events as swarm_event frames`

### Task 3: 前端接线（类型 + 分流 + store 行缓冲）

**Files:**
- Modify: `desktop/src/rpc/types.ts`、`desktop/src/state/swarmWiring.ts`、`desktop/src/state/swarmStore.ts`
- Test: `desktop/tests/swarmWiring.test.ts`、`desktop/tests/swarmStore.test.ts`

**Interfaces:**
- Produces: `SwarmEventFrame`；`applyRunEvent(frame): void`（swarmStore，内部走 Task 4 的 `applyRunEvent(rows, event)` reducer，上限 1500 行/task 丢头）；`clearRun(taskId): void`；`runRows: Record<string, (MessageView|ToolCallView)[]>`。

- [ ] **Step 1: 失败测试**：swarmWiring——`swarm_event` 帧被消费且 `runRows[tid]` 长度增长、非 swarm_event 不消费；swarmStore——tool_execution_start 事件落行、1500 上限丢头、clearRun 清除。
- [ ] **Step 2: RED。**
- [ ] **Step 3: 实现**（store 先用直接 append 的朴素实现，reducer 语义在 Task 4 替换——本任务的测试只断言 append/cap/clear，与 Task 4 的 reducer 测试互补）。
- [ ] **Step 4: 绿 + 提交** `feat(desktop): swarm_event frames land in per-task run rows`

### Task 4: swarmRun 视图 reducer + 面板实时化

**Files:**
- Create: `desktop/src/state/swarmRun.ts`
- Modify: `desktop/src/state/swarmStore.ts`（接 reducer）、`desktop/src/ui/pages/RoomPage.tsx`（TaskDetailPanel）
- Test: `desktop/tests/swarmRun.test.ts`、`desktop/tests/swarmStore.test.ts`（reducer 接线）

**Interfaces:**
- Produces: `applyRunEvent(rows: RunView[], event: AgentEventFrame): RunView[]`（纯函数）；面板渲染规则：active → `runRows[task.id]`（文件 seed 恒空）；终态 → transcript fetch 权威全量 + `clearRun`；sticky 80px。

- [ ] **Step 1: 失败测试（reducer 核心语义）**：
  - text_start/delta → 流式 assistant 行 text 累积；message_end(new_messages 带 id) → 替换流式尾行为权威视图（无重复）；
  - tool_execution_start/update/end → 行 append、progress/result patch、status running→ok/error；
  - message_start(user) → user 行；无关事件（agent_start/turn_*）返回原数组引用。
- [ ] **Step 2: RED。**
- [ ] **Step 3: 实现 reducer + store 接线 + 面板改造**（active 时渲染 runRows；`active` 翻转的 effect 里 fetch 后 `clearRun`；sticky 照 SubagentTranscript 的 STICK_PX 模式）。
- [ ] **Step 4: 绿（swarmRun + swarmStore + 全量 `pnpm test`）+ `pnpm build`。**
- [ ] **Step 5: Commit** `feat(desktop): task detail streams live from swarm events`

### Task 5: mock + e2e + 文档

**Files:**
- Modify: `desktop/src/dev/swarmMock.ts`、`desktop/e2e/swarm.spec.ts`、`CHANGELOG.md`、`docs/guide/swarm.md`（一句"任务详情实时"）

- [ ] **Step 1: swarmMock 脚本链在 running 期 emit `swarm_event`**（tool_execution_start → update → end → message_update delta → message_end new_messages），e2e 新例：打开 running 任务详情，断言完成前已见工具行（`data-swarm-task-detail` 内 tool 节点）。
- [ ] **Step 2: e2e 全量绿。**
- [ ] **Step 3: CHANGELOG（Swarm 小节追加）+ guide 一句。**
- [ ] **Step 4: Commit** `feat(desktop): live task detail e2e and docs`

### Task 6: 全门禁 + 终审

- [ ] **Step 1: `scripts/check.sh` + desktop 三件套 + `pnpm e2e` 全绿。**
- [ ] **Step 2: 全新子代理终审（spec + Review Focus 逐项）；Critical/Important 即修即绿。**
- [ ] **Step 3: 汇报（提醒：GUI 需重启加载新 Python；前端 vite 热更已生效）。**
