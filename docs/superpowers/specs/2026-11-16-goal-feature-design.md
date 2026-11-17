# Goal Feature 设计（2026-11-16）

状态：已定稿（2026-11-16）
作者：Lead（与用户讨论后整理）
相关：`docs/reviews/2026-11-16-coding-harness-goal-designs.md`、`zenagent/app/chat.py`、
`zenagent/app/runtime.py`、`zenagent/agent/session.py`、`zenagent/app/records.py`、
`zenagent/app/commands.py`、`zenagent/app/views.py`、`zenagent/cli/rpc/`、`desktop/src/`

---

## 1. 意图与为什么

Zen Agent 现在有 plan、todo、checkpoint/rewind，但没有一个跨轮次的「完成契约」：
一个长任务在一个 turn 结束后就停下来，用户必须手动重复「继续」。这既浪费交互，
也让「无人值守跑一段时间」不可能。

Goal 要补的是这一层：

> 用户给出一个**可验证的目标**，agent 每轮结束后由独立检查判断是否完成；
> 未完成且未超预算，就自动开始下一轮；完成、阻塞、预算耗尽或用户暂停时停止。

它不是 plan mode（动手前的设计），也不是 todo（一轮内的步骤），而是跨 turn 的
「什么算做完」。一期同时服务两种场景：

- **交互式**：TUI/REPL 里正常对话，随时 `/goal`，人可以在旁边纠偏；
- **无人值守**：应用开着、人离开，goal 自动续跑；关掉应用即暂停，下次打开恢复。

TUI 和 Desktop GUI 共用同一套命令、状态和 RPC，不为两个前端做两套逻辑。zen/swarm
的 worker 调度改造不在本期，但 goal 的状态机和接口要能被后续 worker 复用。

---

## 2. 目标 / 非目标 / 反目标

### 目标

1. TUI/REPL 和 Desktop GUI 的普通对话里支持 `/goal`：设置、查看、暂停、恢复、清除。
2. goal 绑定当前 session；状态跨 turn、跨 compaction、跨 resume 保留。
3. 每轮结束后自动判定；未完成且未超预算时自动续跑；用户消息优先于 goal 续跑。
4. 完成判定优先使用用户提供的确定性 `--check` 命令；没有 check 时使用一次独立的
   one-shot 模型调用读 transcript 判三态（默认当前 session model，独立上下文）。
5. 硬预算：turn、wall-clock、token；连续无进展/验证失败/检查超时有阈值；所有终止
   状态命名且可解释。
6. 状态在 TUI 状态栏/侧栏和 GUI 的 goal chip/panel 可见；命令输出给出完整状态。
7. 不改变现有权限模型：goal 续跑仍是普通 user message，工具仍走 permission broker。
8. 为 zen/swarm 留出复用点：`GoalController` 不依赖 TUI/GUI，可按 session 实例化。

### 非目标

- 不做 daemon / 后台进程 / 应用关闭后继续跑。
- 不做云端/CI runner。
- 不做一 session 多 goal 并行。
- 不做从普通任务自动推断 goal（必须用户显式 `/goal`）。
- 不做 zen/swarm worker 的 goal 化调度（二期再议）。
- 不做 goal 的定时/事件触发（cron/webhook）。
- 不做多裁判评审团（Grok 风格）；一期是单裁判。
- 不做 goal 的语义记忆/跨项目复用。

### 反目标（出现即失败）

- goal 完成只凭 agent 自己说「done」，没有独立检查。
- goal 文本被当成更高优先级指令，或 goal 扩大工具权限。
- 没有预算/无进展检测，agent 无限循环烧钱。
- 用户中断/报错后 goal 仍自动重启。
- 关掉应用或 compaction 后 goal 状态丢失。
- goal 续跑阻塞 UI，用户无法插话、暂停或查看状态。
- 两个 goal 同时写一个 checkout 且互相踩（一期用文档警告 + 建议 worktree）。

---

## 3. 约束

- **app 层实现**：`zenagent/app/goal.py` 持有状态机、持久化、验证器；`agent/` 的
  loop 不认识 goal。kernel 侧只做一处「消息标记」的透传：`UserMessage` 增加可选
  `goal: bool = False`（JSON 往返保留），`Agent.prompt(..., goal=False)` /
  `agent_loop(..., user_goal=False)` 把它写到首条 user message；loop 不根据它分支。
  这不是行为改动，只是 provenance（和现有 `steering` 标记同型）。
- **一个 session 一个 active goal**：goal 是 session 级，不是项目级/全局级。
- **in-process**：应用进程活着才续跑；应用退出即暂停，重新打开恢复 active goal。
- **session JSONL append-only**：goal 作为 side-channel record 持久化，不引入 SQLite。
- **所有前端共用**：`CommandRegistry`、`RunStatus`、RPC `status`/`command`；GUI 不新增
  一套独立命令协议。
