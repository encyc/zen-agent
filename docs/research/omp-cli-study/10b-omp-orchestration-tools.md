# 10b · omp 编排与自主性工具（task / todo / wait / ask / eval / memory / plan）

> **对象**：omp 18.14.0 的**本地安装包源码**（npm 包，不是仓库 main 分支）：
> `omp = ~/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src`
> `core = ~/.bun/install/global/node_modules/@oh-my-pi/pi-agent-core/src`
> `tui = ~/.bun/install/global/node_modules/@oh-my-pi/pi-tui/src`
> **基线**：ZenAgent 0.3.0（HEAD `2aab733`）。9 个工具全顶层（`zenagent/tools/__init__.py:27-37`）；内核 `AgentTool` 只有
> `name / description / parameters / execution_mode / execute`（`zenagent/agent/loop.py:82-105`）；权限门按**工具名表**分类（`zenagent/app/permissions.py:32`）。
> **证据约定**：`路径:行` 均为源码实读；**【推断】**= 我基于源码的判断，不是 omp 明文；不贴大段实现代码。
> **分工**：同批另有 10a（核心工具族）、10c（世界类工具）、10d（ZenAgent 工具基线）、10e（工具控制面）。09 篇已从 TUI 角度给出"todo/subagent 是 P1"的总账，本篇不重复，只补**编排类工具的语义 / 成本 / 最小实现边界**，并回答 todo–task–wait 的先后。审批控制面（cache、restricted folders、approval mode 矩阵）归 **10e**，本篇只写"工具怎么声明审批"。
> **口径**：omp 内置工具 = **30 builtin + 3 hidden**（`omp/tools/builtin-names.ts:1-38`；hidden = `yield`/`goal`/`think`）。ZenAgent = 9（含 web_search/fetch 两个 provider 依赖的）。

## 0. 结论速览（含 todo-vs-task-vs-wait 的答案）

1. **omp 的"工具多"不等于"schema 多"**：13 个 `essential` 顶层 + 5 个被钉在顶层的 `discoverable`（todo/ask/grep/web_search/yield）= 模型实际直接可见约 18 个；其余（ast_*/lsp/github/memory_*/checkpoint/rewind…）挂到 `xd://` 设备，用 `read xd://` 查文档、`write xd://<tool>` 执行（`omp/tools/essential-tools.ts:24-38`、`omp/tools/xdev.ts:58-66,84-87`、`prompts/system/system-prompt.md:85-87`）。**真正该抄的是"编排三件套"，不是工具数量。**
2. **omp 的编排有三条腿**：`task`（一次性 spawn，blocking/async 二态）、`eval` 内核（`agent()` / `workpool()` / `wait(handles)` 句柄式编排）、`wait`（阻塞在自己启动的后台作业/服务/本轮自己发出的提问回复上）。三者共用一套 async job 投递，作业类型只有 `"bash" | "task" | "eval"`（`omp/async/job-manager.ts:49`）。
3. **`task` 的成本不在 tool 代码，在契约面**：agent 定义发现与 spawn policy、结构化结果（`yield`）、worktree 隔离、并发/递归上限、成本归属、完成度探针（**额外 LLM 调用，默认开**，`omp/task/completion-probe.ts:55-56,232`；`omp/task/settings.ts:383-386`）。`executor.ts` 5298 行不是夸张，是这堆契约。
4. **`todo` 的模型面很小，价值在提醒环**：9 个 op、按**内容**寻址；但 omp 真正让它生效的是 tracker——首轮 eager 预置（可强制 `tool_choice="todo"`）、跑中 nudge（12 次变更阈值、每周期≤2 次）、**停止时未完成提醒**（最多 3 次，且跳过"在等用户/在等后台"）（`omp/session/todo-tracker.ts:21-22,163-210,233-320`）。只抄工具不抄提醒，收益约一半【推断】。
5. **`wait` 的 schema 是空对象**（`omp/tools/wait.ts:23`）：它不是"等任意东西"，只等**自己拥有的**作业/服务/本轮未答的 agent；`useless` 标记（同文件 `:146-160,301`）把它和"轮询"划开——空等结果可被压缩丢弃（`core/compaction/utils.ts:246-269`），loop guard 也不把它算成进展（`core/types.ts:1036`）。
6. **`eval` 与 bash 的本质差别是"持久内核 + 句柄"**：状态跨调用/跨压缩（`prompts/tools/eval.md:1`）、`agent()` 返回 handle、`await tool.<name>(args)` 工具桥（`prompts/tools/eval-agents.md:2-9`）。它不是更快的 bash，是**编排 DSL**；ZenAgent 复刻等价物要子进程内核+工具桥+权限/取消穿透，v0.4 不可行。
7. **v0.4 顺序：TODO 先（P1），TASK 次（P1 条件项，实际建议 v0.4.x/v0.5），WAIT 不做（P2，且前置是后台作业）**。todo 是唯一"不引入新运行时子系统却直接改变长任务行为"的工具；task 要同时解决子代理权限、成本归属、持久化、取消四个切面（3–5 倍实现面）；wait 依赖 ZenAgent 根本没有的 async job 世界。
8. **不要抄**：worktree 隔离、IRC 对等消息、workpool/seance/投机启动、completion probe、goal 模式机、memory backend 抽象、managed skill 自改写、`xd://` 设备发现。理由与"最小替身"见 §12。

