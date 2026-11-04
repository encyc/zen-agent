# 异步并行子代理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `task` 工具获得异步派发（`async: true`，镜像 bash async 范式）与并发执行、per-dispatch `model` 参数、worker 契约层（file manifest + join 终验），并修复后台 child 的两个 UI 缺口。

**Architecture:** 新增进程级 `TaskManager`（镜像 `jobs.JobManager` 的 accessor/abandon 形态，但平行实现——保留式收割 vs cleanup-on-wait 语义不同）。`build_task_tool` 的同步/异步两条路径共享同一个 `_run_child` 协程体：同步直接 await（0.7.8 行为逐字节不变），异步包成 `asyncio.create_task` 注册进 TaskManager 后立即返回任务号；后台 child 在派发时**冻结** mode/live_tools/model 并**钉住**自己的 `SubagentStore` 实例。`wait` 工具按 id 前缀路由（`task_` → TaskManager），信封 details 带 `subagent_cost/tokens` 供 TUI/telemetry 入账。

**Tech Stack:** Python 3.11+，asyncio 单事件循环（无线程），pytest；prompt 改动受 spec.py/goldens/source-gate/budget 四闸门约束；闸门 `uv run pytest` + `uv run python scripts/prompt_goldens.py --check`。

**Spec:** `docs/superpowers/specs/2026-11-04-async-parallel-subagents-design.md`（已批准，含三项用户裁决：会话切换存活 / UI 修复进 v1 / 模式冻结不中止）

## Global Constraints

- **同步路径逐字节不变**：不带 `async: true` 的派发，信封文本、details 键集、watchdog、黑名单、模式跟随行为与 0.7.8 完全一致——Task 2 的回归测试钉住这一点。
- 无线程：一切并发是同一事件循环上的协程；child 之间无共享可变状态。
- 后台 child 派发时冻结 mode/live_tools/model；ESC（abort token）不绑定后台 child；watchdog（`[task].timeout_s`）照常生效。
- TaskManager 进程级、绑运行循环；循环变更 abandon；完成条目保留最近 32 条，重复 `wait` 幂等返回。
- 提示词散文只进块文件（source gate）；prompt 内容变更 bump `PROMPT_VERSION` + `prompts/CHANGELOG.md` 条目 + goldens 重生成。
- 工具描述预算闸门：本计划将 `TOOL_TOTAL_BUDGET_CHARS` 4400→4800（task/wait 两个工具新增真实语义，14 工具的旧总额度不再够用；单项 900 上限不动）——已获批准的裁量点。
- 每个 commit `uv run pytest` 全绿；conventional commits（`feat(task): …`、`fix(tui): …`、`docs: …`）。

## Review Focus

- **转录重复追加**：会话切换（压缩/undo/fork/clear）时 `SubagentStore.bind_session` 清 `_written`，在途 child 的 recorder 会把整段消息重放到已 carry 的文件。钉住 store（Task 2）+ carry 跳过在途（Task 2）后，必须有一个「压缩打断后台 child」的测试（Task 2 Step 5）。
- **后台 child 状态显示**：派发卡瞬间完成后，磁盘 meta 是「有 header 无 end」→ `_read_meta` 报 `interrupted`；tracker 必须以 TaskManager 在途集合优先，否则侧边栏撒谎 20 分钟（Task 5 的测试钉住）。
- **花费双计/漏计**：sync task 结果自带 `subagent_cost`；async 的花费只在 `wait` 信封里。chat.py 钩子若不改工具名过滤会把 wait 的入账漏掉（现只认 `task`）——Task 5 钉住；同时不得双计。
- **模式冻结边界**：async 派发必须在使用 `live_tools()`/`mode` **之前**、于派发调用内同步求值——若在后台协程里才求值，冻结失效（Task 2 的测试：派发后改 mode，收割断言工具面不变）。
- **wait 幂等与存活**：重复 `wait task_N` 返回同一信封；会话切换后 `wait` 仍可达（TaskManager 进程级）。Task 4 钉住。

---

### Task 1: TaskManager 进程级注册表

**Files:**
- Modify: `zenagent/tools/task.py`（新段：`TaskRun`/`TaskManager`/`get_task_manager`/`close_task_manager`，照 `tools/jobs.py:119-433` 的形态）
- Test: `tests/test_task.py`