- **权限不变**：goal 不改 permission mode；`--check` 是用户显式提供的命令，模型不可改。
- **无新依赖**：只用标准库 + 现有 `httpx`/provider 适配器。
- **TDD**：每个任务先写失败测试；`bash scripts/check.sh` 是唯一闸门。

---

## 4. 方案取舍（已决策）

### A. app 层 `GoalController`（选中）

- 新增 `zenagent/app/goal.py`：`GoalState`、`GoalController`、`GoalVerifier`。
- `/goal` 注册进 `build_default_commands`；TUI/REPL 直接用，GUI 通过 RPC `command` 复用。
- 状态用 `Session.append_meta` 的 `goal` record 持久化；`Runtime.session` setter 绑定/恢复。
- `ChatController._run` 的 finally 里，用户队列清空后触发 `goal.after_turn()`；未完成且
  未超预算就注入下一条 goal 续跑。
- 不改 kernel 行为；zen/swarm 以后可按 session 复用同一个 controller。
- 代价：app 层多一个模块和一条异步链；但最容易测，最不污染内核。

### B. kernel 级 goal hook（落选）

把 goal 状态和 `agent_end` 续跑放进 `Agent`，所有前端自动获得；但内核变大，goal 语义
泄漏进核心，持久化和 UI 仍要另做，验证也容易退化成模型自报。

### C. extension/plugin 实现（落选）

核心改动最少，但 `/goal` 的会话绑定、状态栏、权限和 resume 都要和插件生命周期打架；
一期不划算。

---

## 5. 设计

### 5.1 状态模型

```text
active ──► complete            （check/verifier 判定达成）
   │
   ├──► paused                （用户 /goal pause；用户中断）
   ├──► blocked               （无进展 3 轮 / 同一验证失败 3 次 / impossible / 运行错误）
   └──► budget_limited        （turn / wall-clock / token 预算耗尽；一次 wrap-up 后终止）
```

| 状态 | 含义 | 可自动续跑 | 可 `/goal resume` |
|---|---|---|---|
| `active` | 正在推进 | 是 | 无操作 |
| `paused` | 用户暂停 / 用户中断 | 否 | 是 |
| `blocked` | 需要用户或外部状态变化 | 否 | 是（重置阻塞计数） |
| `budget_limited` | 预算耗尽，已给收尾轮 | 否 | 否（需新 goal） |
| `complete` | 目标达成 | 否 | 否（需新 goal） |

`GoalState`：

```python
@dataclass
class GoalState:
    goal_id: str                 # uuid4().hex
    objective: str               # 1..4000 chars
    status: GoalStatus           # Literal["active","paused","blocked","budget_limited","complete"]
    check: str | None            # 用户提供的确定性检查命令
    max_turns: int               # 默认 40；/goal --budget N 覆盖
    max_tokens: int              # 默认 2_000_000（input-cache + output）
    max_wall_seconds: int        # 默认 4h
    turns_used: int
    tokens_used: int
    wall_seconds_used: float
    no_progress_turns: int       # 连续无工具调用的自动 goal turn
    verifier_failures: int       # 连续验证失败（网络/解析/不可用）
    check_failures: int          # 连续同一 check 失败签名
    last_check_key: str          # exit code + 输出尾行的归一化签名
    last_verdict: str | None     # "met" | "not_met" | "impossible" | None
    last_reason: str             # 最近一次判定/阻塞/预算原因
    created_at: float
    updated_at: float
```

`GoalView`（UI 只读快照，放 `zenagent/app/views.py`）：

```python
@dataclass
class GoalView:
    objective: str
    status: str
    check: str | None
    turns_used: int
    max_turns: int
    tokens_used: int
    max_tokens: int
    wall_seconds: float
    max_wall_seconds: int
    last_verdict: str | None
    last_reason: str
    updated_at: int
```

`RunStatus` 增加 `goal: GoalView | None = None`；`to_wire()` 自动带到 RPC。

规则：

- 一个 session 同时只有一个 goal；`/goal <new>` 替换旧 goal，并记录 `replaced` 原因。
- `complete` / `budget_limited` 是终态；要再来一次只能设新 goal。
- `paused` / `blocked` 可以 `/goal resume`；resume 重置 `no_progress_turns`、
  `verifier_failures` 和 `check_failures`，保留已用预算（防止通过 pause/resume 逃预算）。
- check 连续 3 次返回同一个失败签名（exit code + 输出最后一个非空行，归一化）→
  `blocked`，`last_reason` 标明「check keeps failing identically」。这防止一个写错的
  check（例如 exit 127）烧满整个 turn/token 预算。
- 新 goal 的 `goal_id` 必须变化；旧 goal 记录留在 session 里供审计。

### 5.2 持久化

新增 record kind `goal`（`zenagent/app/records.py`）：