## 1. omp 的工具面：模型实际看到什么

| 层 | 数量 | 例子 | 机制 |
|---|---|---|---|
| essential（顶层） | 13 | read/write/bash/edit/glob/find/eval/task/wait/learn/manage_skill/context_notes/new_context | 类上 `loadMode="essential"`（`omp/tools/essential-tools.ts:24-38`） |
| 固定顶层 discoverable | 5 | todo/ask/grep/web_search/yield | `XDEV_KEEP_TOP_LEVEL`：todo 供 prelude 机、ask 是交互面、grep 是 bash 拦截器的重定向目标、web_search 多数模型不会走 xd 协议、yield 终止结构化子代理（`omp/tools/xdev.ts:52-66`） |
| discoverable→xd:// | 其余 | ast_grep/lsp/github/memory_*/checkpoint… | `read xd://` 列设备+schema，`write xd://<tool>` 执行；`tools.xdev` 默认 true（`omp/tools/settings.ts:1018-1027`） |
| hidden | 3 | yield/goal/think | 不默认注册，按模式/场景启用（`omp/tools/builtin-names.ts:36`） |

**对 ZenAgent 的含义**：9 个工具的 schema 成本完全可接受，**不需要**工具发现机制；要抄的是 omp 系统提示里的"工具政策"措辞（`prompts/system/system-prompt.md:96-136`：Specialized Tools / General / Exploration 三段式），而 ZenAgent 现在只有一句 "Prefer using tools"（`zenagent/app/support.py:47-49`）。这条是 P1 里最便宜的（半天）【推断】。

## 2. `task` — 子代理

### 2.1 模型面与参数

- 单发：`agent?`（默认 `task`）、`name?`（CamelCase ≤32，可寻址）、`task` 或 `taskFile`（互斥，`local://`/绝对路径，≤4MB，`omp/task/types.ts:34`）、`solutionSpace`（**必填**：描述问题开放度，不写兄弟 agent/协作）、`outputSchema?`/`schemaMode?`、`tools?`、`effort?`（lo/med/hi）、`isolated?`（`omp/task/types.ts:75-97`）。
- 批量：`context` + `tasks[]`；批量时 `agent` 只能在 item 里，顶层传会被显式拒绝（`types.ts:120-130`）。
- 关键 schema 都是**动态生成**的：batch/structured/isolation/effort/seance 各自开关决定 wire schema（`types.ts:259-300`）——"工具参数随设置变"是 omp 的通用手法，ZenAgent 目前 parameters 是静态 dict。

### 2.2 执行语义（决定成本的地方）

- `async.enabled` 默认 true（`omp/tools/settings.ts:963-967`）：**非 blocking** agent 的 spawn 立刻变成后台 job 并返回 job/agent id；`blocking: true` 的 agent 才内联返回（`prompts/tools/task.md:1`、`omp/task/index.ts:1315-1337`）。
- 并发：每 session 一个 semaphore，`task.maxConcurrency` 默认 32，且 **resize 就地生效**（`omp/task/index.ts:771-774,1003-1013`；`omp/task/settings.ts:307-311`）；批量内部用 worker-pool 保序（`omp/task/parallel.ts:26-82`）。
- 递归：`task.maxRecursionDepth` 默认 2，`taskDepth` 由父会话持有 `task` 工具决定（`omp/task/types.ts:304-307`、`omp/task/settings.ts:343-347`）。
- 隔离：`task.isolation.enabled` 默认 **false**；开启后子代理在 `~/.omp/wt/` 下的 worktree/overlay 里跑，带 owner marker 防止清错（`omp/task/worktree.ts:11-13`、`omp/task/isolation-ownership.ts:16,69-73`），`apply` 默认 true、`merge` 默认 `patch`（`omp/task/settings.ts:147-166`）。
- 结果契约：`<task-result id agent status duration>` + `abort-reason`/`error`/`<preview full-output="agent://id">`/`<merge-summary>`（`prompts/tools/task-summary.md:1-23`）；内联阈值 `ASYNC_INLINE_RESULT_MAX_CHARS-500`，超阈值给 5000 字符预览 + `agent://` 指针（`omp/task/result-summary.ts:24-26,52-96`）。**父只付 envelope，不付子 transcript。**
- 结构化结果：`task.structured` 默认 **false**（`omp/task/settings.ts:268-271`）；开启后由 hidden 工具 `yield` 提交 `{data}|{error}`，schema 重试 3 次后覆盖放行（`omp/tools/yield.ts:29,350,360`、`prompts/tools/yield.md:1-11`）；增量 section 的 schema 形状在 `omp/task/yield-assembly.ts:55-68` 推导。