**Interfaces:**
- Produces: `TaskRun`（dataclass：`task_id, call_id, name, model_id, started_at, status, envelope, details, _done: asyncio.Event`）；`TaskManager.start(call_id, name, model_id, coro_factory) -> TaskRun`；`TaskManager.wait(task_id, timeout_s) -> TaskRun`（未知 id 抛 `UnknownTaskError(task_id, known)`；完成条目保留、幂等返回）；`TaskManager.running_call_ids() -> set[str]`；`TaskManager.ids() -> list[str]`；`get_task_manager() -> TaskManager`（照 `jobs.get_job_manager` 的 loop 绑定 + abandon）；`close_task_manager()`；保留常量 `TASK_RUN_RETENTION = 32`。

- [ ] **Step 1: 写失败测试**：`test_task_manager_start_and_wait_roundtrip`（start 一个立即完成的 fake coro → wait 返回 TaskRun，envelope/details 相符）；`test_task_manager_wait_is_idempotent_after_completion`（wait 两次同一 id，第二次不挂起）；`test_task_manager_unknown_id_raises_with_known_list`；`test_task_manager_running_call_ids_tracks_lifecycle`（运行中集合非空 → 完成后为空）；`test_task_manager_retention_keeps_last_32`。
- [ ] **Step 2: 跑测试确认失败**（`uv run pytest tests/test_task.py -q -k task_manager`）。
- [ ] **Step 3: 实现**：`TaskManager.start` 用 `asyncio.create_task` 包 `coro_factory()`，完成回调写 `status/envelope/details` 并 `._done.set()`；`wait` 未完成时 `await asyncio.wait_for(run._done.wait(), timeout)` 超时返回 running 的 TaskRun（不杀）；retention 用 `collections.deque(maxlen=32)` 存完成条目；abandon 照 `JobManager._abandon`。
- [ ] **Step 4: 测试通过**；**Step 5: commit** `feat(task): process-level TaskManager registry for background children`。

### Task 2: 异步派发路径 + 会话切换存活

**Files:**
- Modify: `zenagent/tools/task.py`（`execute` 拆 `_run_child` 共享协程体；`async` 参数；钉住 store；`carry_task_dir` 加 `skip`）
- Modify: `zenagent/agent/subagents.py`（`carry_task_dir(source, target, skip: set[str] | None = None)`——skip 命中 call_id 的转录文件不复制）
- Modify: `zenagent/app/sessions.py:200,224`、`zenagent/app/chat.py:402,454`（四处调用传 `skip=get_task_manager().running_call_ids()`，函数内延迟 import 防环）
- Test: `tests/test_task.py`

**Interfaces:**
- Consumes: Task 1 的 TaskManager 全部接口。
- Produces: `build_task_tool` schema 新增 `"async": {"type": "boolean", "default": false}`；异步派发返回文本 `task task_N dispatched (NAME); call wait with job_id="task_N" to collect the envelope.`，`details={"task_id","name","dispatched":True}`；`_run_child(*, child, name, call_id, store, started, timeout_s, on_update) -> tuple[str, dict]`（信封文本 + details，同步路径原样复用）。

- [ ] **Step 1: 失败测试**：`test_async_dispatch_returns_immediately_with_task_id`（fake child sleep 0.2s：execute 耗时 <50ms、返回 dispatched 文本）；`test_async_child_completes_in_background_and_writes_transcript_end_line`（sleep 后 TaskManager wait 拿到与同步同构的信封，`SubagentStore.list()` 的 meta.status 非 interrupted）；`test_async_child_survives_session_rebind`（派发后调 `tool.bind_session(new_path)` + `carry_task_dir(old, new, skip=running_call_ids())`，断言新目录无该在途转录、完成后转录落在钉住的旧目录且无重复行）；`test_async_freezes_mode_and_tools_at_dispatch`（dispatch 后改 live mode/tools，收割断言 `tools_seen` 与派发时一致）；`test_sync_path_unchanged`（无 async 参数的既有全部测试照旧通过——回归哨兵）；`test_async_detaches_from_abort_token`（dispatch 后立即 abort 父调用，后台 child 跑完）。
- [ ] **Step 2: 确认失败**。**Step 3: 实现**：`execute` 顶部 `async_flag = bool(args.get("async"))`；把现有「构造 child → watchdog → prompt → 信封」体抽成 `_run_child`；同步路径 `await _run_child(...)` 不变；异步路径在**调用协程内**先求值 `live_tools`/`mode`/`_live(model)`（冻结），用 `session_cell["path"]` 新建钉住 store，不创建 `watch_abort`，`get_task_manager().start(call_id, name, model_id, lambda: _run_child(...))` 后返回派发文本。`build_task_tool` 的 `bind_session` 改为同时更新 `session_cell["path"]`。`carry_task_dir` 按 stem 过滤 skip 集合。
- [ ] **Step 4: 通过**（含全量 `tests/test_task.py` 回归）；**Step 5: commit** `feat(task): async dispatch with pinned stores and session-switch survival`。