```json
{
  "type": "goal",
  "goal": {
    "goal_id": "…",
    "objective": "…",
    "status": "active",
    "check": "pytest -q",
    "max_turns": 40,
    "max_tokens": 2000000,
    "max_wall_seconds": 14400,
    "turns_used": 3,
    "tokens_used": 51234,
    "wall_seconds_used": 421.0,
    "no_progress_turns": 0,
    "verifier_failures": 0,
    "last_verdict": "not_met",
    "last_reason": "3 tests failed",
    "created_at": 1760000000.0,
    "updated_at": 1760000421.0
  }
}
```

- 每次状态变化 append 一条完整快照；`GoalController` 读取 `session.records` 里最后一条
  有效 `goal` record（`goal` 为 `null` 表示已清除）。
- `records.py` 增加 `GOAL`、`goal(state: dict) -> dict`、`is_goal(record)`；把 `GOAL`
  加入 `KNOWN_KINDS` / `KINDS`。
- `keep_for_rewrite(keep_goal=True)`：goal record 在 undo/retry/compact/shake 时保留；
  `SessionManager.fork()` 传 `keep_goal=False`，fork 出来的 session 不继承 goal。
  理由：fork 是新的工作分支，继承 active goal 可能意外续跑；用户想继续可在 fork 里
  重新 `/goal`。
- lazy session：goal 在首条消息前 append 会进 `_pending_records`，首条消息写入时按序落盘；
  `materialize()` 也会 flush。
- `Runtime.session` setter：`self.goal.bind_session(session)`；取消正在进行的验证/检查，
  读取新 session 的 goal。旧 session 的 goal 不再驱动（状态保留在文件里）。
- resume 与 session 切换：
  - **默认（`goal_pause_on_session_switch = false`）**：切换离开当前 session 时，
    goal 保持 `active`；切回该 session 时如果 goal 仍 active，继续自动续跑。
    `/sessions` 浏览本身不会启动目标 session 的 goal，只有切回并成为当前 session 后
    才会继续。
  - **用户开启 `goal_pause_on_session_switch = true`**：切换离开时把当前 active goal
    置 `paused`（reason: session switched）；切回后保持 paused，等 `/goal resume`。
  - 应用启动时恢复的 session：active goal 继续（默认行为）；带 `check` 的 goal 在
    重启/导入时恢复为 `paused`，必须 `/goal resume` 确认后才会执行命令（session 文件
    可能来自导入/手改，不能默认执行 shell）。
  - `/fork` 不继承 goal；`/import` 进来的 goal 以 `paused` 恢复。
- compaction：`Session.load` 收集整个文件的 records，不按 segment 截断；goal 不丢。
- 写失败：沿用 `Session.append_meta` 的 best-effort；`Runtime.persist_failures` 的现有
  通知照常显示。一期不因 goal record 写失败中断 run。

### 5.3 命令面

```text
/goal <objective>                        设置/替换 goal，并立即开始第一轮
/goal --check "<command>" <objective>    带确定性检查
/goal --budget <turns> <objective>       覆盖 turn 预算（时间/token 用配置默认）
/goal                                    查看当前状态
/goal pause                              暂停（不中断当前 run，只停止后续续跑）
/goal resume                             恢复 paused/blocked；立即开始下一轮
/goal clear                              清除 goal（别名 stop/off/cancel）
```

解析规则：

- 空参数 → status。
- 第一个词是 `pause|resume|clear|stop|off|cancel` 且没有更多参数 → 生命周期命令。
- 否则扫描开头的 `--check <value>` 和 `--budget <n>`；`--check` 的值支持单/双引号，
  也可以是一个不含空格的 token；`--budget` 必须是正整数。剩余原文是 objective。
- 标志必须在 objective 之前；objective 里出现 `--check` 不解析为标志。
- objective 为空或超过 4000 字符 → 返回错误，不改变现有 goal。
- `/goal <new>` 在已有 active goal 时替换：旧 goal 记录保留，返回消息里说明 replaced。
- 当前 busy 时设置 goal：状态立即写入，第一轮等当前 run 和用户队列清空后开始；
  返回「goal set; will start when the current turn finishes」。
- `/goal pause` 在验证/检查进行中：取消验证任务，置 `paused`；当前 agent run 不 abort。
- `/goal clear` 在 run 中：置清除，不 abort 当前 run；后续不再续跑。
- status 输出：objective、status、check、turns/max、tokens/max、wall/max、last verdict、
  last reason、created/updated；complete/budget_limited 显示终态原因。
- i18n：命令描述、所有 `cmdmsg.goal_*`、TUI/侧栏/状态栏文案进 `i18n/en.py` + `zh.py`。

### 5.4 续跑循环

集成点：`ChatController._run` 的 `finally`，在自动 compaction 之后、用户队列 drain 之前。

伪代码：