### 2.3 成本归属

- 子代理 token 记在 `SingleResult.usage/tokens/requests`（`tui/tools/task.ts:2487-2545`），父会话把它汇总到 **`subagentCost`**，与自身花销分开（`omp/session/session-manager.ts:599`、`omp/session/session-entries.ts:411`）。
- 完成度探针：默认 true，每次另发小请求估"完成百分比"，**cost 累加进 progress.cost**（`omp/task/completion-probe.ts:55-56,232`、`omp/task/executor.ts:2353-2362`）。这是 omp 愿意为"可观测性"多付的 LLM 钱，ZenAgent 不该抄。
- 委派倾向是可调的：模型级 delegation bias 决定描述里是否推子代理（`omp/task/prompt-policy.ts:8-11`），另有 `task.eager` 首轮 prelude（`omp/session/todo-tracker.ts:203-221`）。“agent 是否只读”由其声明工具集是否为只读档子集推出（`omp/task/read-only-policy.ts:7-27`）——与 ZenAgent 的 `READ_ONLY_TOOLS` 同构。
- 认知成本由提示词兜底：`task.md` 明说"用最具体的 agent / 一个集成 owner / **中途禁止 build/lint/test，结束后跑一次** / 大 payload 走 `local://`"（`prompts/tools/task.md:12-15,28`）；系统提示还有一条硬规则："用户说 parallel ⇒ 必须用 task 子代理，并行工具调用不算"（`prompts/system/system-prompt.md:98`）。

### 2.4 `task/` 目录地图（35 文件，读源码时按这张表跳）

| 分组 | 文件 | 职责 |
|---|---|---|
| 执行 | `index.ts`(1980) `executor.ts`(5298) `parallel.ts` `spawn-run.ts` | 工具/派发/子代理进程与并发 |
| 结果契约 | `structured-subagent.ts` `yield-assembly.ts` `result-summary.ts` `output-manager.ts` | schema 解析、增量 section、`<task-result>` envelope、`agent://` 输出文件 |
| 隔离 | `worktree.ts` `isolation-runner.ts` `isolation-ownership.ts` `restore.ts` `persisted-revive.ts` | worktree 生命周期、owner marker、恢复 |
| 策略 | `spawn-policy.ts` `read-only-policy.ts` `types.ts` `settings.ts` `prompt-policy.ts` | agent 定义、spawn 白名单、只读判定、设置 |
| agent 目录 | `discovery.ts` `agents.ts` `name-generator.ts` | `.omp/agents/*.md` + 内置 6 agent；命名用词典（无 LLM） |
| 附加能力 | `seance*.ts` `prewalk.ts` `completion-probe.ts` `workpool.ts` `speculative-launch.ts` `eval-tools.ts` `error-attribution.ts` `widget-inputs.ts` `provider-concurrency.ts` | 会话复制、预走、完成度探针、批处理池、流式抢跑、eval 工具注入、job widget |

> 读法：`index.ts` 是工具面与控制流，`executor.ts` 是子代理进程内部；其余按上表分组看。ZenAgent 若只做同步单发，实际只需要 `index` + `result-summary` 两组的语义。

### 2.5 ZenAgent 现状 / 差距 / 优先级