### Task 3: file manifest（files_touched）

**Files:**
- Modify: `zenagent/tools/task.py`（`_files_touched(messages) -> list[str]` + 两种路径的 details 增键）
- Test: `tests/test_task.py`

**Interfaces:**
- Produces: details 新键 `files_touched: list[str]`（write_file/edit_file 的 `path` 参数去重保序；bash 写入不可见——spec 已知限制）。

- [ ] **Step 1: 失败测试**：`test_envelope_details_carry_files_touched`（fake child 流带 write_file/edit_file/read_file toolCall → 恰好两个路径、去重）；`test_files_touched_empty_for_readonly_child`。
- [ ] **Step 2: 确认失败**。**Step 3: 实现**：`_run_child` 收尾扫描 `child.messages` 的 AssistantMessage toolCall 块。**Step 4: 通过**；**Step 5: commit** `feat(task): files_touched manifest in task envelopes`。

### Task 4: wait 工具路由 task id

**Files:**
- Modify: `zenagent/tools/wait.py`（`_wait` 按 `task_` 前缀分流；新 `_wait_for_task`）
- Modify: `zenagent/prompts/blocks/tools/wait.md`（描述补一句 task 收割）
- Test: `tests/test_wait.py`、`tests/test_prompts_tools.py`（golden 随 Task 7 重生成）

**Interfaces:**
- Consumes: Task 1 `TaskManager.wait`；UnknownTaskError 文案照 UnknownJobError（known 列表）。
- Produces: wait 返回 task 信封时 `details={"task_id","name","status","subagent_cost","subagent_tokens","files_touched",...}`（供 Task 5 入账）；abort 竞速语义照 `_wait_for_result`（不杀后台 child）。

- [ ] **Step 1: 失败测试**：`test_wait_collects_task_envelope`（async 派发 fake child → wait 拿到 `<task-result` 全文与花费 details）；`test_wait_unknown_task_id_lists_known`；`test_wait_task_idempotent`；`test_wait_task_timeout_reports_running`（超时返回运行中提示，不杀）；`test_wait_task_abort_keeps_child_running`（等待中 abort → wait 返回 aborted 提示，后台 child 照跑、之后仍可收割）；`test_wait_still_handles_bash_jobs`（bg 路径回归）。
- [ ] **Step 2: 确认失败**。**Step 3: 实现**：`job_id` 以 `task_` 开头 → `get_task_manager().wait(...)`（timeout 钳制沿用现有 min/max）；结果文本即 TaskRun.envelope。**Step 4: 通过**；**Step 5: commit** `feat(wait): collect background task envelopes by task id`。

### Task 5: UI 修复（running 显示 + 花费入账）

**Files:**
- Modify: `zenagent/app/subagent_tracker.py`（`views()` 查 TaskManager 在途集合，覆盖磁盘 `interrupted`）
- Modify: `zenagent/app/chat.py:582`（花费钩子工具名 `task` → `("task", "wait")`，details 含 `subagent_cost` 才入账）
- Test: `tests/test_tui_subagents.py`（或现有 tracker 测试文件）、`tests/test_app_chat.py` 对应钩子测试

**Interfaces:**
- Consumes: `get_task_manager().running_call_ids()`；wait details 的花费键。

- [ ] **Step 1: 失败测试**：`test_tracker_shows_running_for_inflight_background_task`（注册表在途 + 磁盘 meta interrupted → SubagentView.status == "running"）；`test_spend_counts_wait_forwarded_cost`（wait 结果 details 带 subagent_cost → tracker.cost 增加）；`test_spend_not_double_counted`（同一 child 的 sync 结果只入账一次）。
- [ ] **Step 2: 确认失败**。**Step 3: 实现**：tracker `views()` 内延迟 import `get_task_manager`；chat.py 钩子改集合判断（loop.py telemetry 无工具名过滤，自动生效，测试断言之）。**Step 4: 通过**；**Step 5: commit** `fix(tui): live status and spend accounting for background subagents`。

### Task 6: per-dispatch model 参数

