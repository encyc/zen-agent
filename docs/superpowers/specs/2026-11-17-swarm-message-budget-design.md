# Swarm 消息预算层（Message Budget）设计

> 状态：已实现（0.13.x）。增补 [2026-11-10-swarm-runtime-design](2026-11-10-swarm-runtime-design.md) §8 的结构性闸门表。

## 1. 现象与根因

一次真实测试：用户 `@a 叫其他人出来打个招呼`。a 的最终文本 `@b @c @d`，b/c/d 各自的
最终文本又 `@` 其他角色；因为每次 run 之间有延时，"我已经打过招呼"这件事对任何
单个角色都不可观测，于是招呼不断变成新的招呼，房间消息数指数增长。

拆开看是四个机制同时成立：

1. **`@` 同时是寻址和派活**，而 agent 的最终文本既是"结果"又是"派活消息"——回复即请求。
2. **广度没有闸门**。已有的是链深（8）、链花费（$5）、每角色 inbox（16）、并发（3）。
   inbox 是队列长度，不是总量；链深 8 配上 3 个角色就是 `O(k^8)`。
3. **钱不能当预扣**。`chainCost` 只在 run 结束后累加，并行分支读到的是同一份陈旧值，
   于是大家一起越过阈值——典型超卖。而打招呼几乎不花钱，$5 拦不住。
4. **没有终止判定**。异步消息系统里"已经干完"需要显式的静默/终止检测协议；这里没有，
   模型也没有"这条会话已达终态"的概念，社交先验反而鼓励回礼。

延迟不是起因，它是放大器：它让"是否已经回执过"从幂等问题变成不可观测问题。

## 2. 目标 / 非目标

目标：

- 让**"一条链最多产生多少任务"**成为与模型行为无关的硬不变量。模型可以无限 `@`，
  受限的是房间，不是礼貌。
- 保留两条既有主路径：结果 `@` 派活者拿回执；结果 `@` 下一个角色继续链式作业。

非目标：

- **不改轮次调度（BSP）**。把自由异步改成同步轮次会打断 `swarm_event` 的逐步流式推送，
  且现在的排队模型已经保证每 run 单元串行；换来的是体验倒退。放弃。
- **不引入 LLM 监督者**。概率性组件不能充当不变量，只能当软闸；硬预算必须在代码里。
- **不改 work item 语义**。跨 work item 的链重基保持不变。

## 3. 五层落在哪里

| 层 | 机制 | 位置 |
|---|---|---|
| L0 语义 | `intent` = `request` / `reply` / `note`；回复不是广播 | `gate.py` |
| L1 消息头 | 链路仍由 `taskId → task.chainId` + `chainDepth` 追溯；intent 在 admit 时派生 | `room.py`（既有）, `gate.py` |
| L2 拓扑 | 扇出上限：request ≤ 4，reply ≤ 1，note = 0；边重复上限 | `gate.py` |
| L3 去重/收敛 | 同 `(chain, from, to)` 边最多 2 次，掐断 ping-pong | `gate.py` |
| L4 预算/熔断 | 链任务数硬预算 24（精确计数）；60s 内 12 条即熔断 | `gate.py`, `router.py` |
| L5 回归 | "打招呼风暴"用例：模型永远 `@` 所有人的最坏情况 | `tests/test_swarm_storm.py` |

## 4. 规则（可判定、与模型无关）

### 4.1 intent 分类

分类是**结构性**的，不读模型的自述：

- system 消息、无 mentions 的消息 → `note`（不派活）
- agent 消息且 `id == task.result_message_id` → `reply`（它是某条任务的产出）
- 其余（用户发帖、`swarm_post`）→ `request`

`reply` 的判定依赖 runner 的写入顺序：先 `record_task(done, result_message_id=posted.id)`，
再 `admit_message(posted)`。这个顺序是契约，写在 `runner.py` 的注释里。

### 4.2 闸门顺序（先链级，后逐 mention）

1. **熔断**：本链 60 秒内已建任务数 ≥ 12 → 全部拒，原因 `chain_burst`。
2. **链任务预算**：本链已建任务数 ≥ 24 → 全部拒，原因 `chain_tasks`。
3. **扇出**：按 mentions 出现顺序取前 N 个（N 见上），其余逐个拒，原因 `fanout_cap`
   （reply 用 `reply_cap`，便于人读懂是哪条规则）。
4. **逐 mention**：边重复（`edge_repeat`）→ 链深（`chain_depth`）→ 链花费（`chain_cost`）
   → inbox（`inbox_full`）。

前三条是新增的，后两条是既有行为，原因词不变——既有测试与房间提示因此保持原样。

### 4.3 为什么任务数预算比美元预算可靠

任务计数在 `admit` 时同步追加，`len([t for t in tasks if t.chain_id == c])` 就是精确值，
不存在超卖。美元预算只能事后结算，天然滞后。所以**任务数当预扣，美元当兜底**。

## 5. 不变量

对任意模型输出序列：

- 每条 chain 的已建任务数 ≤ `max_chain_tasks`（24）
- 同 `(chain, from, to)` 的已建任务数 ≤ `max_edge_repeats`（2）
- 一条 `reply` 消息产生的任务数 ≤ 1
- 上述三条不读模型文本，只读已落盘的任务表

因此最坏情况有界，且界与角色数、模型话痨程度无关。

已知的逃逸路径只有一条：**跨 work item 时链会重基**（新链、新预算）。这是有意的——新工作
本来就该有新预算——而且它留下房间卡片，人看得见。`work_open` 的"重复调用复用空 item"
已经挡住了"每轮开一个新 item"的机械用法。

## 6. 兼容性

- 六个闸门是 `[swarm]` 的配置项（global-only，和 `max_concurrent` / `max_chain_depth`
  同一节），经 `SwarmLimits.from_policy(config)` 注入 `Router`，设置页可直接改。
  它们和既有四项一样在下一个房间重建时生效：有任务在飞的房间保留旧策略。
- `UserConfig` 继承 `zenagent.swarm_settings.SwarmPolicy`，十个 `swarm_*` 字段与解析器
  都在那个模块里；`zenagent.limits` 的注册表用 `DEFAULT_SWARM_*` 常量作默认值，
  三方（注册表 / 解析器 / 数据类）不可能各自漂移。
- `intent` **不落盘**。它能被判定，靠的是"消息 id 等于某任务的 `resultMessageId`"，
  而这条记录在消息写进 `room.jsonl` 之后才追加——房间是 append-only 的，回填等于篡改。
  因此 intent 在 admit 时派生，拒绝原因（`reply_cap` 等）作为房间系统消息留在日志里，
  可观测性不打折。链路追溯继续走既有的 `taskId → task.chainId`。
- 既有 `test_mutual_mentions_stop_at_the_depth_cap` 断言的是旧防线（链深 8 才停）。
  新行为是边上限先停，因此该用例改为断言 `edge_repeat`（并改名为
  `test_mutual_mentions_stop_at_the_edge_cap`）；链深闸门由
  `test_chain_depth_cap_rejects` 单独覆盖。