现状：无子代理、无后台作业；`Agent` 同一实例只允许一个 run（`zenagent/agent/agent.py:245`），但**新建一个 child `Agent` 是可复用的**（`agent.py:65-110`）；无 agent 定义发现；无结构化结果；无隔离；TUI 无子代理卡。
差距（按实现顺序）：child 运行时（模型/工具集/独立 ContextGuard）→ 权限穿透（plan/ask 模式下 child 的写操作要不要再弹窗？）→ 取消传播（`AbortToken` 已有，`zenagent/events.py`）→ 成本归属（`RunStatus` 需加 `subagent_cost`，`zenagent/app/views.py:65-83`）→ 结果 envelope。
优先级：**P1 条件项**（若 v0.4 只做一件事则不做；做 todo 之后的下一个，或 v0.5）。成本估算（不含隔离/agent 定义/异步）：约 250–400 行 + 一个测试文件【推断】。

## 3. `todo` — 分阶段清单

### 3.1 模型面

- 单一工具、`op` 派发：`init | start | done | rm | drop | block | unblock | append | view`（`omp/tools/todo.ts:53`）；参数 `op/list?/task?/phase?/items?/reason?`（`:60-70`）。
- **按内容寻址，不用 ID**；模型传 `task-1` 会得到专门文案（`todo.ts:271-274`），因此 init 时重复相位/重复任务被直接拒绝（`:324-339`）。in-progress 指针自动前进：无 in_progress 时最早 pending 变 in_progress，多个 in_progress 只留最早（`:128-143`）。
- 批量原子性：同一次调用里任一 op 出错，**整批丢弃**且返回错误（`:755-760`），避免"半成功 + 重试撞 already exists"。
- 状态机细节：`block` 只能作用于 pending/in_progress/blocked（不重开已完成），reason 折成单行（`:429-450`）；`unblock` 回 pending；`done/rm` 可指向 task 或整个 phase（`:381-398,417-422`）。
- 结果文本是**给人/模型看的汇总**（剩余项、`closed/total`、当前 phase、worked-ahead 解释，`:635-707`）；`details.phases` 给 UI，`completedTasks` 给 HUD 动画（`:762-763`）。
- 持久化二态：有 session 文件 → 从**分支上的 toolResult details** 回溯 canonical 快照；无 session → 仅内存（`:185-217,739`）。`/todo edit` 用 Markdown checklist 往返（`- [x]`/`[!] <!-- blocker: … -->`，`:551-633`）。

### 3.2 让它真正生效的提醒环（omp 的关键设计）

| 机制 | 触发 | 数值/约束 | 证据 |
|---|---|---|---|
| eager prelude | 首轮、非 plan、无 todo、非问答句 | `todo.eager` 默认 `default`；`preferred` 只提醒，`always` 直接**强制 tool_choice=todo** | `omp/session/todo-tracker.ts:163-210`、`omp/tools/settings.ts:419-427`、`prompts/system/eager-todo.md:1-17` |
| 跑中 nudge | 连续 mutating 工具调用 | 阈值 12 次，每周期≤2 次 | `todo-tracker.ts:21-22`、`omp/session/agent-session.ts:2374-2376` |
| 停止提醒 | 模型停止但仍有未完成项 | 最多 3 次；"在等用户回答"或"有后台作业会唤醒"时**跳过** | `todo-tracker.ts:233-320`、`omp/tools/settings.ts:389-409` |
| HUD | UI 层 | 可见性用 snapshot fingerprint 绑定，不串分支 | `todo.ts:172-250` |

### 3.3 ZenAgent 现状 / 差距 / 优先级

现状：无 todo；**`details` 不落盘**（`ToolResultMessage.to_json` 只写 content，`zenagent/types.py:179-194`）——这是移植时要先解决的硬约束；没有"停止时"钩子，只有工具轮之间的 steering（`zenagent/agent/loop.py:120-122`、`agent.py:153`）；TUI 有 dock 可放 HUD（`zenagent/cli/tui/app.py:160-175`）。
差距：工具（~250 行）+ 状态存放（sidecar，见 §12.4）+ 提示词（~120 词）+ HUD（~80 行的 Static）+ 测试。
优先级：**P1（本批最高）**。

## 4. `wait` — 阻塞等待