**Files:**
- Modify: `zenagent/tools/task.py`（schema 加 `"model"`；execute 派发时 `resolve_model`）
- Modify: `zenagent/app/runtime.py`、`zenagent/cli/main.py`（构建点传 `resolve_model`）
- Modify: `zenagent/app/support.py`（新增 `resolve_catalog_model(catalog, raw) -> Model`：裸 id 歧义报错列出 `provider/id`，缺 api key 报错指名环境变量）
- Test: `tests/test_task.py`、`tests/test_app_runtime.py`（wiring）、`tests/test_app_support.py`

**Interfaces:**
- Consumes: `api_key_for(catalog, model)`（`app/support.py:62`）；catalog dict 结构与 `/model` 同源。
- Produces: `resolve_model: Callable[[str], Model] | None = None` kwarg——返回 Model 或 raise `ValueError(msg)`（msg 进 `invalid_arguments` 错误信封）；details.model 报实际 model id。

- [ ] **Step 1: 失败测试**：`test_task_model_arg_resolves_provider_model`（fake resolver 返回 id="second" 的 Model → stream models_seen 断言）；`test_task_model_arg_unknown_lists_hint`；`test_task_model_missing_key_fails_fast`（resolver raise → 错误信封含环境变量名，child 未运行）；`test_runtime_wires_resolve_model`。
- [ ] **Step 2: 确认失败**。**Step 3: 实现**：`resolve_catalog_model` 处理 `provider/id` 与裸 id 两形态；runtime 构建点 `resolve_model=lambda raw: resolve_catalog_model(runtime.catalog, raw)`（headless 同构）。**Step 4: 通过**；**Step 5: commit** `feat(task): per-dispatch model parameter with catalog resolution`。

### Task 7: prompt 契约层 + goldens

**Files:**
- Modify: `zenagent/prompts/blocks/task_child_system.md`（文件范围纪律 + 测试证据 + files 清单；保持 ≤ 新 budget）
- Modify: `zenagent/prompts/blocks/tools/task.md`（async/model 参数 + 并行 worker 文件不相交 + join 后全量测试终验）
- Modify: `zenagent/prompts/blocks/system/tool_policy.md`（委派 bullet 增补同一终验规则）
- Modify: `zenagent/prompts/spec.py`（`task_child_system.budget_chars` 900→1100；`TOOL_TOTAL_BUDGET_CHARS` 4400→4800；`PROMPT_VERSION` → `2026-11-04.1`）
- Modify: `zenagent/prompts/CHANGELOG.md`（条目：动机=spec 三护栏 + 异步语义）
- Modify: `tests/test_prompts_tools.py`（child prompt 断言随内容）
- Test: `tests/test_prompts_budget.py`、`scripts/prompt_goldens.py --check`

- [ ] **Step 1: 改块文件与 spec.py**（child prompt 总长 ≤1100；task.md/wait.md 增量后总额 ≤4800）。
- [ ] **Step 2: 更新 child prompt 测试断言**（scope 纪律句、测试证据句、files 清单句）。
- [ ] **Step 3: `uv run python scripts/prompt_goldens.py` 重生成**；`--check` 过；`uv run pytest tests/test_prompts_budget.py tests/test_prompts_tools.py -q` 过。
- [ ] **Step 4: commit** `feat(prompts): worker contract and async dispatch guidance (2026-11-04.1)`。

### Task 8: 文档 + 全量回归

**Files:**
- Modify: `docs/guide/tools-permissions.md`（task 行补 async/model/manifest；wait 行补 task 收割）
- Modify: `CHANGELOG.md`（Unreleased 条目：Added/Changed/Fixed 分节）
- Test: 全量

- [ ] **Step 1: 两处文档**（中文，含语义与边界：会话切换存活、ESC 不波及、模式冻结 caveat、bash 写入不可见）。
- [ ] **Step 2: 全量 `uv run pytest -q` + `prompt_goldens.py --check`** 双绿。
- [ ] **Step 3: commit** `docs: async parallel subagents in tool guide and changelog`。

## 不做（spec 非目标，重申）

worktree/隔离、文件冲突检测器、后台 kill 界面、跨进程后台任务、并发数上限配置、测试并发隔离。

## 验证

1. 每任务：`uv run pytest tests/test_task.py tests/test_wait.py -q`（或对应域）+ 相关域全绿后 commit。
2. 终验：`uv run pytest -q` 全量 + `uv run python scripts/prompt_goldens.py --check`。
3. 冒烟（真实 provider）：`zen -p "用 task 工具 async 派发两个调研任务（一个用不同 model），继续干点别的，然后 wait 收割"`——核对：立即返回、侧边栏 running、收割信封完整、花费入账、ESC 不影响。
4. 用户侧验收：真实工作流并行派两个 review；对照 0.7.8 基线看阻塞是否消失。
