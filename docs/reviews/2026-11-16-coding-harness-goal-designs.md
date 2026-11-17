# Coding Harness 的 Goal 功能设计调研（2026-11-16）

- 范围：Claude Code、OpenAI Codex、Cursor、Grok Build、OpenCode，以及社区可移植实现
- 方法：官方文档 + 源码（Codex `codex-rs/ext/goal`、`ThreadGoal`）+ 第三方对比文章 + loop engineering 论文
- 时点：2026-11-16。`/goal` 仍在快速演进，具体行为以各家当天的文档/源码为准

---

## 0. 结论摘要

2026 年这波 `/goal` 不是「更长的 prompt」，而是把 agent 从「一次请求 = 一次任务」
改造成「一个会话 = 一个带完成条件的控制循环」。各家实现细节不同，骨架高度一致：

> **目标即契约、完成靠证据、续跑靠事件、状态挂在线程/会话上。**

一个完整的 goal 系统由五部分组成：

1. **Goal state**：objective、status、token/turn/time 用量、所属 thread/session。
2. **续跑触发器**：turn 结束、idle、定时或外部事件。
3. **完成判定**：独立裁判、模型自报、评审团，或确定性命令。
4. **预算与终止**：token 预算、回合预算、超时、卡死检测、命名终止状态。
5. **上下文与持久化**：每轮重注目标、resume、compaction、磁盘状态。

设计质量的分水岭不在「怎么让模型继续跑」，而在：

- 目标是否**可失败**（有没有一个真的能判 false 的检查）；
- 完成判定是否**独立于执行者**（maker-checker）；
- 终止状态是否**命名且互斥**（错误/预算耗尽不能算成功）；
- 续跑是否**事件驱动且有刹车**（失败不重启、空转/执行失败有阈值）；
- 状态是否**持久化且线程隔离**（resume、并发、子会话）。

---

## 1. 统一运行模型

```text
/goal <objective>
        │
        ▼
   Goal: active ──► turn 1（objective 本身作为 prompt）
        ▲                    │
        │                    ▼
        │            turn end / idle
        │                    │
        │              ┌─────┴─────┐
        │           未达成       达成 / 不可能
        │              │             │
        └──────────────┘             ▼
           自动续跑            complete / clear
```

与 plan mode、todo 的区别：

| 概念 | 回答的问题 | 生命周期 |
|---|---|---|
| plan | 动手前打算怎么做 | 一次设计，可审批 |
| todo | 这一轮还剩哪几步 | 一轮/一个任务内 |
| goal | 跨轮次「什么算做完」 | 跨 turn，直到终态 |

`/goal` 与 `/loop` 也不同：`/goal` 由**条件**驱动，`/loop` 由**时间**驱动。

---

## 2. 各家实现

### 2.1 Claude Code：独立小模型裁判 + Stop hook

官方文档：<https://code.claude.com/docs/en/goal>

- **设定**：`/goal <condition>`，条件最长 4,000 字符；一个 session 只能有一个 active goal，
  新 goal 替换旧的；`/goal clear`（别名 `stop`/`off`/`reset`/`none`/`cancel`）清除。
- **首轮**：设置 goal 后立即开始一轮，条件本身就是 directive。
- **判定**：`/goal` 是 session-scoped **prompt-based Stop hook**。每轮结束把条件和对话
  发给配置的 small fast model（默认 Haiku 一类），返回三态：
  - **Not yet met**：继续，理由作为下一轮指导；
  - **Met**：清除 goal，记录 achieved；
  - **Impossible**：清除 goal，记录 failed + 理由。
- **裁判限制**：裁判不调用工具、不读文件，只能看到 transcript 里已经出现的内容。
  所以「tests pass」必须由 agent 真的跑过并把输出写进对话，否则裁判看不见。
- **续跑边界**：
  - 后台 subagent / shell 还在跑时，**跳过本轮判定**，等它们结束；
  - 连续多轮没有工具调用（空转）时停止循环、保留 goal、把控制权交还用户；
  - 后台工作持续 30 分钟触发 check-in，之后按 1h/2h 退避，最多 3 次 idle check-in；
  - 不可恢复错误（认证、余额、context overflow、模型不可用）清除 goal；
  - 可重试错误（过载、断连）自动重试 3 次后暂停；限流/用量上限直接暂停。
- **权限**：goal 不改变 permission mode。Manual 模式下该问的还会问；Auto mode 只负责
  单轮内自动批准工具，不负责开始下一轮。