- schema `{}`（`omp/tools/wait.ts:23`）；`loadMode="essential"`、`intent="optional"`、`interruptible` + `handlesToolInterrupt`（`:96-107`）——**这四个声明就是它的全部成本模型**：等待可被 steering 打断、被打断时仍要"排空已到事件"。
- 只等三类：自己 owner 的 job、自己启的 service、本轮自己发出且仍可能回复的 agent；别人的 agent 不构成等待理由（`:88-95,57-69`）。
- 唤醒 race：job promise / IRC bus / service / "对方已不可能回复" / 30 分钟上限 / abort（`:168-246`，`WAIT_MAX_MS=30min` 在 `:24`）。
- 两个语义值得抄：**`useless`**（空等/超时结果标记为"上下文无用"，压缩可丢，`omp/pi-agent-core` 的 `core/types.ts:1036`）；**undelivered 优先**（已结算但没送达的结果先返回，`:138-145`、`omp/async/job-control.ts:50-64`）。
- 进度：500ms 一次 snapshot 推给 UI（`:25,230-236`）。

ZenAgent 现状：**零**后台能力（bash 同步、Signal 杀进程组，`zenagent/tools/bash.py:107,151-176`），无 job 表、无跨轮投递、无 agent 消息。结论：**现在做 wait 等于做一个没有对象的函数**。优先级 **P2**，前置是 background bash（`async` 参数 + job 表 + 完成注入）。若做，最小面：一个工具、等自己启动的作业、无 30 分钟上限（用 bash timeout）、无 service/IRC。

## 5. `ask` — 结构化提问

- schema：`questions[]{id,question,header?,options[{label,description?,preview?}],multi?,recommended?}`，至少 1 题（`omp/tools/ask.ts:70-90`）；保留标签 `Other (type your own)`/`Chat about this`/`Next →` 模型不能占用（`:64-72`）。
- 语义：`recommended` 自动加 "(Recommended)"；UI 超时可自动选 recommended，**plan mode 下禁用超时**（`:755-758`）；`concurrency="exclusive"` 因为交互面是单例（`:579-582`）；无 UI 时 abort 或返回专用文案（`:637-645`、`prompts/tools/ask-headless-fork.md`）；返回 `User answers: …`（`:1008`），回答可带图片。
- 提示词约束很硬："**只有真正必须用户决定的取舍才问**；先给出未见的决策上下文再问；一次可批 2–5 个选项"（`prompts/tools/ask.md:1-8`）。

ZenAgent 现状：只有**审批**弹窗（`PermissionBroker` 在 `zenagent/app/permissions.py:218-255`，`ToolApproval` 在 `zenagent/cli/tui/screens/modals.py:183`），模型不能主动提问；plan 模式只靠一句 prompt（`zenagent/app/modes.py:66-75`）。差距：新 modal + 一个 ~120 行的工具，零额外 LLM 成本。优先级 **P2**；若 v0.4 要把 plan 模式做成工作流（写 plan 文件→批准），ask 升 **P1**。

## 6. `eval` — 代码化编排底座（与 bash 的分界）

| 维度 | bash | eval |
|---|---|---|
| 状态 | 每次新 shell | **持久内核**，跨调用、跨压缩（`prompts/tools/eval.md:1`） |
| 结构化 | 文本 | `agent()` 返回 handle（`.id/.status/.wait()/.send()/.cancel()/.output()`），`workpool()` 保活批处理（`prompts/tools/eval-agents.md:2-9`） |
| 编排 | 自己写脚本 | `wait(handles)` = 波次屏障；`await tool.<name>(args)` = **工具桥**（`eval.md:16-18,27`） |
| 成本 | 进程 | 进程 + 内核 + 可能的 `judge()/judgeBatch()` 模型调用（`eval.md:48`） |
| 超时 | `timeout` 1–3600s | cell deadline 默认 30s，0 关闭；**不包含**等子代理的时间（`omp/tools/tool-timeouts.ts:12`、`eval-agents.md:12`） |

配套件两个：`eval-backends.ts:11-27` 决定 py/js 是否可用（探针失败就从不广告），`run-code.ts:30-68` 把函数/正则渲染成可执行实参。这些都是内核级投入，ZenAgent 不涉及。

判断：这是 omp 最有想象力、也**最不该抄**的一块。ZenAgent 定位 httpx-only Python 内核，复刻要常驻子进程 + 语言内核 + 工具桥 + 权限/取消穿透 + 包安装，收益（"模型写代码调工具"）远小于成本。优先级 **N**；可偷的两个概念是"句柄化"（返回 id 而非字符串）和"工具桥"（未来若做 task，预留 agent handle 概念）【推断】。

## 7. 记忆 / 技能自改写：`retain`/`recall`/`reflect`/`memory_edit`/`learn`/`manage_skill`

