# 无策略上限的 Agent Loop 设计

> 状态：待批准（§8 记已定项）。移除全部轮次与花费上限，守卫从劝导改为打断。

## 1. 现象与根因

`agent_loop` 目前有八处 `break`。其中四处不是「模型停下了」也不是「被叫停了」，而是
harness 按**数量或金额**猜「你该停了」：`max_turns`（100 轮）、`max_cost`（默认关）、
子代理 300 轮 / 每任务 $N、goal 40 轮、swarm 角色各自一套。这些数没有任何共同依据，
只能说明它测的不是「做完了」，而是「大概该完了」。

| 类别 | 谁决定停 | harness 是否越权 |
|---|---|---|
| 模型自己不再请求工具 | 模型 | 否 |
| provider 失败 / 用户 abort / 输出被截断反复失败 | 传输层 / 人 / 物理不可能 | 否 |
| **轮次到线、花费到线** | **harness 自己算的** | **是** |

它还有一个结构性副作用：**它占着「打断」这个唯一的硬手**。于是两个循环守卫只能做
*劝导*——把一条纠正消息写进 transcript，指望模型自己出来。而写进 transcript 就意味着：
`loop.py` 在 `finally` 里整表过滤一次（`loop.py:505-510`）、`views.py` 按前缀再挡一次
（`views.py:428`）、两个前缀常量从 loop 导出、`guard.py` 在 docstring 里解释这套规避。
三个组件都知道一个不该存在于 transcript 的概念。这个脏不是启发式造成的，是「只能劝导」
造成的。

## 2. 目标 / 非目标 / 反目标

**目标**

- kernel 里不存在任何「按轮次或花费替模型决定停」的策略。
- 移除的旋钮：`[limits.agent].max_turns`、`[task].max_turns`、
  `[limits.goal].max_turns`、swarm manifest 的 `maxTurns`；以及 kernel `AgentConfig.max_cost`、
  `--max-cost`、`[task].max_cost`、`[limits.subagents].max_cost`、swarm manifest 的 `maxCost`。
- 守卫的动作从*劝导*改为*打断*，因此它不再需要触碰 transcript：`finally` 的过滤、
  `views.py` 的分支、两个前缀常量一并删除。

**非目标**

- 不动 provider 重试（`stream_with_retry` 保持）。
- 不动**看门狗**：工具 `timeout_s`、子代理 `timeout_s`。它们拦的是「卡死」，不是「做多了」。
- 不动 goal 的 `max_tokens`/`max_wall_minutes`（见 §8.3，待你一句话）。
- 不引入新的「智能」停止策略（LLM 判官、相似度阈值等）。概率组件不能当不变量。
- 不改 `no_stream`、`_MAX_LENGTH_FAILURES` 两个防御分支（见 §5.1）。

**反目标**（写清楚了就不许做）

- 把 `max_turns` 换成 `max_cost`/`deadline` 并默认打开——那是同一个猜测换单位。
- 让守卫变成会误杀的静默杀手：打断必须可解释（撞的是哪个工具、重复几次），且必须可恢复
  （run 结束、会话健在，一条新 prompt 就能继续）。

## 3. 约束

1. **已发布包。** PyPI 0.14.1 用户的 `config.toml` 里可能写着这些键。两条校验路径都
   **拒绝未知键**：`[limits]` 走 `parse_limits`（`limits.py:668`），`[task]` 走
   `config.py` 的 `_TASK_KEYS`（`config.py:88,232`）。直接删键会让老配置启动失败。
   必须留墓碑键，且**两条路径各实现一次**。
2. **线协议。** `agent.termination` 是对外字符串（`docs/guide/rpc-sdk.md:143`、
   `docs/guide/sessions.md:49`），desktop 消费：`AuxRows.tsx:83` 直接打印原值，
   `chatStore.ts:625` 把未知值映射成 "recovered"。
3. **状态映射有默认分支。** `status_for_termination`（`app/runs.py:104`）对未列出的
   termination 返回 `done`；子代理 `_status_for`（`subagents_runtime.py:182`）落 `ok`；
   goal 对非 `stop` 一律 `blocked`（`goal.py:491`）。加新值必须同时改这三处。
4. **测试面。** 约 90 处引用散在 12 个测试文件（`test_limits.py` 21 处最多），多数是
   「限制生效」的正向断言，要改成「不受限」的断言，而不是删掉。
5. **历史可读。** 旧 session 与旧 ledger 行里已有 `max_turns`/`max_cost`，消费侧的映射
   必须保留这两个字符串，哪怕生产者不再产生。