```python
finally:
    termination = ...
    self._builder.finalize()
    ...
    if termination == "stop" and not self._pending_queue.items:
        ctx = GoalTurnContext(
            messages=list(self.runtime.agent.messages),
            auto=self._goal_turn,
            tool_calls=self._turn_tool_calls,
            termination=termination,
            usage_delta=...,
            elapsed_s=...,
            user_queued=False,
        )
        decision = await self.runtime.goal.after_turn(ctx)
        if decision.action == "continue":
            self._task = asyncio.create_task(
                self._run(decision.prompt, goal=True)
            )
            return
    # 原有逻辑：steering continuation / queue drain / idle
```

**预算看门狗（硬上限）**

token / wall-clock 不能只在 turn end 检查：一个 goal turn 可能因为卡住的工具、审批等待
或 provider 重试跑很久。goal turn 进行中，`ChatController` 起一个轻量 watchdog
（每 5s + 每次 `message_end` 检查一次），用 `usage_total` / `SubagentTracker.tokens` /
active wall time 计算；超过 token 或 wall 上限时：

1. 设置 `goal.budget_abort = True`；
2. 调 `agent.abort()`；
3. `after_turn` 看到 `termination="aborted"` 且 `budget_abort` 为真时映射
   `budget_limited`，而不是 `paused`；
4. token / wall 到顶**不再给 wrap-up turn**（硬上限），只把最后的 assistant 文本和
   预算原因展示给用户；turn 数到顶才给一次 wrap-up（见 §5.6）。

wall time 只在 live turn / verification 期间累计；`active_since` 只在内存，turn 结束
时把 elapsed 加到 `wall_seconds_used` 并落 record，重启不计算停机时间。

具体规则：

- **用户优先**：`_pending_queue` 有 queue lane 时先跑用户消息，本轮不做 goal 续跑；
  用户消息跑完后，如果 goal 仍 active，再评估续跑。
- **只在 clean stop 后续跑**：`termination == "stop"` 才自动续跑；`aborted` → `paused`
  （`budget_abort` 为真时 → `budget_limited`）；`error` → `blocked`；
  `max_turns` / `max_cost` → `blocked`（用户可 resume）。
- **后台工作**：如果 `TaskManager.running_call_ids()` 非空，或 `ChatController` 仍有
  后台 job，推迟 goal 验证/续跑，等下一次 idle。不要和 subagent 抢同一个 run。
- **验证期间保持 busy**：`ChatController.busy` 在验证/check 期间为 True，UI 显示
  「checking goal」；用户输入进 queue，验证结束后先跑用户消息。
- **goal turn 标记**：`_run(..., goal=True)` 记录 `self._goal_turn = True`，并通过
  `Agent.prompt(text, goal=True)` → `agent_loop(..., user_goal=True)` 把
  `UserMessage.goal = True` 写进首条消息；用户 turn 为 False。loop 不读这个标记。
  `turns_used` 对 active goal 的所有 clean turn 计数；`no_progress_turns` 只对 auto
  goal turn 递增，用户 turn 重置为 0。
- **工具调用计数**：`ChatController._on_event` 在 `tool_execution_end` 时递增
  `self._turn_tool_calls`；`_run` 开始时清零。没有工具调用的 auto turn 视为无进展。
- **第一轮**：`/goal` 设置后，如果 idle，立即 `_run(objective, goal=True)`；objective
  作为首条 goal user message（不是普通 user message）。
- **暂停/清除**：`GoalController.pause/clear` 只改状态；当前 run 继续，后续 `after_turn`
  不续跑。`/goal resume` 在 idle 时立即起一轮。
- **不阻塞 UI**：验证/check 是异步 task；TUI/GUI 仍可输入、滚动、`/goal pause`。
- **递归保护**：`_run` 里最多起一个 goal continuation task；`after_turn` 不会在
  验证 task 未结束时再次进入（`_goal_checking` 标志或 asyncio.Lock）。

### 5.5 验证

```python
class GoalVerifier(Protocol):
    async def verify(self, goal: GoalState, ctx: GoalTurnContext) -> GoalVerdict: ...

@dataclass
class GoalVerdict:
    verdict: Literal["met", "not_met", "impossible"]
    reason: str
    evidence: str = ""      # 检查输出尾部 / 裁判引用
    tokens: int = 0         # 验证消耗，计入 goal 预算
```

两种实现，按 goal 是否带 `check` 选择：

**CheckVerifier（优先）**

- 用 `asyncio.create_subprocess_shell` 在 `runtime.cwd` 执行 `goal.check`；
  `stdout=PIPE, stderr=STDOUT`，`start_new_session=True`，超时 `check_timeout_s`（默认 300s）。
- 超时杀进程组；捕获输出尾部 ≤ 8 KB。
- exit 0 → `met`；非 0 → `not_met`，reason 用输出尾部（截断到 2000 字符）。
- 连续 3 次超时或启动失败 → `blocked`；单次超时算 `not_met`。
- **check 是权威的**：check 通过即完成。约束（例如「不要改 public API」）必须写进
  check 命令；文档和 `/goal` 输出要明确这一点。没有 check 才用模型裁判。