- **持久化**：session 级；resume 会恢复仍 active 的 goal，但 turn 数、计时、token 基线重置。
- **状态展示**：`/goal` 无参数显示条件、运行时长、已评估轮数、token 花费、裁判最近理由。

**设计要点**：maker-checker 分离做得最干净；代价是裁判只看 transcript，且判定是
模型意见而非现场事实。

### 2.2 OpenAI Codex：ThreadGoal + 模型自报 + 运行时硬约束

源码：`codex-rs/ext/goal/`、`codex-rs/state/src/model/thread_goal.rs`

Codex 的状态机是几家里最完整的：

```text
active ──► complete
   │
   ├──► paused          （只能由用户显式暂停）
   ├──► blocked         （同一阻塞连续 3 个 goal turn）
   ├──► usage_limited   （限流/额度，系统控制）
   └──► budget_limited  （token 预算耗尽，系统控制）
```

**状态模型**（SQLite，按 `thread_id` 持久化）：

```text
ThreadGoal {
  thread_id, goal_id, objective,
  status: active | paused | blocked | usage_limited | budget_limited | complete,
  token_budget: Option<i64>, tokens_used: i64, time_used_seconds: i64,
  created_at, updated_at
}
```

**模型可见工具**：

| 工具 | 语义 |
|---|---|
| `get_goal` | 读当前 goal、状态、预算、用量、剩余 token |
| `create_goal` | 只在用户/系统显式要求时创建；有未完成 goal 时失败；可带 token budget |
| `update_goal` | 只能设 `complete` / `blocked` / `paused`；不能设 active/usage_limited/budget_limited |

工具描述里写死了几条硬规则：

- `complete` 只能在目标真正达成且**没有剩余必需工作**时设；不能因为预算快用完就标完成；
- `blocked` 要求同一个阻塞条件**连续至少 3 个 goal turn**，且确实无法在没有用户输入或
  外部状态变化的情况下继续；不能因为「难、慢、不确定」就 blocked；
- `paused` 只能在用户显式要求时设；预算限制优先于暂停；
- goal 工具要求持久化 thread；一个 thread 只能有一个未完成 goal。

**运行时续跑**：

- `continue_if_idle()` → `start_turn_if_idle()`，`turn_trigger = "goal"`；
- 每轮结束记账 token/time；budget 到顶 → `budget_limited`，注入一次
  `budget_limit.md` 收尾提示，明确「不要开始新的实质工作，总结进展/剩余/阻塞」；
- 同一阻塞连续 3 轮 → `blocked`；
- 自动续跑连续 3 次空响应 → `blocked`；
- 执行工具连续 3 轮失败 → `blocked`；
- usage limit → `usage_limited`；
- 目标被编辑时注入 `objective_updated` steering；目标作为 untrusted context 注入
  （`goal_objective`），防止 goal 文本里的内容被当成更高优先级指令；
- resume 时恢复 active goal。

**设计要点**：没有独立裁判（官方文档未描述）；它用「模型自报 + 工具契约 + 运行时状态机」
替代了裁判。强项是预算、阻塞阈值、错误分类和 resume；弱项是完成仍然依赖模型诚实。

### 2.3 Cursor：产品化轻量 goal

官方文档：<https://cursor.com/docs/agent/overview>

- Agent 把每条消息当作一个新 job；`/goal` 给一个 long-lived objective，直到完全完成。
- CLI 中 `Ctrl+C` 暂停 goal。
- 状态是 durable 的：active/paused 跨 idle 和 headless 运行保留。
- 官方没有描述独立裁判、token budget flag 或 `/goal clear` 拼写。
- `/goal` 仍在 rolling out；看不到时建议新开 chat。
- 可与 Custom Mode（playbook）或 `/loop`（定时）组合。

**设计要点**：降低使用门槛，把 goal 当作「长期 objective」而不是严格控制循环；
适合交互式使用，但公开的完成判定细节最少。

### 2.4 Grok Build：评审团 + token 预算

文档：<https://docs.x.ai/build/modes-and-commands>（经第三方对比文章转述）

- `/goal <objective>`，跨多轮工作；每轮结束检查进度，决定继续、暂停等人、或认为完成。
- 看起来完成时，一组 **reviewer subagents** 按证据检查；**多数同意**才算完成。
- 发现缺口就继续；反复卡在同一缺口就暂停并展示缺什么。
- `/goal` / `/goal status` 显示目标、状态、token 用量、耗时；
  `/goal pause` / `/goal resume` / `/goal clear`；启动时可加 `--budget <tokens>`。

