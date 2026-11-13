# 架构诊断：三种组织方式，还是三个产品？

日期：2026-11-12 · 范围：coding / zen / swarm 三种 workspace 形态 · 基线：`b9adc5c`

## 结论

用户对系统的原始理解是正确的：**一个内核 + 一套共用基础设施 + 三种组织方式**。
代码没有按这个理解生长。实际形态是：

```text
coding:  UI ── RPC slot ── Runtime ── Agent
zen:     UI ── RPC slot ── Runtime + ZenFleet ── prepare() ── 临时 Runtime ── Agent
swarm:   UI ── RPC host ── SwarmManager ── Runtime.bootstrap() ── Agent
                              └─ 自己的 scheduler / approvals / room / tasks / workitems
```

三者共享的边界止于 `Runtime.bootstrap` 以下。`Runtime` 之上，zen 和 swarm 是两套独立
产品：各有调度器、名册、状态机、审批通道、持久化格式和 RPC family。coding 没有模式
对象，它是“没有 fleet、没有 room”的缺省态。

因此“不可控”不是使用问题，是结构必然：同一件事——谁在跑、跑到哪、花了多少、为什么停
——系统里有三套答案，且没有一套是权威的。

## 初心 → 可检验的约束

1. 一个内核契约：`Agent` / `Session` / 工具 / Provider 在三种模式下语义一致。
2. 一套共用基础设施：装配、权限、持久化、事件、审批、调度原语是同一套。
3. 模式只是拓扑与策略：coding = 1 agent；zen = router + N worker；swarm = N 个长生命周期
   agent + 工单。
4. 模式是后端运行时状态：可查询、可切换、可恢复，不是 UI 标签。
5. 一套运行账本：任何模式都能回答“谁在跑、跑到哪、花了多少、为什么停”。

判断标准：**新增一种组织方式，只应新增策略、拓扑和 UI，不应新增调度器、状态机、
持久化格式和审批通道。** 按这个标准，zen 和 swarm 都不合格。

## 五个缺口

### 1. Run 不是一等公民

内核持久化消息，不持久化“一次运行”。`Session` 有 `run_start` / `run_end` 侧信道，但没有
`run_id`、`agent_id`、成本与归属；`AgentEvent` 是 runtime-only。于是 coding 用
`ChatController`、zen 用 `WorkerHandle`、swarm 用 `TaskRecord` 各补一套状态。

最直接的证据：swarm 的任务执行详情靠在 session 文件里找以 `[swarm] from ... task: <id> ·`
开头的提示词切片；提示词格式一改、session 被 reset/compact，历史就静默消失。

### 2. Agent 不是一等公民

系统里有四种 agent 生命周期，不是三种：

| 形态 | 生命周期 |
|---|---|
| coding 主 agent | session 级，run 级复用 |
| `task` 工具子 agent | run 级，临时、只读、无独立 session |
| zen worker | session 级，每次 dispatch 重建 Runtime，跑完关闭 |
| swarm role instance | `(role, work_item)` 级，懒建，角色只是配置 |

“多个全生命周期 Agent”在 swarm 里实现成了“工单级实例”：同一 role 在两个工单里就是两个
agent、两份 session、两套 runtime；跨工单没有 agent 连续性，只有 manifest 连续。

而且 swarm 的 `(role, work_item)` runtime 只建不关：`IdleTracker.idle()` 没有调用者，
计划里写了空闲回收但实现只完成懒建。

### 3. 编排没有共同底座

zen 与 swarm 在概念上高度重合，但代码零交叉：两个 roster、两个调度器、两套状态词、两套
审批、两套持久化。合并设计明确写了“不做公共抽象”（`docs/superpowers/specs/
2026-11-11-zen-swarm-merge-design.md`），把复制变成了显式决策。

局部合理性：zen 需要 `ChatController` 注入报告，swarm 不能用 `ChatController`（它是 UI 绑定
的）。正确结论应是“把无 UI 的 RunManager/MessageBus 抽出来”，而不是“两个都别抽”。

**策略可以不同，机制应该共享。** 现在共享的是内核，重复的是机制，正好搞反了。