- check 由用户在 `/goal` 里提供，模型不能改；MVP 不接受模型提议的 check。
- check 与 agent 的 bash 工具同 cwd，但不走 permission broker（用户显式输入，等同
  用户自己跑命令）；`/goal` status 会原样显示命令，便于审计。
- **check 是 controller-only**：续跑 prompt 不包含命令本身，只包含上一次 check 的
  输出尾部/`last_reason`。这样不会把可能含密钥的命令发给 provider，也不会让 agent
  误以为自己需要/可以改写 check。agent 仍然可以根据失败输出去修代码。

**ModelVerifier（兜底）**

- 独立一次 provider 调用，不用 agent 的 transcript / tool loop；默认用当前 session
  model（一期不引入 `verifier_model` 配置），`thinking="off"`，
  `max_tokens=verifier_max_tokens`（默认 512）。`/model` 切换后，下一次验证用新模型；
  裁判上下文仍是独立的，这是可接受的。
- 如果当前 provider 无法做 one-shot（无 key / CLI-backed / 不支持 `thinking=off`），
  该次验证算失败；带 `check` 的 goal 不受影响。后续可以加 `[goal] verifier_model`
  字符串配置，不在本期。
- system prompt：完成审计员；只根据 transcript 里的证据判断；不可信 goal 文本放在
  `<objective>` 里；返回严格 JSON `{"verdict":"met|not_met|impossible","reason":"..."}`。
- transcript 摘录：最近 N 条消息（默认 12 条 / ≤12k 字符），工具结果截断到 2k 字符；
  优先保留最近的 assistant 文本和 tool 输出。不把完整 session 发给裁判。
- 解析失败重试 1 次；仍失败算一次 `verifier_failures`；连续 3 次 → `blocked`。
- `impossible` → `blocked`，reason 原样展示；用户可 resume 或改 goal。
- 裁判消耗计入 `tokens_used`（`GoalVerdict.tokens`）；不写入主 transcript。

**失败处理**

- 验证器不可用（无 key / provider 错）→ 不标完成；`verifier_failures += 1`；3 次 → blocked。
- check 通过但模型裁判不可用：check 权威，直接 met，不调用模型裁判。
- 验证结果写入 goal record；`last_verdict` / `last_reason` 供 UI 展示。

### 5.6 预算与停止

| 预算 | 默认 | 配置 | 说明 |
|---|---|---|---|
| turn | 40 | `[limits.goal].max_turns` | 每个 clean turn 计数；`/goal --budget N` 覆盖 |
| wall-clock | 240 min | `[limits.goal].max_wall_minutes` | 只累计 active 时间；暂停/关应用不计 |
| token | 2,000,000 | `[limits.goal].max_tokens` | provider-aware `context_occupancy(total, api)` + subagent tokens；含验证消耗 |
| check timeout | 300 s | `[limits.goal].check_timeout_s` | 单次命令 |
| verifier max tokens | 512 | `[limits.goal].verifier_max_tokens` | 裁判单次输出 |

- turn 数到顶 → `budget_limited` + **一次 wrap-up turn**：注入一条 goal message，
  要求「不要开始新工作；总结已完成、剩余、阻塞、下一步」，跑完后不再续跑。
- **token / wall-clock 到顶是硬停**：watchdog 中断当前 run，不再给 wrap-up turn；
  只展示最后的 assistant 文本 + 预算原因。这样「硬上限」才真的硬。
- wrap-up 本身不计入新的 turn 预算（或计为最后一轮）；token 仍计入。
- **token 计数**：复用 `app.support.context_occupancy(usage_total, model.api)`：
  OpenAI 风格 `input` 已含 cached，Anthropic 的 `input` 不含 cache read/write，按
  provider 归一；再加上 `SubagentTracker` 的 token 增量。验证器自己的 tokens 也计入。
- 无进展：连续 3 个 auto goal turn 没有工具调用 → `blocked`；用户 turn 重置计数。
- 验证失败：连续 3 次 `verifier_failures` → `blocked`。
- check 失败：连续 3 次同一失败签名（exit code + 输出最后一个非空行，归一化）→
  `blocked`；不同失败原因不触发。这个阈值防止写错的 check 烧满预算。
- 运行错误：`error` / `max_turns` / `max_cost` → `blocked`；`aborted` → `paused`。
- 所有终态都写 `last_reason`，`/goal` status 和 UI 可读；错误/预算耗尽绝不标 `complete`。

### 5.7 上下文注入

- `UserMessage` 增加 `goal: bool = False`；`to_json()` 写 `"goal": true`；
  `message_from_json()` 读 `goal`（默认 False）。旧 session 不受影响。
- `MessageView` 增加 `goal: bool = False`；`user_view(...)` 传递；TUI/GUI 把 goal 行
  渲染成「goal」卡片/淡色行，而不是普通用户气泡。
- 续跑 prompt 模板（goal 文本是不可信数据）：