**设计要点**：用多裁判降低「自欺」；成本更高，但比单模型裁判更接近 maker-checker。

### 2.5 OpenCode：2.2.1 起原生 `/goal`

Release：<https://github.com/anomalyco/opencode/releases/tag/v2.2.1>（2026-10-29）

- TUI 和 Desktop 支持 `/goal <objective>`；
- 支持 pause、resume、clear；
- 会自动 review progress，直到 objective 达成；
- 官方 release notes 没有展开裁判模型、预算、持久化细节。

社区在原生支持之前已有多个插件（如 `opencode-goal-plugin`、`OpenCode-goal-plugin`），
提供 Codex 风格的持久 goal、独立 completion verifier、fail-closed 持久化等。原生
`/goal` 出现后，这些插件多数转为维护模式或建议直接用内置实现。

### 2.6 可移植实现：`secemp9/goal`

仓库：<https://github.com/secemp9/goal>

这是理解「最小可用 goal」的好参考，明确说是 Codex CLI 的移植：

- 支持 Claude Code、Cursor、OpenCode；
- 状态存项目内 `.claude/goal/goal_state.json`（一个项目一个 goal）；
- 字段：`goal_id`、`objective`（≤4000 字符）、`status`
  （active/paused/budget_limited/complete）、`turn_budget`、`turns_used`、
  `tokens_used`（占位）、`time_used_seconds`、`created_at`、`updated_at`；
- Claude Code 用 hooks 实现续跑：
  - `UserPromptSubmit`：每轮开始注入 goal 提醒；
  - `PostToolUse`：轮内监控预算，接近上限时警告；
  - `Stop`：轮末记账、递增 turn、未完成且未超预算就强制下一轮；
    刚到预算上限时给一次 wrap-up，然后停止；
- MCP 工具：`get_goal` / `create_goal` / `update_goal`；
- OpenCode V2 用原生 plugin API 实现 `/goal`、上下文注入、轮次/时间记账和续跑；
- goal 绑定 root session，child session 不驱动续跑；用户中断或执行失败会暂停，
  需要显式 `/goal resume`。

**设计要点**：证明了 goal 可以做成一个薄层：状态文件 + turn-end hook + 三个工具，
不需要改内核。缺点是没有独立裁判，完成仍靠模型自报（它加了 completion audit 提示）。

### 2.7 相邻路线：loop engineering / spec-driven

论文：*Stop Hand-Holding Your Coding Agent: Engineering the Loops that Replace
Step-by-Step Prompting*（arXiv:2607.00038，2026-06）

它把这类系统叫 **loop specification**，五个部分：

```text
trigger + goal + verification + stopping rule + memory
```

并给出一个五级验证阶梯：

| 级别 | 验证方式 | 性质 |
|---|---|---|
| 1 | 确定性检查（assert、exit code、golden output） | 自主区 |
| 2 | 规则/约束（lint、schema、policy） | 自主区 |
| 3 | 延迟的现场真相（测试、部署、真实用户反馈） | 客观区 |
| 4 | 模型按 rubric 打分 | 辅助判断 |
| 5 | 人类 checkpoint | 监督 |

论文对 50 个公开 loop 的统计：70% 在 level 1/2 自主验证，74% 命名了终止状态；
但只有 22% 使用自动触发、20% 调用命名 skill、32% 有持久记忆——「知道怎么算完」
比「无人值守地跑起来」成熟得多。

它列出的反模式与 goal 直接相关：

- **while-true around a stranger**：没有命名 skill、没有真检查的无限重试；
- **self-approving loop**：生成者和裁判是同一个模型，分数上涨质量不涨（reward hacking）；
- **specification gaming**：改测试、硬编码期望值、绕过检查；
- **pretending level 4 is level 1**：把模型意见当成确定性检查；
- **unattended runaway**：没有任务相关停止规则、没有卡死检测、没有预算上限。

---

## 3. 对照表