- 全部依赖外部 memory backend（hindsight/mnemopi）或 local 文件后端，否则 `createIf` 返回 null，工具根本不存在（`omp/tools/memory-recall.ts:13-16`、`memory-edit.ts` 只支持 mnemopi、`learn.ts:71-77`）。
- 默认 `loadMode="discoverable"`（不上顶层）；**只有 `learn`/`manage_skill` 是 essential**；写全局 scope 或写 skill 时审批从 `read` 动态升到 `write`（`memory-retain.ts:40-44`、`learn.ts:49-57`）。
- `learn` = 一次调用两条腿：写一条 lesson + 可选铸造 managed skill（`learn.ts:15-36,79-180`）；`manage_skill` = create/update/delete，`write` 档且 authored skill 同名时拒绝（`manage-skill.ts:15-32,41-48`）。
- ZenAgent：skills 只有**只读** `read_skill`（`zenagent/skills.py:104-140`），无 memory。差距：可以做"写 skill"，但那等于开放自修改+审批+冲突规则三件事。优先级 **N**（v0.4 明确不做）。

## 8. 其余 hidden / 自主权工具（一句话结论）

| 工具 | 是什么 | ZenAgent |
|---|---|---|
| `yield` | 子代理结构化结果提交；`{data}`/`{error}`，可增量 section；hidden、read 档（`omp/tools/yield.ts:29,350,360,362`） | 只有做 task 才需要 → 随 task |
| `goal` | goal mode 状态机（create/get/resume/complete/drop），hidden、动态审批（`omp/goals/tools/goal-tool.ts:61-71`、`prompts/tools/goal.md:1-10`） | 无 goal 循环 → N |
| `think` | 把"思考"变成可渲染的 tool call；返回 `------`；hidden、intent omit（`omp/tools/think.ts:39-71`） | N（原生 reasoning 已够） |
| `context_notes` | 实验性持久笔记本，≤16KiB，omit=读、`""`=清空（`omp/tools/context-notes.ts:66-72`） | 无实验性上下文管理 → N |
| `new_context` | 只是"请求新窗口"的信号，本身不压缩（`context-notes.ts:150-162`） | N |
| `run-scope.ts` | 不是工具：eval 运行期 browser promise 归属（`AsyncLocalStorage`），防托管 Promise 逃逸 | N（无 eval 内核） |
| `context.ts` | 不是工具：`AgentToolContext` 的宿主装配（ui/hasUI/toolCall） | N（ZenAgent 已有 execute 形参） |
| `plan-mode-guard` | 不是工具，是 write/edit 的写路径守卫：plan 模式下仅允许 `local://` 沙箱（`omp/tools/plan-mode-guard.ts:80-103,129-149`） | ZenAgent plan 模式更粗（按工具名，`permissions.py:143-160`）→ 先维持 |

## 9. 工具面契约：审批与超时（与 10e 的分界）

**omp：工具自己声明审批**（`core/types.ts:1053-1089`）：

- 三档 `ToolTier = read|write|exec`；对象形式可带 `reason`/`override`（强制弹窗）/`policy: allow|deny|prompt`/`policyKey`（把策略键指向被代理的工具）；**函数形式**按参数动态返回（例：`learn` 写全局才升 write、`goal` 的 `get` 是 read）。
- 另三个可选面：`formatApprovalDetails(args)`（提示正文）、`approvalRestrictionPaths(args)`（受限目录）、`approvalCacheTargets(args)`（"记住授权"的键）（`omp/tools/approval.ts:60-66,143,302-303,524-545,718,730`）。
- 没声明审批的 MCP 工具按 `exec` 兜底（`approval.ts:718`）。**持有审批语义的是工具，不是 dispatcher**——这是与 ZenAgent 最大的方向差异。

**ZenAgent**：权限门按名字表硬编码（`READ_ONLY_TOOLS`，`permissions.py:32`），plan 模式也是"名字不在只读集就拒"（`:158-163`）。加 2 个工具要改 2 处（工具表 + 只读集）。**建议**：不引入 omp 的对象/函数审批 DSL（那是为 30 个工具+插件生态设计的），但把分类集中到一个 `TOOL_TIERS: dict[str, str]`，两个新工具天然声明 read/exec【推断】。

**超时**：omp 只有 9 个工具有 per-tool 超时表（bash/eval/browser/computer/ssh/fetch/lsp/debug/ida，`omp/tools/tool-timeouts.ts:10-20`），且 `task/todo/wait/ask` 都**不在表内**——它们要么自带上限（wait 30min；task 有 `maxRuntime`（默认 0=不限）与 idle TTL 420s，`omp/task/settings.ts:363-366,396-399`），要么由 UI 超时（ask）。ZenAgent 只有 bash 有 `timeout_s`（`zenagent/tools/bash.py:107`）；若加 wait/task，必须自带 deadline，不能靠调用方【推断】。

