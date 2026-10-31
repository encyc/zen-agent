# TUI 工作面板实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 TUI 侧栏做成工作面板（Todo / Subagent / Jobs），todo 单家不变式 + 只读详情浮层，后台/subagent 可见；并让子代理转录落盘、可点击进只读视图、`--resume` 后仍可回看。

**Architecture:** 数据经 `zenagent.app` 快照（`RunStatus`）进 TUI，唯一重绘入口是 `ZenApp._refresh_status`；持久化在 `zenagent/agent/subagents.py`（UI 无关，task 工具写、controller 读）；子代理事件用一个模块级 observer（仿 `set_ask_handler`）进 app 层缓冲。

**Tech Stack:** Python 3.11+、Textual >=8.2,<9、rich、pytest、pytest-asyncio；无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-31-tui-work-panel-design.md`

## Global Constraints

- 基线 v0.4.2；`zenagent/app/**` 不得 import `textual` / `prompt_toolkit` / `rich`。
- 侧栏固定宽 26；窄屏阈值 `NARROW_WIDTH = 100`；`#sidebar` 的 CSS 保持不变。
- 单列 dock 不变式不变：`#transcript` (1fr) → `#status-band` (1 行) → `#prompt-row`。
- todo 任何时刻只有一个可见面（侧栏可见 → 侧栏；否则 dock HUD）。
- 子转录格式 = 首行 `meta` + `Message.to_json()` 行 + 末行 `end`；新文件 0600、目录 0700。
- 写盘是 best-effort，任何 IO 失败不得冒泡进 agent 循环。
- 每个任务结束时 `bash scripts/check.sh` 必须绿；不新增网络/依赖。

## Review Focus

按「用户最可能撞上、猜错就难受」排序：

1. **80 列终端按 `ctrl+b`**：侧栏必须保持隐藏、todo 留在 HUD、输入区完好 —— 不能出现空白窄栏或 todo 消失。（T4）
2. **`/undo` 或 `/fork` 后点开旧 subagent**：不能凭空出现已截断任务的条目（store 文件还在，transcript 已无卡片）。（T11）
3. **子代理写盘失败/目录不可写**：查看器显示"无转录"，但 task 工具照常返回 envelope，主流程不报错。（T9/T11）
4. **真实 `--resume` 后点开 subagent**：内容与当场看到的一致；缺 `end` 行的中断任务也能读已完成部分。（T11/T13）
5. **查看子转录时按 `esc`**：返回父转录且不丢内容、不触发 `interrupt`；查看中父转录仍在增长。（T12）

## 文件结构

```
zenagent/app/views.py                  JobView / SubagentView / RunStatus 字段 / ToolCallView.started_at / TranscriptBuilder
zenagent/app/chat.py                   status 组装、subagent 事件缓冲、entries 合并
zenagent/app/runtime.py                Runtime.session setter 绑定 task 工具 sidecar
zenagent/app/sessions.py               fork/undo 的 carry、delete 的 trash
zenagent/agent/subagents.py            新增：SubagentStore + task_dir/carry/trash
zenagent/tools/task.py                 TaskTool.bind_session、observer、进度转发、落盘
zenagent/cli/tui/app.py                _sync_chrome、WorkStrip、查看模式、计时器、palette
zenagent/cli/tui/widgets/sidebar.py    重写为分区 widget
zenagent/cli/tui/widgets/work_strip.py 新增：dock 提示条
zenagent/cli/tui/widgets/subagent_banner.py 新增：查看模式横幅
zenagent/cli/tui/screens/todo_list.py  新增：只读 todo 浮层
zenagent/cli/tui/theme.tcss             新 id 的样式
```

---

## Slice 1 — 侧栏底座

### Task 1: 视图模型与 RunStatus 字段

**Files:**
- Modify: `zenagent/app/views.py`
- Test: `tests/test_app_views.py`

**Interfaces:**
- Produces: `JobView(id, command, status, started_at, duration_ms, exit_code)`（frozen dataclass）、`SubagentView(call_id, name, status, duration_ms, cost, tokens, has_transcript)`（frozen）、`RunStatus.jobs: list[JobView]`、`RunStatus.subagents: list[SubagentView]`、`RunStatus.active_subagent: str | None`、`ToolCallView.started_at: float | None = None`、`job_views(jobs) -> list[JobView]`、`sort_jobs(views) -> list[JobView]`。

- [ ] **Step 1: 写失败测试** —— `tests/test_app_views.py`：`RunStatus()` 三个新字段为空；`job_views([fake_job])` 映射字段；`sort_jobs` 把运行中排前、其余按 `started_at` 倒序；`ToolCallView(...).started_at is None`。
- [ ] **Step 2: 跑测试确认失败** —— `uv run pytest tests/test_app_views.py -q`，期望 `ImportError`/`TypeError`。
- [ ] **Step 3: 实现** —— 在 `views.py` 加两个 dataclass + 两个纯函数（`job_views` 用 duck typing 读 `id/command/status/started_at/duration_ms/exit_code`），`RunStatus` 加三字段（保持 `@dataclass`），`ToolCallView` 加 `started_at`；更新 `__all__`。
- [ ] **Step 4: 跑测试确认通过** —— 同上。
- [ ] **Step 5: 提交** —— `git commit -m "feat(app): job/subagent view models on RunStatus"`

### Task 2: ChatController 组装 jobs / subagents

**Files:**
- Modify: `zenagent/app/chat.py`
- Test: `tests/test_app_chat.py`

**Interfaces:**
- Consumes: `job_views`、`sort_jobs`（T1）。
- Produces: `ChatController.status` 填 `jobs`（`get_job_manager().list()`，`RuntimeError` → `[]`）、`subagents`（来自 `_cards` 里 `name == "task"` 的卡片：`call_id`、`name=card.args.get("name") or "task"`、`status` 由卡片状态/details 推出、`duration_ms/cost/tokens` 取 `card.details`、`has_transcript=False`）、`active_subagent`（第一个 running 的 task 卡片 id）；`_on_event` 的 `tool_execution_start` 给卡片写 `started_at = time.monotonic()`。

- [ ] **Step 1: 写失败测试** —— fake runtime 里塞两张 task 卡片（一张 running、一张 ok，ok 的 `details` 带 `duration_ms/cost/tokens`）→ `chat.status.subagents` 顺序与字段正确；`monkeypatch.setattr("zenagent.app.chat.get_job_manager", ...)` 返回假 manager → `status.jobs` 正确；无 loop 时 `status.jobs == []`。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— `chat.py` 顶部 import `get_job_manager`、`job_views/sort_jobs`；`status` 里组装；`_on_event` 写 `started_at`；`reload_transcript`/`clear` 重置相关缓存。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 3: 侧栏分区重写

**Files:**
- Modify: `zenagent/cli/tui/widgets/sidebar.py`、`zenagent/cli/tui/app.py`（`_refresh_status` 调用 `refresh_contents`）、`zenagent/cli/tui/theme.tcss`
- Test: 新增 `tests/test_tui_sidebar.py`

**Interfaces:**
- Produces: `NARROW_WIDTH`（保持 100）、`SidebarRow(Static)`（`key`、`text`、`clickable`、`on_click` 回调）、`SidebarSection(Vertical)`（标题 + keyed 行，`set_rows(rows: list[SidebarRow])`）、`Sidebar(VerticalScroll)`（`refresh_contents(chat, models, sessions)`；`apply_width(width) -> bool` 只返回是否可见，不自己改 display）。
- 分区：Session / Model / Runtime / Todo / Subagent / Jobs；Todo 区整体可点（回调由 app 注入）；Subagent 行按 `call_id` keyed；Jobs 行按 `job.id` keyed；Jobs 空/Subagent 空时整区隐藏。

- [ ] **Step 1: 写失败测试** —— 120x40 开 app → `ctrl+b` → 六个 section 标题都在；无 todo 时 Todo 区隐藏；塞两张 task 卡片后 Subagent 区出现两行且 key 正确；再刷新只改文本不重建 widget（比较行实例 id）。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— 重写 `sidebar.py`（保留 `#sidebar` id 与 CSS）；`app.py` 在 `_refresh_status` 里调 `sidebar.refresh_contents(...)`；行高 1，文本按 22 列截断。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 4: chrome 统一决策 + todo 单家 + WorkStrip

**Files:**
- Modify: `zenagent/cli/tui/app.py`、`zenagent/cli/tui/theme.tcss`
- Create: `zenagent/cli/tui/widgets/work_strip.py`
- Test: `tests/test_tui_layout.py`、`tests/test_tui_sidebar.py`

**Interfaces:**
- Produces: `ZenApp._sidebar_open: bool`、`ZenApp._sync_chrome() -> None`（唯一决策点）、`WorkStrip.set_work(jobs_running: int, task: str | None, *, sidebar_hidden: bool)`、WorkStrip `on_click` → 打开侧栏。
- 规则：`visible = _sidebar_open and width >= NARROW_WIDTH`；`sidebar.display = visible`；`TodoHud.display = (not visible) and bool(todos)`；WorkStrip 显示条件 `not visible and (jobs_running > 0 or task)`。

- [ ] **Step 1: 写失败测试** —— 120 列：`ctrl+b` 后 `TodoHud.display is False`、关掉后 `True`；90 列：`ctrl+b` 后侧栏仍 `display is False` 且 todo 在；WorkStrip 在"有 job + 侧栏关"时出现、点击后侧栏打开且条消失；无活时不出现。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— 加 `_sidebar_open`、`_sync_chrome`，在 `_refresh_status`/`resize`/`ctrl+b` 调；`WorkStrip` 放 `TodoHud` 之后；`on_resize` 调 `_sync_chrome`。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 5: TodoListScreen（只读浮层）

**Files:**
- Create: `zenagent/cli/tui/screens/todo_list.py`
- Modify: `zenagent/cli/tui/app.py`（palette 项、HUD 点击、侧栏 Todo 区点击）
- Test: `tests/test_tui_todo.py`

**Interfaces:**
- Produces: `TodoListScreen(DialogScreen[None])`，构造参数 `phases: list[dict]`（`RunStatus.todos` 形状，用 `zenagent.tools.todo.summarize_todo`/phase 结构渲染）；`esc` -> `dismiss(None)`；`ZenApp.action_show_todo_list()`。
- 渲染：phase 分组；状态符号 `✓ done / ▶ in progress / ○ pending / ⊘ blocked`；条目全文换行；超高内部滚动（`max-height: 70%`）。

- [ ] **Step 1: 写失败测试** —— 有 todo 时点 HUD/侧栏 Todo 区 → `TodoListScreen` 压栈，文本含某条目全文与状态符号；`esc` 后关闭、焦点回输入；palette 里能搜到 "Show todo list"。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 6: 1 秒工作轮询

**Files:**
- Modify: `zenagent/cli/tui/app.py`
- Test: `tests/test_tui_sidebar.py`

**Interfaces:**
- Produces: `ZenApp._work_timer`（`set_interval(1.0, ...)`）；仅当 `status.jobs` 有 running 或 `active_subagent` 时运行，空闲 `stop()`；回调走现有 `_schedule_refresh()`。

- [ ] **Step 1: 写失败测试** —— 无活时 `_work_timer is None`；注入运行中 job → 轮询开启；清除后关闭；轮询一轮后 job 状态从 `running` 变 `ok` 反映到侧栏行。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— `_refresh_status` 末尾调 `_sync_work_timer()`。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

---

## Slice 2 — 子转录

### Task 7: TranscriptBuilder 纯重构

**Files:**
- Modify: `zenagent/app/views.py`、`zenagent/app/chat.py`
- Test: `tests/test_app_views.py`、`tests/test_app_chat.py`（既有，全绿即回归）

**Interfaces:**
- Produces: `TranscriptBuilder`：`views: list[MessageView | ToolCallView]`、`cards: dict[str, ToolCallView]`、`current: MessageView | None`、`load(messages) -> None`、`apply(event: AgentEvent) -> bool`。
- `ChatController` 内部改持 `self._builder`；`transcript` / `_cards` 对外语义不变（父路径行为逐字节不变）。

- [ ] **Step 1: 记录现状** —— `uv run pytest tests/test_app_chat.py tests/test_tui_chat.py -q` 全绿。
- [ ] **Step 2: 写等价的 builder 单测** —— 用现有 chat 事件序列构造 builder，断言 `views/cards` 与 `ChatController._transcript/_cards` 一致（message delta、tool start/update/end、thinking）。
- [ ] **Step 3: 重构** —— 从 `_on_event` 抽出视图构造到 `TranscriptBuilder.apply`，controller 改为委托；不夹带新功能。
- [ ] **Step 4: 跑全量相关测试** —— `uv run pytest tests/test_app_chat.py tests/test_app_views.py tests/test_tui_chat.py -q`；再 `bash scripts/check.sh`。
- [ ] **Step 5: 提交**（单独一个 refactor 提交）

### Task 8: SubagentStore（持久化）

**Files:**
- Create: `zenagent/agent/subagents.py`
- Test: 新增 `tests/test_subagents_store.py`

**Interfaces:**
- Produces: `TASKS_DIRNAME`、`MAX_TASK_FILES=32`、`MAX_TASK_TOTAL_BYTES=8*1024*1024`、`MAX_TASK_FILE_BYTES=2*1024*1024`、`task_dir(session_path) -> Path`、`carry_task_dir(old_session, new_session) -> None`、`trash_task_dir(session_path, trash_dir) -> Path | None`、`SubagentMeta`（frozen：`call_id, name, model, started_at, status, duration_ms, cost, tokens, truncated`）、`SubagentStore(session_path: Path | None)`：`bind_session`、`begin`、`append`、`finish`、`list`、`load`、`prune`。
- 文件格式见 spec §5.2（`meta` 首行 / `Message.to_json()` / `end` 末行；缺 `end` = interrupted；坏行跳过）。

- [ ] **Step 1: 写失败测试** —— begin/append/finish 往返 + `load` 得到的 messages 与写入一致；缺 end → `interrupted`；坏行跳过且 `truncated`；超单文件上限写 `truncated` 后停止追加；33 个文件触发 prune 删最旧；`carry_task_dir` 复制目录；`trash_task_dir` 挪进 `.trash/<stem>.tasks`；`session_path=None` 时全 memory-only 不落盘。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— 用 `tempfile`/`os.replace` 原子写首行，追加用 `os.open(..., O_APPEND)`；目录 `mkdir(mode=0o700)`，文件 `0o600`；任何 `OSError` 静默降级。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 9: task 工具观察者与落盘

**Files:**
- Modify: `zenagent/tools/task.py`、`zenagent/app/runtime.py`、`zenagent/cli/main.py`、`zenagent/cli/repl.py`、`zenagent/cli/rpc.py`
- Test: `tests/test_task.py`

**Interfaces:**
- Produces: `SubagentObserver = Callable[[str, str, dict], None]`、`set_subagent_observer(observer)`、`get_subagent_observer()`、`TaskTool(AgentTool)`（含 `bind_session: Callable[[Path | None], None]`）、`build_task_tool(..., session_path: Path | None = None) -> TaskTool`。
- 行为：child 第二 listener 做进度转发（`on_update`，节流 ≥100ms，`turn N · <tool> <preview>`）、`store.append`、`observer(call_id, "event", ...)`；start/end 各广播一次；end 一律 `finish`（ok/error/aborted）；四个 build 调用点传各自 `session_path`。

- [ ] **Step 1: 写失败测试** —— 起一个 fake stream 的 task 调用：observer 收到 start/event/end；`on_update` 收到至少一条进度；结束后 `<sessions>/tasks/<stem>/<call_id>.jsonl` 存在且 `load` 出消息；`bind_session(None)` 后不再落盘但 observer 仍有事件；写盘抛 `OSError` 时 envelope 正常。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— `TaskTool` dataclass + `_TaskRecorder`（store + 节流）内部类；`execute` 里接线；四个调用点补 `session_path`。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 10: 会话生命周期接线

**Files:**
- Modify: `zenagent/app/runtime.py`（`session` setter）、`zenagent/app/sessions.py`（`fork`/`undo`/`delete`）、`zenagent/app/chat.py`（`retry`/`compact_now`）
- Test: `tests/test_app_sessions.py`、`tests/test_app_chat.py`

**Interfaces:**
- `Runtime.session` setter 追加 `self.task_tool.bind_session(session.path)`（与 artifact sink / todo 同一 choke point）。
- `carry_task_dir(old, new)` 调用点：`SessionManager.fork()`、`SessionManager.undo()`、`ChatController.retry()`、`ChatController.compact_now()`。
- `SessionManager.delete()`：把 `tasks/<stem>/` 挪到 `.trash/<stem>.tasks/`（best-effort）。

- [ ] **Step 1: 写失败测试** —— 会话切换后 `task_tool` 的写入路径变到新 stem；`/fork` 后新会话能看到旧转录文件（复制到位）；`/undo` 同理；`delete` 后旧目录进 `.trash/`。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 11: controller 子代理缓冲与 resume 重建

**Files:**
- Modify: `zenagent/app/chat.py`
- Test: `tests/test_app_chat.py`

**Interfaces:**
- Produces: `ChatController.on_subagent_event(call_id, kind, payload)`；`ChatController.subagent_transcript(call_id) -> list[MessageView | ToolCallView] | None`（内存 builder 优先，否则 `SubagentStore(当前 session).load()` → `messages_to_views`）；`status.subagents` 改为内存条目 + `store.list()` 合并、**只保留当前 transcript 里 task 卡片的 call_id**、`has_transcript` 真实判断；`_subagent_entries` 缓存（task end / `reload_transcript` / `clear` 失效）。

- [ ] **Step 1: 写失败测试** —— `on_subagent_event` 三段后 `subagent_transcript` 有视图；新 controller（模拟 resume）在 tmp session 目录有文件时能读盘得到内容；transcript 里没有卡片的孤儿文件不出现在 `status.subagents`；`/undo` 截断后条目消失。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** —— 子 buffer（每个 call_id 一个 `TranscriptBuilder`）；`has_transcript` 判断；缓存。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 12: TUI 子转录查看模式

**Files:**
- Modify: `zenagent/cli/tui/app.py`、`zenagent/cli/tui/theme.tcss`、`zenagent/cli/tui/widgets/sidebar.py`（行点击回调）
- Create: `zenagent/cli/tui/widgets/subagent_banner.py`
- Test: 新增 `tests/test_tui_subagent_view.py`

**Interfaces:**
- `#transcript-stack { height: 1fr }` 包 `SubagentBanner` + 父 `TranscriptView`（保留 `#transcript` id）+ 子 `TranscriptView`（id `subagent-transcript`，默认隐藏）。
- `ZenApp._viewing: str | None`、`enter_subagent_view(call_id)`、`_exit_subagent_view()`；`action_interrupt` 优先退出查看；`PromptInput.disabled` 开关；`on_mount` 注册 observer、`on_unmount` 清空。

- [ ] **Step 1: 写失败测试** —— 点击侧栏 Subagent 行 → 子 view 显示、父 view 隐藏、banner 文案含名字与"只读"、`PromptInput.disabled is True`；`esc` 返回、父内容仍在、输入可用；查看中再喂一条父事件，返回后能看到；`has_transcript=False` 的行点击无反应。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

### Task 13: resume 集成测试 + 收尾文档

**Files:**
- Create/Modify: `tests/test_tui_subagent_resume.py`、`docs/feature-map.md`、`README.md`（侧栏/子转录一句）
- Test: 本任务新增的集成测试 + 全量 `bash scripts/check.sh`

**Interfaces:**
- 集成：tmp session dir + fake stream 跑一轮 `task` → 关闭 app → 用同一 runtime/session 重建 app → 侧栏出现历史条目 → 点击进入看到内容；缺 `end` 的文件夹显示 interrupted 且可读。

- [ ] **Step 1: 写失败集成测试**
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 修到通过**（补必要的 wiring）
- [ ] **Step 4: 更新 `docs/feature-map.md` 与 README 的一行描述**
- [ ] **Step 5: `bash scripts/check.sh` 全绿 + 提交**