| | Claude Code | Codex | Cursor | Grok Build |
|---|---|---|---|---|
| **设定** | `/goal <condition>`，≤4000 字符 | `/goal <objective>`，≤4000 字符；首轮即 prompt | `/goal <objective>` | `/goal <objective>` |
| **判定** | 独立 small fast model 读 transcript，三态 | 模型 `update_goal` 自报 + 运行时硬约束 | 未公开独立裁判 | reviewer subagents 多数同意 |
| **续跑** | 每轮结束的 prompt Stop hook | 线程 idle 时 `start_turn_if_idle`，`turn_trigger=goal` | 每轮结束自动继续 | 每轮结束检查进度 |
| **停止** | met / impossible / 不可恢复错误 / clear / 空转 | complete / blocked / paused / usage_limited / budget_limited | Ctrl+C 暂停 | pause / clear；连续卡住暂停 |
| **预算** | 状态显示 token，无公开预算 flag | token budget，到顶一次 wrap-up | 未公开 | `--budget <tokens>` |
| **状态** | session 级；resume 恢复条件、重置计数 | SQLite `ThreadGoal`，按 thread 持久化 | durable，跨 idle/headless | 会话内 |
| **权限** | goal 不改 permission mode；auto mode 独立 | 沙箱/审批不变；需要决策时暂停 | 未展开 | 未展开 |

**OpenCode**：2.2.1 起内置 `/goal`，pause/resume/clear + 自动 progress review；公开细节少于上表四家。

---

## 4. 设计维度与取舍

### 4.1 目标表示

| 方案 | 例子 | 优点 | 缺点 |
|---|---|---|---|
| 自然语言 condition | `tests in test/auth pass` | 低门槛、灵活 | 容易写成不可失败的「clean up」 |
| objective + 显式检查 | `... verified by npm test exit 0` | 可判定、可复用 | 需要用户会写检查 |
| 结构化 spec 文件 | `GOAL.md` + acceptance criteria | 适合长任务、可版本化 | 重，需要额外工具/流程 |

无论哪种，4,000 字符上限都意味着：**长 spec 放文件，goal 指向文件**。Codex 的
command reference 明确这么建议；Claude Code 也把条件限制在 4,000 字符。

目标写法的最小公式：

```text
<可测量的终态> + <明确的检查方式> + <不能越过的边界>
```

弱：`Migrate the auth module`
强：`Migrate auth to the new API, verified by the auth test suite passing,
without changing public endpoints`

### 4.2 完成判定

按可靠性排序：**确定性检查 > 独立模型裁判 > 评审团 > 执行者自报**。

- 确定性检查（exit code、测试、断言）是唯一能真正「失败」的检查；
- 独立模型裁判要解决「只看 transcript」的盲区：要求 agent 每轮把证据写进对话；
- 评审团降低单模型自欺，但成本高；
- 执行者自报最便宜，也最容易被 reward hacking / specification gaming 击穿。

无论选哪种，**maker 不能同时是 checker**。如果必须用模型裁判，至少换模型/换上下文，
并给固定 rubric；如果 agent 能修改检查本身（测试、lint 配置），检查就失效了。

### 4.3 状态机

至少需要这些状态：

```text
active
paused          # 用户显式暂停
blocked         # 同一阻塞连续 N 轮，需要用户/外部变化
usage_limited   # 限流/额度，系统控制
budget_limited  # token/turn/time 预算耗尽，系统控制
complete        # 目标达成
```

关键约定：

- 错误 ≠ 成功；预算耗尽 ≠ 完成；
- 预算到顶只给**一个收尾轮**，不再开新活；
- `blocked` 有阈值，避免模型用「我卡住了」逃避；
- `paused` 只能用户触发，模型不能自己暂停；
- resume 后重新计数，避免旧状态污染新 run；
- 一个 session/thread 同时只有一个 active goal。

### 4.4 续跑触发

| 触发 | 代表 | 适用 |
|---|---|---|
| turn-end hook | Claude Code Stop hook | 交互式 session |
| idle continuation | Codex `start_turn_if_idle` | 有持久 thread 的 runtime |
| 定时 | `/loop`、scheduler | 周期性任务 |
| 事件 | CI、issue、webhook | 无人值守流水线 |

必须处理的边界：

- 后台任务还在跑 → 推迟判定；
- 连续空响应 / 执行失败 → N 次后 `blocked`；
- 失败或用户中断 → 不自动重启，等显式 resume；
- 子会话不驱动续跑：goal 绑定 root session；
- goal 不扩大权限：auto mode 与 goal 是两件事。

### 4.5 持久化与上下文

| 方案 | 代表 | 特点 |
|---|---|---|
| session 内存/transcript | Claude Code | 简单；resume 需恢复条件 |
| SQLite | Codex | 强一致、按 thread 隔离、可记账 |
| 项目 JSON | `secemp9/goal` | 可移植、人类可读；并发弱 |
| session side-channel record | Zen Agent 现有 `Session.records` | 与 JSONL 同生命周期、可回放 |

长期任务还要防上下文腐烂：每轮重新注入 goal；目标被编辑时注入 steering；
compaction 后从磁盘恢复；极端做法是每轮全新上下文 + 磁盘状态（Ralph loop）。

