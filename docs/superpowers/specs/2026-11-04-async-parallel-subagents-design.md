# 异步并行子代理设计

日期：2026-11-04
状态：已批准（2026-11-04，用户裁决三项：会话切换存活 / UI 修复进 v1 / 模式冻结不中止）
范围：`task` 工具的异步派发与并发执行、worker 型子代理（zen 写权限）的契约层、per-dispatch 模型参数。worktree 隔离不在本设计内（明确放弃，见方案节）。

## 意图与动机

0.7.7 的 subagent 会话语料审计（12 次运行 67% 失败）暴露两类结构性问题，0.7.8 修掉了产出与预算层，剩下的执行模型问题在本设计解决：

1. **同步阻塞**（审计 FM5）：一次 review 子代理跑 19–30 分钟，父 agent 整个被挂起，用户只能干等（原话：「我怎么发现这个subagent在当前状态停了十分钟了？」）。
2. **并发不可能**：`task` 是同步工具，父 agent 一次只能派一个、必须当场等——调研和评审天然可并行，现在被迫串行。
3. **worker 缺契约**：0.7.8 已让 child 工具面跟随权限模式（zen 下 child 有 write/edit/bash），但派发契约、产出清单、测试证据还是空白。

用户裁决（本轮 brainstorming 定案）：**共享树、不开 worktree、worker 也开放异步**。安全性靠架构不变量 + 三个护栏（见设计 §4），不靠隔离。

## 目标

- `task` 支持 `async: true`：派发立即返回任务号，child 在后台并发跑；父 agent 自主选择继续干活还是 `wait` 收割（镜像 `bash async` + `wait` + `JobManager` 的既有范式，loop 不改——sequential 工具调用瞬间返回，多个 child 天然并发）。
- **默认同步路径与 0.7.8 行为逐字节一致**（快速调研一发入魂的用法保留）。
- 并发 child（读或写）共享主树；worker 契约三护栏：文件范围纪律、file manifest、join 后父 agent 全量测试终验。
- worker 信封结构化带出：改了哪些文件（从转录提取，不靠 child 自报）、跑了哪些测试及结果（child 自报）。
- per-dispatch `model` 参数：重活不再被父会话的模型绑架。
- 后台任务**进程级寿命**：会话切换（自动压缩/undo/fork/clear）不打断在途 child；ESC 只中止同步 child。
- 两个 UI 缺口修复进 v1：后台 child 在侧边栏显示 running（而非 interrupted）、后台花费经 wait 收割入账。

## 非目标

- worktree / 任何形式的树隔离（用户明确裁决放弃；并行写者的隔离需求以「计划阶段文件不相交」的分工纪律替代）。
- 文件级冲突检测器（manifest 供父 agent 人眼/推理发现重叠，不做自动 diff 引擎）。
- 后台任务的 kill 按钮 / 管理界面（v1 后台 child 跑完预算自然结束；收割即 `wait`）。
- 跨进程 / 跨会话的后台任务（单进程事件循环内，进程退出即尽力取消，转录已在盘上）。
- 并发数上限治理（`[task].max_concurrent` 之类——三个并发 fake 测试通过即够用，真有需要再加配置）。

## 反目标

- 不让派发变复杂：`async` 是纯 opt-in，不用它的模型看到的工具面几乎不变。
- 不做假并发：child 是协程不是线程；child 之间无共享可变状态（各自 Agent/ContextGuard/转录），唯一的共享面就是文件树，由契约覆盖。
- 语义不分裂：read 子代理与 worker 用同一套异步机制、同一个 `wait` 收割。

## 已考虑的方案

**方案 A：worktree 并行写者**——每个 worker 独立 worktree，父 agent 审后合并。
优点：真隔离、逐 worker 丢弃。缺点：worktree 生命周期/脏树快照/合并裁决三套机制，工程量最大；且与「计划阶段任务解耦」的既有分工纪律重复收费。**否决**（用户裁决）。

**方案 B：全异步化**——`task` 永远立即返回，收割一律显式 `wait`。
优点：单一语义。缺点：3 秒的快速调研也要两回合；与 0.7.8 刚发布的信封直返行为断层；模型需要忘掉旧习惯。**否决**。

**方案 C（选定）：per-call `async` 参数**——镜像 `bash` 工具的 async 惯例：默认同步阻塞拿信封，`async: true` 立即返回任务号。父 agent 按任务形态自选；提示语进派发清单（下一步依赖结果→同步；要并行/趁等待干活→异步）。

## 设计

### 1. TaskManager（`tools/task.py`，镜像 `tools/jobs.JobManager` 的进程级形态）