## 4. 方案

| 方案 | 做法 | 评价 |
|---|---|---|
| A. 只打断可证明的 | 轮次与花费旋钮全删；守卫改成打断 | **选中**。kernel 里停止只剩两个来源：模型自己，或证明它原地打转 |
| B. pi 式纯内核 | cap 与 guard 一起出 kernel，检测移到 app 层 | kernel 最干净，但 SDK 直连 kernel 的用户零保护，且 app 层要重写签名比较——同一段代码换个家 |
| C. 保留有界，换成时间 | `max_turns` → `run_timeout_s` | 改动最小，但「按数量猜」没解决，transcript 的脏也还在 |

## 5. 设计

### 5.1 kernel：终止面收敛

| 条件 | `termination` | 说明 |
|---|---|---|
| 模型不再请求工具 | `stop`（或原始 stop_reason） | 现有 |
| provider `error` / `aborted` / abort token | `error` / `aborted` | 现有 |
| 工具批全部要求 terminate | `tool_terminate` | 现有 |
| 守卫证明原地打转 | `loop_detected`（新增） | 见 §5.2 |
| 流没有终局事件 | `no_stream` | 防御，保留 |
| 连续两轮输出被截断 | `length`（**改**：现报 `stop`） | 防御，保留 |

删除：`_MAX_TURNS`、`AgentConfig.max_turns`、`AgentConfig.max_cost`、`_run_cost`、
`AgentState.subagent_spend`（三处只服务于花费上限；子代理花费仍写进 `details` 供显示）。

`_MAX_LENGTH_FAILURES`（`loop.py:174,435`）**保留**：它拦的不是「做得太多」，而是「参数被
截断了，再问一次还是截断」——物理不可能，不是猜测。但它现在 `break` 时没有赋值，落成
`termination="stop"`，让 ledger 记成成功、goal 当成正常一轮。改为显式 `length`。

### 5.2 守卫：一个检测器，一个动作

`guard.py` 保留一个 `RunawayDetector`：

- 判据：同一批工具调用的**字节相同签名**连续出现 N 次（`[limits.agent]
  loop_guard_threshold`，默认 5）。
- 豁免：poll 类命令（`sleep/wait/tail/ps/pgrep/top/jobs`）不计入连续——它们观测的是会变的状态。
- 动作：`termination="loop_detected"`，detail 形如 `3× bash({"command":"pytest -q"})`，
  随 `agent_end` 发出。**不写任何消息**，不碰 `state.messages`。

**detail 怎么过线**：`AgentEvent` 目前只有 `termination`（`events.py`）。新增可选
`detail: str | None`，只挂在 `agent_end`；RPC 事件表（`cli/rpc/protocol/_chat.py`）加同名
可选字段并重新生成 `desktop/src/rpc/wire.gen.ts`；desktop 把 `loop_detected` 映射成一句
人话（现在是原样 token 或 "recovered"）。

**删除** `EvidenceLedger` 与 evidence-stall 守卫。它在劝导语义下是无害提醒，在打断语义下
会误杀——判据本身不可证明（文件可能被别的进程改了，它自己的 docstring 就写着）。降级成
「只在请求期注入」又违反「不往上下文里塞幽灵消息」。

代价要认：模型把同一页用不同 flag 重读十二轮时，不再有任何提醒。这是有意放弃的。

### 5.3 拆除清单

| 位置 | 删 / 改 |
|---|---|
| `agent/loop.py` | `_MAX_TURNS`、`max_turns`/`max_cost` 分支、`_run_cost`、`AgentState.subagent_spend` |
| `agent/agent.py`、`app/runtime.py`、`app/bootstrap.py` | 对应的 `max_turns`/`max_cost` 参数与透传 |
| `limits.py` | `[limits.agent] max_turns`、`[limits.goal] max_turns`、`[task] max_cost` → 墓碑键（§5.4） |
| `config.py` | `DEFAULT_TASK_MAX_TURNS`、`DEFAULT_TASK_MAX_COST`、`task_max_turns`、`task_max_cost`、`_TASK_KEYS` 条目 |
| `cli/flags.py`、`app/limits_apply.py` | `--max-turns`、`--max-cost` 及其覆盖逻辑 |
| `tools/task.py` | `max_turns`/`max_cost` 参数、`truncated reason=max_turns`；`timeout_s` 保留 |
| `tools/wait.py` | 注释里的 `max_cost guard` 措辞 |
| `app/goal.py` | `max_turns`、两个 turn 预算分支、**wrap_up 机制**（§5.7） |
| `app/swarm/{roles,roster,manager}.py`、`app/zen/fleet.py` | `max_turns`、`max_cost` 字段与透传 |
| `app/runs.py` | 新增 `loop_detected` → `interrupted`；**保留** `max_turns`/`max_cost` 历史值 |
| `agent/subagents_runtime.py` | 新增 `loop_detected` → `truncated`；保留历史值 |
| `desktop/` | goal 分母、`agentEditor` 的轮次/花费输入、`dev/bridge.ts` fixture、`swarmMock`、`types-swarm.ts`、i18n 四条 × 两语言 |
| `docs/` | `guide/config.md` 两段、`guide/rpc-sdk.md`、`guide/sessions.md`、`guide/commands.md`、`architecture.md` |