### 4.6 预算、权限与安全

- **预算**：token（Codex/Grok）、turn（port）、time；至少有一个硬上限。
- **可观测**：`/goal` 状态要显示条件、运行时长、轮数、token、最近判定理由。
- **健康指标**：cost per accepted change（花费 ÷ 通过验证的改动数），比裸 token 花费有意义。
- **安全**：goal 文本是用户输入，按 untrusted data 注入；不可逆操作仍需人类批准；
  两个 goal 写同一个 checkout 会互相踩，需要 worktree 隔离。

---

## 5. 反模式

| 反模式 | 表现 | 解法 |
|---|---|---|
| 自评自批 | 分数上涨、质量不涨 | maker-checker 分离；换模型/上下文 |
| 规范博弈 | 改测试、硬编码期望值 | hold-out；证明检查能抓住 bug |
| 弱目标 | 永远「还差一点」 | 目标必须能失败，写清检查命令 |
| 裁判盲区 | 裁判只看 transcript，agent 没跑测试 | 要求每轮把证据写进对话 |
| 失控循环 | 没预算、没卡死检测 | token/time/turn 预算 + no-progress |
| 目标漂移 | 跑着跑着做别的事 | 每轮重注 goal；状态落盘；定期对齐 |
| 理解债 | 代码合了没人看懂 | 人类在不可逆步骤 checkpoint |

---

## 6. 对 Zen Agent 的启示

Zen Agent 的约束与这些产品不同：内核小（五层、纯 Python、一个 `httpx` 依赖）、
session 是 append-only JSONL、app 层已有 `Runtime` / `ChatController` / `CommandRegistry` /
`RewindState` 这类 session 级状态先例。因此 goal 更适合：

1. **放 app 层，不进 agent loop 内核**：`GoalState` + turn-end coordinator，
   通过 `agent.prompt()` 注入续跑消息，保持 kernel 不认识 goal。
2. **状态用 session side-channel record 或 sidecar**：跟 `RewindState` 一样按 session
   绑定/清除，避免引入 SQLite；resume 时从 `Session.records` 恢复。
3. **判定优先确定性检查**：`goal.check` 跑测试/lint/exit code；没有检查命令时再用
   独立 cheap model 读 transcript，且保存 last verdict/reason。
4. **续跑挂在 `ChatController` 的 turn 结束点**：复用 `wait_for_idle`，处理后台 subagent /
   job 未完成、用户中断、失败不重启、预算到顶 wrap-up。
5. **状态机 + 预算 + 权限边界**：active/paused/blocked/budget_limited/complete；
   goal 不改 permission mode；root session 拥有 goal，child session 不驱动续跑。
6. **先做交互式，再做无人值守**：TUI/REPL 的 `/goal` 是最小闭环；headless/RPC/desktop
   复用同一状态机，但预算、通知、恢复要单独设计。

具体实现方案与任务拆分见后续 design/plan 文档（按 superpowers 流程先设计、后计划）。

---

## 7. 来源

- Claude Code `/goal` 官方文档：<https://code.claude.com/docs/en/goal>
- Claude Code hooks / prompt-based Stop hook：<https://code.claude.com/docs/en/hooks>
- Codex goal 源码：<https://github.com/openai/codex/tree/main/codex-rs/ext/goal>
- Codex `ThreadGoal` 状态模型：<https://github.com/openai/codex/blob/main/codex-rs/state/src/model/thread_goal.rs>
- Codex goal 工具定义：<https://github.com/openai/codex/blob/main/codex-rs/ext/goal/src/spec.rs>
- Codex goal runtime：<https://github.com/openai/codex/blob/main/codex-rs/ext/goal/src/runtime.rs>
- Codex goal accounting：<https://github.com/openai/codex/blob/main/codex-rs/ext/goal/src/accounting.rs>
- Cursor Agent 文档：<https://cursor.com/docs/agent/overview>
- Grok Build modes and commands：<https://docs.x.ai/build/modes-and-commands>
- OpenCode 2.2.1 release：<https://github.com/anomalyco/opencode/releases/tag/v2.2.1>
- `secemp9/goal`（Codex goal 的可移植实现）：<https://github.com/secemp9/goal>
- Flavio Copes 四家对比：<https://flaviocopes.com/ai-glossary/goal-mode>
- *Stop Hand-Holding Your Coding Agent*（arXiv:2607.00038）：<https://arxiv.org/abs/2607.00038>