- **进程级单例**（照 `get_job_manager` 的 accessor 模式，绑当前事件循环）：注册表 task id → `{asyncio.Task, name, model, started_at, status, envelope, store}`；id 用 `task_N`（与 bash 作业的 `bg_N` 不冲突）。
- **派发时钉住存储**：每个后台 child 在派发时持有自己的 `SubagentStore` 实例引用（不穿透可重绑的会话级 store）——`bind_session` 清空 `_written` 计数器导致的「转录整段重复追加」隐患从根上消除。
- 异步路径：`execute` 把「构造 child → `child.prompt()` → 记录信封」整段包成协程 `asyncio.create_task` 派出去，立即返回轻量结果：任务号、名字、一行「用 wait 收割」提示。
- 完成回调：信封写进注册表 + 钉住的 store `finish`（转录由 `_TaskRecorder` 照常写，与同步路径同一条代码）。
- **会话切换存活**（用户裁决）：自动压缩、`/undo`、`/fork`、`/clear` 一律不打断在途后台 child；`carry_task_dir` 跳过在途 call_id（TaskManager 暴露 running 集合），完成的转录落在钉住的目录。
- 循环变更时 abandon（照 `JobManager`）；完成条目保留最近 N 条（常量，暂不进 config）；重复 `wait` 同一条目幂等返回信封。
- TaskManager 与 JobManager **平行实现**（寿命/取消语义不同，抽象收益存疑——builder check 后定案）。

### 2. `task` 工具参数

- `async: boolean`（默认 false）。同步路径行为与 0.7.8 完全一致（watchdog、黑名单、模式跟随、live tools 全部不动）。
- `model: string`（可选）：在 runtime catalog（含 config.toml 自定义 provider，与 `/model` 同一解析集）按 id 解析。接受裸 model id（多 provider 撞 id 且无默认 provider 可判时报错并列出 `provider/id` 限定写法）与 `provider/id` 限定形式。child 的 api_key/thinking 跟随所选 model；解析失败与缺 api key env 都是 fail-fast 错误信封（指名环境变量）。details.model 报告实际使用的 model。

### 3. `wait` 工具扩展

- 接受 `task_N`（显式 id，无 id 时不列任务——派发提示已指路）；阻塞至完成、返回完整信封；timeout 语义沿用 `JobManager.wait`。
- **收割转发花费**：wait 返回的 details 带 `subagent_cost`/`subagent_tokens`，TUI 统计与 telemetry 钩子（`chat.py`、`loop.py` 的 tool 结果监听）同时识别 `wait` 结果——后台 child 的花费从收割时刻入账（用户裁决：进 v1）。
- schema 形态留给 plan 细化。

### 4. worker 契约（prompt 层 + 结构化提取）

- **child prompt**（`task_child_system.md` 增补）：只碰任务声明范围内的文件；动过代码必须跑相关测试；最终报告列出测试结果。
- **file manifest 结构化提取**：信封 details 增加 `files_touched`，从 child 转录的 write/edit 工具调用参数提取路径（不靠 child 自报，防谎报；bash 侧写入不可见——已知限制）。实现点（recorder 钩子 vs 收割时回放转录）留给 plan。
- **派发清单**（父侧 `tools/task.md` / `tool_policy.md` 增补）：并行 worker 的任务必须文件不相交；收割完全部 worker 后，父 agent 重新跑全量测试再继续——单个 worker 的「绿」在并发下不作数（被后续修改作废的风险由终验兜住）。

### 5. 生命周期与中止

- 后台 child：**进程级寿命**。父回合结束、会话切换（压缩/undo/fork/clear）都照跑；`wait` 可跨回合、跨会话收割（转录持久，不丢）。
- 派发时的 abort token **不绑定**后台 child（detach）——ESC 只影响同步 child 与父循环。
- **模式冻结**（用户裁决）：工具面与权限模式在派发时求值并冻结；此后用户降级（zen → plan/ask）**不中止**在途 worker。已知暴露窗口 = `[task].timeout_s`（默认 ≤900s），写入文档 caveat。
- 0.7.8 的 watchdog 对后台 child 照常生效；失控后台任务最多跑满预算。
- 进程退出：尽力取消（同 bash 作业语义）；信封/转录已落盘。

### 6. UI 修复（用户裁决：都进 v1）

- **running 状态显示**：后台派发后工具卡瞬间完成，侧边栏从磁盘 meta 读到「有 header 无 end 行」会显示 `interrupted`——修复为：TaskManager 注册表中的在途任务显示 `running`（tracker 读取注册表或经 observer 状态流）。
- **花费入账**：见 §3 收割转发。

### 7. 并发正确性边界（写进代码注释与文档）

- child 之间无共享可变状态：各自 `Agent`、`ContextGuard`、转录文件（`SubagentStore` 按 call_id 分文件、派发时各自持实例——并发安全）。
- 唯一共享面是文件树：读并发自由；写并发靠「文件不相交」契约 + manifest + 终验。子代理并发跑测试可能互相干扰（端口/临时目录）——已知限制，靠终验兜底，不在 v1 做测试隔离。

## 留给 plan 的决定

- `wait` 收割 task 的 schema 形态（单参数多态 vs 子命令式）。
- `files_touched` 的提取实现点（recorder 钩子 vs 收割时回放转录）。
- tracker 读 TaskManager 在途状态的具体通路（直接读注册表 vs observer 增量）。
- `task_N` 编号器归属（TaskManager 模块级，照 JobManager 的 `_counter`）。

## 测试策略（概要）

- 并发 3 个 fake child：派发立即返回、完成顺序无关收割、`wait` 阻塞与超时。
- 后台 child 完成后转录落盘、observer 事件到 UI、ESC 不波及。
- `model` 参数：解析、歧义报错、缺 key 错误信封、details.model 正确。
- `files_touched` 提取正确性（含 write/edit 混合、失败 run）。
- worker prompt 契约断言、goldens 重生成、全量回归。