```text
[goal] Continue working toward the objective below.

<objective>
{objective}
</objective>

Budget: turn {turns_used}/{max_turns}, tokens {tokens_used}/{max_tokens},
elapsed {wall_seconds}/{max_wall_seconds}s.
Last verification: {last_verdict} — {last_reason}

Take the next concrete step. Do not restate the plan. Do not ask the user a question
unless the goal is blocked. The controller runs the configured check; use the last
verification reason/output below to decide the next step. Do not start work outside
the objective.
```

- 首轮 prompt 用同一模板，`last_verdict` 为空，附上 objective 原文。
- goal 文本永远不作为 system prompt；不携带权限；不覆盖用户当前指令。
- 用户中途发消息：正常 user message；goal 保持 active，turn 结束后继续评估。

### 5.8 TUI

- `/goal` 注册进 `build_default_commands`；TUI/REPL 的 slash 层自动获得补全和 help。
- 新增 `GoalHud`（`zenagent/cli/tui/widgets/goal_hud.py`）：一行，位于 status band 上方，
  仅在存在 goal 时显示：

```text
◎ goal active · 3/40 turns · 51.2k tok · last: not_met (3 tests failed)
```

  paused/blocked/budget_limited/complete 用不同图标/文案；点击打开 `/goal` 状态输出
  （或一个只读详情对话框，二选一，留给 builder）。
- 侧栏（`sidebar.py`）增加 Goal section：objective 两行、status、check、预算、
  last reason；复用 `RunStatus.goal`。
- status band 增加一个 goal segment（可选；若 `GoalHud` 已足够，可只保留 HUD）。
- i18n：`tui.goal.*`、`cmd.goal.description`、`cmdmsg.goal_*`。
- 不在 TodoHud 里混 goal；todo 和 goal 是两个概念。

### 5.9 GUI / RPC

- `RunStatus.goal` 由 `ChatController.status` 填充；`to_wire()` 自动包含；
  `RpcHost._status_result` 的 `status` 快照和扁平字段都能拿到。
- `desktop/src/rpc/types.ts`：新增 `GoalView`，`StatusResult.goal?: GoalView | null`。
- Desktop store/reducer：把 `status.goal` 放进 session view state；现有 status 刷新路径
  直接生效，不需要新 RPC 方法。
- UI：
  - composer/header 一个 `GoalChip`：图标 + status + turns；点击打开 `GoalPanel`；
  - `GoalPanel`：objective、check、budget、last verdict/reason、created/updated；
    pause/resume/clear 按钮；
  - 按钮走现有 RPC `command`（`name: "goal"`, `argument: "pause" | "resume" | "clear"`），
    不新增 RPC 方法；
  - slash menu 从 `commands` catalog 自动拿到 `/goal`；
  - goal 续跑消息用 `goal` 标记渲染成 goal 行，不显示成用户输入。
- 应用关闭：RPC 子进程退出，goal 状态已在 session 文件；重新打开恢复 active 并续跑。
- `GoalView` 字段用 snake_case 与 `RunStatus` 一致；TypeScript 侧保持 snake_case，
  避免一层无意义的 camelCase 映射（现有 `StatusResult` 也是 snake_case）。

### 5.10 配置

在 `zenagent/limits.py` 新增 `[limits.goal]`：

```toml
[limits.goal]
max_turns = 40
max_wall_minutes = 240
max_tokens = 2000000
check_timeout_s = 300
verifier_max_tokens = 512
```

- `Limits` 增加对应字段；`LIMITS` registry 增加 5 个 knob（int，apply="session"）；
  `parse_limits` / `render_limits_block` / `describe` / setter 自动覆盖。
- 全局配置（和现有 `[limits]` 一样，项目配置不能覆盖预算）。
- `verifier_model` 一期不做独立配置：用当前 session model + 独立上下文；后续需要再加
  `[goal] verifier_model`（字符串 knob，不在本期）。
- `/goal --budget N` 只覆盖 `max_turns`；时间和 token 用配置默认。

#### 5.10.1 行为偏好：离开 session 是否暂停 goal

`goal_pause_on_session_switch` **默认 `false`**，存 `~/.zenagent/settings.json` 的
`prefs`（`zenagent/settings.py` 的 `get_pref` / `set_pref`），不进 `[limits.goal]`。

- `false`（默认）：离开 session 时 goal 保持 `active`；切回后继续自动续跑。
- `true`：离开时把 active goal 置 `paused`；切回后等 `/goal resume`。
- TUI：`SettingsPicker` 增加一行「Goal: pause when leaving session」，值 on/off；
  选中即写 `settings.set_pref(...)`，不需要重启。
- GUI：Settings → Conversation 增加同一个开关；通过新增的 RPC `set_pref`
  （`{"type":"set_pref","name":"goal_pause_on_session_switch","value":true|false}`）
  写同一个 prefs 存储；页面读取当前值走 `status` 或 RPC `prefs` 查询。