### 5.4 配置迁移：墓碑键

四条键（agent/task/goal 的 `max_turns`、task 的 `max_cost`）同语义，实现分两处：

- `limits.py`：`Limit` 加 `deprecated: bool = False`；`parse_limits` **接受并忽略**，
  `write_managed` 不再写。
- `config.py`：`_TASK_KEYS` 保留这两项为「接受并忽略」，与 limits 共用一个警告出口。

启动时每键警告一次：
`[limits.agent].max_turns 已移除：run 只在模型自己停止或原地打转时结束；此键被忽略`。

### 5.5 线协议与历史

- 新增 `termination="loop_detected"`、`agent_end.detail`（可选）。
- **生产者停止产生** `max_turns`/`max_cost`；**消费者保留**这两个字符串（旧 session、
  旧 ledger、desktop 都已存在），文档里标注为历史值。
- desktop 未知 termination 的回退要改，否则打断显示成 "recovered"。

### 5.6 状态分类

| 消费方 | `loop_detected` | 理由 |
|---|---|---|
| run ledger | `interrupted` | 词表里已有（`RUN_STATUSES`）；不是模型错了，是被叫停 |
| 子代理 | `truncated` + reason | 与旧 `max_turns` 同类，但不再有轮次 |
| goal | `paused` + reason | §2 要求可恢复；`blocked` 是终态，与要求矛盾 |

### 5.7 goal 的 wrap_up：删除

现状核查：wrap_up **只**由轮次触发（`goal.py:520-527`）；token/wall 预算到线直接
`_finish("budget_limited")`，没有收尾轮，`GoalDecision.wrap_up` 字段设了但没有任何读取方。
轮次触发删除后它是死代码。

删掉：`GoalDecision.wrap_up`、`render_goal_prompt(wrap_up=…)` 分支、轮次收尾那段。
goal 剩余的停止来源：check 的 verdict、blocker、用户、以及 §8.3 待定的两个预算。

## 6. 验证

- 新增：重复调用 5 次 → `termination="loop_detected"`；`agent_end.new_messages` 里**没有**
  合成消息；`state.messages` 与 run 前只差模型的真实消息。
- 新增：poll 命令重复 20 次不触发。
- 新增：`loop_detected` 在 ledger / 子代理 envelope / goal HUD 三处显示为预期状态。
- 新增：`--max-cost` 不再存在，`[task.max_cost]` 配置被忽略且警告一次。
- 修改：12 个测试文件里的「到线就停」断言改为「不受限」。
- 迁移：带四条旧键的老 config 能启动，各警告一次，`limits` RPC 不再列出它们。
- 手工：headless one-shot 跑长任务，确认不再在 100 轮被截断。

## 7. 留给 builder 的

- 墓碑键警告的文案归属（kernel 还是 desktop i18n）。
- `loop_guard_threshold=5` 是否随之调整（我的判断：不动）。
- `SubagentLimits` 删空后是否还需要这个类型。
- `agent_end.detail` 的字段名（`detail` / `reason` / `termination_detail`）。

## 8. 已定与最后一处

1. **`max_cost` 全删**——kernel、CLI、task、swarm、zen fleet 一起走，不再有任何花费上限。
2. **goal wrap_up 删除**——它只由轮次触发，轮次走了它就是死代码。
3. **goal 的 `max_tokens`(2M) / `max_wall_minutes`(240) 我按同一原则也删了**，理由：这两个
   数和 40 一样是编出来的，而 goal 真正的停止依据是 check 的 verdict。不同意就说一声，
   恢复它们只是两个 `Limit` 条目加两个分支。