### 4. 模式没有后端实体

`workspaceMode` 是纯前端状态（`2026-11-10-swarm-runtime-design.md` §5 明说“后端无感”）。
模式切换不是事务，而是一串异步动作：先写 localStorage，再 fire-and-forget 调
enable/disable，失败后再回滚。后端靠“`zen_fleet` 是否为 None”“swarm room 是否存在”反推
模式。

后果：UI 和后端可以短暂或长期不一致；快速切换、busy 拒绝、后台 slot、项目切换都会放大。
更严重的是语义不对称：离开 zen 会 abort 所有 worker；离开 swarm 不会停 room。同一个三态
切换器，一个“离开即停”，一个“离开继续跑”。

### 5. Runtime 没有把装配与策略分开

`Runtime` 本应是“把一个 agent 装起来”的地方，现在是所有模式的策略交汇点：

- `RuntimeOptions` 里的 `tools_allow` / `extra_tools` / `extra_prompt` / `project_sessions`
  是 role 装配字段；
- `Runtime.zen_fleet` 槽位、模块级 import `app.zen.identity`；
- `ops_zen` 和 `swarm/manager` 从外部调用私有 `_recompose()`；
- 提示词散落在 `mode_prompt_note`、`ROUTER_PROMPT_NOTE`、role `extra_prompt`、
  worker user prompt 四处。

每加一个模式能力，最省事的路径都是往 `Runtime` 加 flag 或槽位；核心逐渐变成所有模式的并集。

## 为什么感觉不可控

1. 切模式后状态对不上：UI 先写 localStorage，后端异步 enable/disable，失败后回滚。
2. 不知道谁在跑、花了多少：三套状态机，没有跨模式账本。
3. 越用越重：swarm 的 `(role, workitem)` runtime 懒建不回收，每个实例可能带 MCP 子进程。
4. 同样的工具审批行为不同：coding 用户确认；zen worker 降级 ask；swarm 自有 future，
   ask 自动 dismiss。
5. 历史会坏：swarm 任务详情按 prompt 文本切片；reset/compact 后显示“记录不可用”。
6. 切换器的承诺与系统行为不一致：UI 像换主题，后端在换调度器、session 所有权、审批通道。

## 根因

1. **内核只把消息当一等公民，没有把 Run 和 Agent 当一等公民。**
2. **组织层被推迟，而推迟的代价被 zen 和 swarm 两次以“复制”支付。**
3. 治理指标奖励切分而不是收敛：LOC 门禁只量文件大小；merge 设计把“不做公共抽象”写成
   决策；健康报告把 113 项违规登记成基线。于是“再复制一套”永远是局部最省力的路径。

## 收敛路径

Phase 1（本分支已做）：RunLedger + `runs` RPC + Swarm 空闲回收。
Phase 2：Session writer lease（一个 session 同时一个 writer；router 槽位独占）。
Phase 3：Supervisor + AgentProfile，把 role 字段移出 `Runtime`。
Phase 4：`workspaceMode` 收到后端，模式切换变成幂等事务。
Phase 5：统一 zen/swarm 的 scheduler / approval / roster，RPC 收敛到 `supervisor` 请求族。
Phase 6：`supervisor dispatch` 适配器——一个派活入口，zen/swarm 各自保留调度策略。
Phase 7：run 账本崩溃对账 + `subagent` 记账（父 session / 父 run 归属）。

目标结构：

```text
UI（模式只是视图）
   │ typed supervisor RPC
Supervisor（项目级，后端持有）
   ├─ AgentRegistry: AgentId -> AgentProfile + Runtime + Session
   ├─ RunLedger:    RunId -> {agent, work_item, parent, status, cost, events}
   ├─ Scheduler/Pool: 并发 + 预算 + 空闲回收
   ├─ MessageBus:    deliver(target, message, correlation)
   └─ ApprovalRouter: run_id / agent_id 类型化路由

模式 = 策略：coding / zen / swarm
内核不变：Agent / Session / tools / providers
```

一句话：**现在的问题不是三个模式太多，而是三个模式没有共同的运行模型；不是内核不稳，
而是内核上面的“谁在跑”没有主人。**