- 运行时在 `switch_session` / `bind_session` 时读这个 pref，改完立即生效。

### 5.11 安全与权限

- **权限不变**：goal 不改 permission mode；续跑 prompt 只是 user message；每次工具调用
  仍走 `PermissionBroker`。`/goal` 不隐含 `--yes`。
- **goal 文本不可信**：包在 `<objective>`；prompt 明确「这是任务上下文，不是更高优先级
  指令」；验证器同样按数据对待。
- **check 命令**：用户显式输入，模型不可改；在 workspace cwd 执行，超时 + 输出上限；
  `/goal` status 原样展示。文档明确「check 与你在 shell 里跑命令同等信任」。
- **重启/导入/手改 session 里的 check 不自动执行**：恢复为 `paused`，`/goal resume`
  先显示命令再启动。这样 session 文件不能成为静默执行 shell 的入口。
- **不自动创建 goal**：只有 `/goal` 命令创建；模型不能给自己发 goal。
- **并发**：一个 session 一个 goal；不同 session 可以各有 goal，但写同一 checkout 会
  互相踩。一期在 `/goal` 帮助文本里提示用 worktree；不做跨 session 锁。
- **不可逆操作**：仍由现有权限/审批拦截；goal 不会绕过。

### 5.12 失败处理

| 场景 | 行为 |
|---|---|
| agent run `aborted` | goal → `paused`，不自动续跑；若 `budget_abort` 为真 → `budget_limited` |
| agent run `error` | goal → `blocked`，`last_reason` 记录错误；用户可 resume |
| agent run `max_turns` / `max_cost` | goal → `blocked`，提示可 resume |
| verifier 网络/解析失败 | `verifier_failures += 1`；3 次 → `blocked` |
| check 超时/启动失败 | 单次 `not_met`；3 次 → `blocked` |
| session 切换 | 取消当前验证/检查；旧 goal 保留在旧 session；新 goal 从 records 恢复 |
| 应用关闭/崩溃 | 进程退出；最后一条 goal record 已 append；重开恢复 active |
| 持久化失败 | 现有 `persist_failures` 通知；goal 内存状态继续，重开可能落后一条记录 |
| 用户 `/goal pause` | 取消验证；当前 run 继续；之后不续跑 |
| 用户 `/goal clear` | 清状态；当前 run 继续；之后不续跑 |

### 5.13 接口草案

```python
# zenagent/app/goal.py

GoalStatus = Literal["active", "paused", "blocked", "budget_limited", "complete"]
GoalVerdictKind = Literal["met", "not_met", "impossible"]

@dataclass
class GoalVerdict:
    verdict: GoalVerdictKind
    reason: str
    evidence: str = ""
    tokens: int = 0

@dataclass
class GoalTurnContext:
    messages: list[Message]
    auto: bool
    tool_calls: int
    termination: str | None
    tokens_delta: int            # provider-aware context_occupancy delta
    subagent_tokens_delta: int
    elapsed_s: float
    user_queued: bool

@dataclass
class GoalDecision:
    action: Literal["continue", "stop"]
    status: GoalStatus
    prompt: str | None = None
    reason: str = ""
    wrap_up: bool = False

class GoalController:
    def __init__(self, *, limits: GoalLimits | None = None,
                 verifier: GoalVerifier | None = None) -> None: ...
    def bind_session(self, session: Session) -> None: ...
    def snapshot(self) -> GoalView | None: ...
    def set(self, objective: str, *, check: str | None = None,
            max_turns: int | None = None) -> GoalState: ...
    def pause(self) -> GoalState | None: ...
    def resume(self) -> GoalState | None: ...
    def clear(self) -> None: ...
    def note_turn_start(self, *, auto: bool) -> None: ...
    def note_budget_abort(self) -> None: ...
    async def after_turn(self, ctx: GoalTurnContext) -> GoalDecision: ...
```

```python
# zenagent/agent/agent.py + loop.py

async def Agent.prompt(self, text: str, images: list[ImageContent] | None = None,
                       *, goal: bool = False) -> None: ...
async def agent_loop(..., user_goal: bool = False) -> AsyncIterator[AgentEvent]: ...
```

```python
# zenagent/app/chat.py

class ChatController:
    def start_goal(self, objective: str, *, check: str | None = None,
                   max_turns: int | None = None) -> str: ...
    # _run(text, images=None, *, goal: bool = False)
    # finally 里调用 runtime.goal.after_turn(...)
```

- `GoalController` 不 import `ChatController`；它只产出 `GoalDecision`。
- `ChatController` 拥有调度；`Runtime` 拥有 `self.goal = GoalController(...)`。
- `Runtime.session` setter 调 `self.goal.bind_session(session)`。
- `bootstrap.assemble` 在 runtime/session 就绪后给 `runtime.goal` 注入 verifier
  （用现有 `stream_fn` / model / api key）；如果无法构造，goal 仍可设置，但完成判定
  会走 `blocked` 并提示「no verifier available」。