## 10. 成本总表（谁付钱）

| 工具 | 额外 LLM 调用 | 子进程 | 持久化 | UI 面 |
|---|---|---|---|---|
| `task` | 子代理自身 turn；completion probe 默认 1 次/间隔 | 是（子代理会话） | 结果文件 + session + `agent://` | 子代理卡/HUD |
| `todo` | 否（eager 强制 tool_choice 会抢一轮） | 否 | session details 或 memory | HUD/清单 |
| `wait` | 否 | 否 | 作业表（内存，消费后 30s 清） | 作业行/进度 |
| `ask` | 否 | 否 | 无 | 模态对话框 |
| `eval` | 仅 `judge*` 助手 | 是（py/js 内核） | 内核状态（进程内） | 单元格输出 |
| `learn`/`manage_skill` | 否 | 否 | skill 文件 / learned.md | 工具卡 |
| `retain/recall/reflect` | 可能（backend 实现）【推断】 | 否 | 外部 backend | 工具卡 |

## 11. ZenAgent vs omp 差距表（本批范围）

| 能力 | ZenAgent | omp | 差距性质 | 优先级 |
|---|---|---|---|---|
| 进度清单 | ❌ | todo + 提醒环 + HUD | 工具+状态+UI | **P1** |
| 子代理 | ❌（Agent 可复用，无 tool） | task（batch/async/隔离/结构化） | 工具+运行时+权限+成本 | P1 条件/P2 |
| 后台作业+等待 | ❌ | async jobs + wait | 新子系统 | P2 |
| 模型主动提问 | ❌（只有审批） | ask | 工具+UI | P2（plan 工作流则 P1） |
| 代码化编排 | ❌ | eval 内核 | 新子系统 | N |
| 长期记忆 | ❌ | retain/recall/reflect | 外部依赖 | N |
| 写 skill | ❌ | manage_skill/learn | 自修改面 | N |
| 工具政策提示词 | 一句 "prefer tools" | 三段式 Tool Policy | 纯文案 | **P1（半天）** |

## 最后：值得抄的最小集 / 明确不抄 / 待裁决问题 + 最小 v0.4 提案

### 12.1 值得抄的最小集（按 ROI 排序）

1. **todo 工具 + sidecar 状态 + HUD 骨架 + 5 条提示词规则**（P1，§12.4 给边界）。
2. **系统提示的"工具政策"三段式**（Specialized / General / Exploration），把"何时用 todo、何时并行、何时别猜文件"写进去（P1，半天，`prompts/system/system-prompt.md:96-136`）。
3. **task 的结果 envelope + 成本归属契约**（只在真的做 task 时；`prompts/tools/task-summary.md:1-23` + `subagentCost` 思路）。
4. **ask 的 plan-mode 语义**（P2；若做 plan 批准流程则升 P1）：推荐项、无超时、批问 2–5 选项。
5. **wait 的 `useless` + `interruptible` 两个标记**（P2，随后台作业一起；先在内核 `ToolResultAgent` 加 `useless` 字段，压缩可以据此丢结果）。
6. **omp 的"按内容寻址 + 批量原子性"**（做 todo 时直接抄，省掉 ID 管理）。

### 12.2 明确不抄

| 不抄 | 理由 |
|---|---|
| worktree / overlay 隔离（`task/worktree.ts` 1088 行） | 需要 Git 操作 + 原生 mount + 清理策略；ZenAgent 单进程单 worktree 定位不需要 |
| IRC 对等消息 / `agent://` 读写 / 广播 | 多 agent 协作世界的前置；ZenAgent 没有常驻 agent 池 |
| `workpool` / `seance` / speculative launch | 都是"多子代理 + 长任务"的优化，先把单发做对 |
| completion probe（额外 LLM 估完成度） | 直接成本，无对应收益 |
| `xd://` 设备发现（30 个工具按需挂载） | 9 个工具不需要；且 18.14.0 已删除配套的 BM25 工具检索层（`omp/config/settings.ts:3243-3249`），说明这层复杂度收益不稳 |
| memory backend 抽象（hindsight/mnemopi/local） | 外部服务 + 抽象层；ZenAgent 若要记忆，一个 JSON 文件即可【推断】 |
| managed skill 自改写（learn/manage_skill） | 打开自修改面（权限、冲突、回滚），与"最小内核"冲突 |
| goal mode 状态机 | 独立产品功能，不是工具层问题 |
| eval 内核（py/js + 工具桥） | 与 httpx-only、无框架定位冲突；成本 >> 收益 |
| omp 的 9 工具 per-tool 超时表 | ZenAgent 只需为"会挂起"的新工具定上限 |

