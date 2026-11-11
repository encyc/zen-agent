# Swarm 任务详情实时化设计（事件流推送）

日期：2026-11-11
状态：已批准（用户选 A：与 coding 相同的事件流体验）
前置：merge/zen-swarm 分支（zen × swarm 合并完成后）

## 意图

打开一个 running 中的 swarm 任务详情时，步骤内容要**实时流入**（打字机文
本、工具卡 running→ok/error、progress 行），与 coding 会话同构；而不是
"run 结束后整段突然出现"。

## 实证根因（复现于 2026-11-11）

- run 期间角色 session JSONL **一行不写**（8s run 实测 file_lines 不变，
  结束 26→32 跳变）：`Runtime` 的 `persist` 订阅者只在 message 完成事件
  追加，注入 prompt 与进行中工具不入盘。
- 因此 `read_task_transcript`（按 `[swarm] from … task: <id>` 锚点切片）
  在 running 期间 `available=False, rows=0`——**回放文件路径结构性拿不到
  进行中数据**；1.5s 轮询无论多快都无济于事。
- 事件在内存里是全的：`AgentListener = Callable[[AgentEvent], Awaitable|None]`，
  `agent.subscribe()` 即龙头。`SwarmManager.stream_fn_factory` **不是**事
  件接缝（那是模型 provider 替换口）。

## 方案（A，已选；B 弃）

**A 事件流推送**：角色 Agent 的事件订阅 → host 层 `swarm_event` 全局帧 →
前端视图级迷你 reducer → 任务详情面板实时渲染。与 coding 的
EventFrame→chatStore.applyEvent 同构；帧形状对齐，为下一阶段 zen
WorkerDrawer 实时化预留同构路径。

B（照抄 SubagentTranscript 的游标增量轮询）弃：后端需给 manager 加 run
内存 buffer + transcript op 游标，改动更深，且仍是 ~1s 轮询、无打字机，
不满足"跟正常 session 一样"。

## 设计

### 后端（zenagent）

- `SwarmManager.__init__` 新增 `event_sink: Callable[[str, str, AgentEvent], None] | None`（role_id, task_id, event）。`stream_fn_factory` 保持原义（模型口）。
- `_build_agent`：bootstrap 后 `agent.subscribe(lambda e: self._tap_role_event(role.id, e))`（sync listener，异常自吞——订阅者契约 best-effort）。
- `_execute`：run 前 `self._active_tasks[role.id] = task.id`，finally `pop`。`_tap_role_event` 查 map；无 active task（rebuild 等）或无 sink → 丢弃。
- `ops_swarm._swarm_manager`：传
  `event_sink=lambda role, task, event: self._send({"type": "swarm_event", "swarm": True, "agent": role, "task": task, "event": serialize_event(event)})`。
  serialize_event 在 cli 层（codec）✓ 不破坏 app/cli 分层。
- 帧量：≤3 并发角色 × token 级事件，与 coding 单会话同量级；不做节流（coding
  也不做），记入风险表。

### 前端（desktop）

- `rpc/types.ts`：`SwarmEventFrame { type:'swarm_event'; swarm:true; agent:string; task:string; event:AgentEventFrame }` 并入 ServerFrame。
- `swarmWiring.applySwarmFrame`：`swarm_event` → `useSwarmStore.applyRunEvent(frame)` → consumed。
- `state/swarmRun.ts`（新，纯函数）：`applyRunEvent(rows: RunView[], event: AgentEventFrame): RunView[]`，视图级 reducer（RunView = MessageView | ToolCallView）：
  - `message_start(user/skill)` → user/skill 行；`message_update.assistant_event.text_*` → 尾部流式 assistant 行（text += delta，thinking 同法）；`message_end` → 以 `new_messages` 的权威视图替换流式尾行。
  - `tool_execution_start/update/end` → 按 `call_id` append/patch ToolCallView（status running→ok/error、progress、result_text、duration_ms）。
  - 其余（agent_*/turn_*/approval/terminal）忽略。
- `swarmStore`：`runRows: Record<taskId, RunView[]>` + `applyRunEvent`（经 reducer 合并，每 task 上限 1500 行、丢头）+ `clearRun(taskId)`；不随任务终态自动清（面板终态对账后清）。
- `TaskDetailPanel`：
  - running/queued → 渲染 `runRows[task.id]`（此时文件 seed 恒空，live 即全部）；
  - 打开 running 任务 = 从"现在"开始流（错过开头，终态对账补全）——与中途打开 coding 会话的语义一致；
  - 终态（active 翻转）→ 现有一次性 transcript fetch 返回权威全量视图 + `clearRun` 释放内存；
  - sticky 滚动（80px 阈值，照 SubagentTranscript）。

### mock/e2e

- `dev/swarmMock.ts`：脚本链在任务 running 期间 emit `swarm_event` 帧（tool_execution_start/end + message_update delta + message_end new_messages）。
- `e2e/swarm.spec.ts` +1 例：打开 running 任务的详情，断言 live 行在任务完成前已可见。

## 非目标

- zen WorkerDrawer 实时化（下一阶段公共抽象，帧思路可复用）。
- 后端 run 内存 buffer / transcript 游标（B 方案遗产，不需要）。
- 事件节流/按面板订阅裁剪（先全量，量级论证见上）。

## 风险

| 风险 | 对策 |
|---|---|
| 3 并发角色 token 级帧量 | 与 coding 同管线同量级；若实测卡顿再加 30ms 合并（记 deferred） |
| 面板中途打开缺开头 | 终态文件对账补全；语义对齐 coding 中途打开 |
| 事件乱序/丢失 | reducer 以 `new_messages`（带稳定 `v{n}` id）为权威覆盖流式行；终态以文件为准 |
| rebuild 期间事件 | `_active_tasks` 无记录即丢弃 |