- `GoalController.set` 只写状态；`ChatController.start_goal` 负责起第一轮。

### 5.14 测试计划

- `tests/test_goal_state.py`：参数解析、状态迁移、`to_dict/from_dict`、4000 字符上限、
  替换/终态/resume 语义。
- `tests/test_goal_records.py`：`records.goal` 构建、`KNOWN_KINDS`、`keep_for_rewrite`
  保留/ fork 丢弃、compact/rewrite 后 replay 最后一条。
- `tests/test_goal_verifier.py`：CheckVerifier exit 0/非 0/超时/输出截断；ModelVerifier
  合法 JSON/非法 JSON/重试/失败计数；fake stream；token 计数。
- `tests/test_goal_controller.py`：`after_turn` 的 continue/stop、预算、no-progress、
  verifier failures、pause/resume/clear、session 切换/恢复。
- `tests/test_chat_goal.py`：`_run` 续跑、用户队列优先、abort→paused、error→blocked、
  后台 subagent 延迟、goal message 标记、第一轮启动。
- `tests/test_commands_goal.py`：`/goal` set/status/pause/resume/clear、`--check`/`--budget`
  解析、busy 时排队、错误参数。
- `tests/test_rpc_goal.py`：`status` 含 `goal`、`command` 分发、`to_wire` 兼容。
- `tests/test_tui_goal.py`：GoalHud/sidebar 渲染、空 goal 隐藏、i18n。
- `tests/test_limits.py`：新 knob 的 parse/render/describe/default。
- 回归：现有 `RunStatus`/`to_wire` 测试需允许新字段；旧 session 解析默认 `goal=False`。

### 5.15 分期

一期按依赖顺序一次做完（plan 会拆成可独立验收的任务）：

1. 状态/记录/配置/命令 + session 持久化。
2. `ChatController` turn-end 续跑 + CheckVerifier + 预算/停止。
3. ModelVerifier + TUI GoalHud/sidebar + GUI GoalChip/Panel + RPC status。
4. 回归、i18n、文档、`scripts/check.sh`。

zen/swarm 的 worker goal 化是二期：复用 `GoalController` 和状态机，把 worker 的
「派工/报告」映射成 goal 的 objective/verifier；不在本设计范围内。

---

## 6. 留给 builder 的决定

这些不影响「做什么」，builder 可以自行决定并在 plan 里写明：

- GoalHud 是单独一行还是并入 status band；点击是打开 `/goal` 文本还是详情对话框。
- GUI 的 goal chip 放在 composer 上方还是 header；panel 用现有组件库的哪一种。
- 验证 transcript 的精确截断策略（按消息数还是字符数；工具结果截断到多少）。
- `GoalController` 内部用 `asyncio.Lock` 还是 `_checking` 布尔防重入。
- 测试里 fake check 命令的具体形态（临时脚本 / `python -c`）。
- `GoalView.updated_at` 用 epoch 秒还是毫秒（与现有 `RunStatus` 保持一致即可）。

## 7. 已定稿的行为决策

1. **session 切换**（§5.2）：默认 goal 保持 `active`，切回继续；设置项
   `goal_pause_on_session_switch`（默认 `false`）可让用户改成「离开即 paused」。
   带 `check` 的 goal 在重启/导入时恢复 `paused`，需 `/goal resume` 确认。
2. `/goal <objective>` 设置后立即开始第一轮。
3. `fork` 不继承 goal；undo/retry/compact 保留。
4. `--check` 是权威判定；没有 check 才用模型裁判；check 是 controller-only，不进续跑 prompt。
5. token/wall 是硬预算，watchdog 中断当前 run，不再给 wrap-up；turn 数到顶给一次 wrap-up。
6. token 计数按 provider 归一的 `context_occupancy`，并计入 subagent 和 verifier。
7. verifier 默认用当前 session model 做独立 one-shot；一期不加 `verifier_model` 配置。
8. check 连续 3 次同一失败签名 → `blocked`。
9. `/goal <new>` 直接替换旧 goal（旧记录保留），不要求先 `/goal clear`。
10. 默认预算 40 turns / 4h / 2M tokens；`/goal --budget N` 只改 turn 上限。

以上为设计定稿；进入 `writing-plans` 拆任务。

---

## 8. 参考

- 调研：`docs/reviews/2026-11-16-coding-harness-goal-designs.md`
- Claude Code `/goal`：<https://code.claude.com/docs/en/goal>
- Codex goal 源码：<https://github.com/openai/codex/tree/main/codex-rs/ext/goal>
- Codex `ThreadGoal`：<https://github.com/openai/codex/blob/main/codex-rs/state/src/model/thread_goal.rs>
- Cursor Agent：<https://cursor.com/docs/agent/overview>
- loop engineering 论文：<https://arxiv.org/abs/2607.00038>
- 可移植实现：<https://github.com/secemp9/goal>