### 12.3 待 Lead 裁决的开放问题

1. **v0.4 的主题**：是"长任务可靠性"（→ todo + 提示词政策）还是"并行/委派"（→ task）？两者可以不同版本。
2. **todo 是否需要提醒环**：omp 的提醒依赖"停止时钩子 + 自动续跑"。ZenAgent 有 follow-up 队列（`zenagent/app/chat.py:140-167`），自动续跑与用户排队语义可能冲突。建议 v0.4 只做**HUD 提示条**，不自动续跑【推断】。
3. **是否允许工具写 sidecar**：todo 状态需要 `<session>.todo.json`；这会新增一种"会话附属文件"。已有 `.titles.json`/`.pins.json` 先例（`zenagent/agent/session.py:44-48,181-193`），可复用原子替换函数。
4. **plan 模式是否升级为工作流**：若升级（plan 文件 + 批准），ask 和 `plan-mode-guard` 的粒度都要重做，优先级会变化。
5. **后台作业是否在 v0.5 路线图**：决定 wait 是否永远 P2。

### 12.4 首选（TODO）的最小实现边界

- **数据模型**：`TodoItem{content, status}`（status ∈ pending/in_progress/completed/abandoned/blocked，blocked 带 `blocker?`）；`TodoPhase{name, tasks[]}`；顺序即阶段顺序；**按 content 在全局唯一寻址**（照 `omp/tools/todo.ts:81-91`）。
- **存放**：`<session>.todo.json` sidecar（无 session 时进程内存）；每次成功变更后**原子替换**（复用 `session.py:181-193` 的写法）；resume/fork 时读；与 transcript 解耦——因为 `details` 不落盘（`zenagent/types.py:179-194`）。
- **工具面**：单工具 `todo`，参数 `{op, list?, task?, phase?, items?, reason?}`；op 集合与 omp 相同 9 个；返回值 = 精简 summary（剩余项 + 进度 + 当前 phase）；`details={"phases": [...]}` 给 UI；错误用 `is_error` + 可行动文案（内容寻址失败时提示"传完整文本"）。
- **提示词**：把 `prompts/tools/todo.md` 压到 ~120 词（同轮调用、内容寻址、3+ 步才 init、阶段顺序、blocked 语义）。
- **UI 钩子**：dock 新增 `TodoHud(Static)` 挂在 `StatusBand` 上方（`zenagent/cli/tui/app.py:160-175`）；数据来自 `ChatController.status` 新增 `todos` 字段（`zenagent/app/views.py:65-83` + `chat.py:71-99`）；工具卡已能渲染 `details`（`zenagent/cli/tui/widgets/tool_card.py:120-160`），无需改。
- **测试**：`tests/test_todo.py`——9 个 op 的状态机、批量原子性、内容寻址错误、in_progress 归一化、sidecar 往返、压缩/resume 后仍在；权限门测试确认 `todo` 属只读档（加进 `READ_ONLY_TOOLS`，`permissions.py:32`）。
- **明确不做**：eager 强制 `tool_choice`（ZenAgent 的 provider 适配器当前不设置 `tool_choice`，`zenagent/providers/openai_completions.py:200`）、停止自动续跑、Markdown 往返编辑、HUD 显隐持久化。

### 12.5 次选（TASK）的最小边界（若 Lead 决定同版做）

同步单发 `task(prompt, name?)`、child = 新建 `Agent`（同 model、只读工具集 + 独立 ContextGuard）、递归深度 1、无隔离、无 agent 定义、结果 envelope 文本、`RunStatus.subagent_cost` 汇总；plan 模式直接拒绝；ask 模式下 child 的写工具不继承（避免嵌套弹窗）【推断】。异步/批量/隔离/结构化 yield 全部推迟。

### 12.6 为什么不是 WAIT 先做

wait 的语义对象是"自己启动的后台作业/服务/本轮提问的回复"（`omp/tools/wait.ts:88-95`），ZenAgent 三者皆无；先做 wait 只能得到"一个永远返回 Nothing to wait for 的工具"，模型会学会忽略它——比不做更糟。它的正确前置是 background bash（job 表 + 完成注入 + 消费语义），那是独立的一个版本主题【推断】。
