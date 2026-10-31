# TUI 工作面板打磨实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec 完成五项打磨：语义色板、侧栏拖拽调宽、todo 全量内联、job 日志浮层、ctx 构成明细（含数据通路）。

**Architecture:** 全部在现有分层内：估算与报告在 app/agent 层（`app/context.py`、`prompts`、`providers/limits.py`），TUI 只渲染 `RunStatus`；拖拽/点击是 widget 行为，宽度与 chrome 决策仍集中在 `ZenApp._sync_chrome`。

**Tech Stack:** Python 3.11+、Textual 8.2、rich、pytest + Textual `run_test()`；无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-31-tui-panel-polish-design.md`

## Global Constraints

- `zenagent/app/**` 不得 import `textual` / `prompt_toolkit` / `rich`。
- 窄屏阈值不变：`NARROW_WIDTH = 100`；`#sidebar` 默认宽 26，可拖 20–60，主列保底 30。
- 所有估算数值显示为 `≈`；字符数是精确值。
- 子侧栏/浮层样式统一走 `zenagent/cli/tui/palette.py`，不再散落 hex。
- 不新增依赖；每个任务结束 `bash scripts/check.sh` 必须绿。

## Review Focus

1. **90 列 + ctrl+b**：把手不出现、侧栏仍隐藏、todo 留在 dock HUD。（T2）
2. **拖到最宽/最窄**：宽 20–60、主列 ≥30，输入行仍贴底、转录不溢出。（T2）
3. **首轮响应前的 ctx**：没有 `last_usage` 时只用估算，无除零/NaN，侧栏显示 `≈`。（T5/T6）
4. **job 被 `wait` 取走后点旧行**：`snapshot` 为 None 时只 toast，不崩。（T4）
5. **30 个任务的 todo**：侧栏行 key 唯一（phase/task 索引）、可滚动、点击任意行开浮层。（T3）

## 文件结构

```
zenagent/cli/tui/palette.py                  新增：语义色常量 + 分区/状态映射
zenagent/cli/tui/widgets/sidebar_resize.py   新增：1 列拖拽把手
zenagent/cli/tui/widgets/sidebar.py          Context/Todo/Jobs 区扩展、行点击
zenagent/cli/tui/screens/job_log.py          新增：只读日志浮层
zenagent/cli/tui/screens/context_breakdown.py 新增：ctx 明细浮层
zenagent/cli/tui/app.py                      把手、chrome、show_* 入口、刷新联动
zenagent/cli/tui/theme.tcss                  把手样式、token 注释
zenagent/cli/tui/{todo_hud.py,work_strip.py,subagent_banner.py,screens/todo_list.py}  换 palette
zenagent/app/context.py                      新增：ContextReport 纯函数
zenagent/app/{views.py,chat.py,runtime.py}   报告保留、RunStatus.context、job_log()
zenagent/providers/limits.py                  公开 estimate_text_tokens
```

---

### Task 1: 语义色板

**Files:**
- Create: `zenagent/cli/tui/palette.py`
- Modify: `zenagent/cli/tui/widgets/{sidebar,todo_hud,work_strip,subagent_banner}.py`、`zenagent/cli/tui/screens/todo_list.py`、`zenagent/cli/tui/theme.tcss`（注释）
- Test: 新增 `tests/test_tui_palette.py`

**Interfaces:**
- Produces: `INK SURFACE LINE TEXT MUTED DIM ACCENT OK ERROR`（hex str）、`SECTION_ACCENTS: dict[str, str]`（Session/Model/Runtime/Todo/Subagent/Jobs 各一色）、`STATE_COLORS: dict[str, str]`（`running/ok/error/failed/timed_out/done/pending/in_progress/blocked` → hex）。
- 现有 widget 删除本地 `_TEXT/_MUTED/_ACCENT/...`，改为 `from zenagent.cli.tui.palette import ...`；分区标题用 `SECTION_ACCENTS[title]`。

- [ ] **Step 1: 写失败测试** —— 常量均为 `#rrggbb`；`SECTION_ACCENTS` 键正好六个；TUI 冒烟：开侧栏后每个 `SidebarSection` 的 title `Static` 的 `styles.color` 等于对应 accent。
- [ ] **Step 2: 跑测试确认失败**（`ImportError`/颜色不等于）
- [ ] **Step 3: 实现** palette 模块 + widget 换常量（语义不变，仅集中）
- [ ] **Step 4: 跑测试通过 + `bash scripts/check.sh`**
- [ ] **Step 5: 提交** `feat(tui): semantic palette module`

### Task 2: 拖拽调宽

**Files:**
- Create: `zenagent/cli/tui/widgets/sidebar_resize.py`
- Modify: `zenagent/cli/tui/app.py`、`zenagent/cli/tui/theme.tcss`
- Test: 新增 `tests/test_tui_sidebar_resize.py`

**Interfaces:**
- `SidebarResizeHandle(Static)`：`on_mouse_down` → `capture_mouse()` + `app.begin_sidebar_resize()`；`on_mouse_move` → `app.resize_sidebar(event.screen_x)`；`on_mouse_up` → `release_mouse()`。
- `ZenApp.DEFAULT_SIDEBAR_WIDTH = 26`、`MIN_SIDEBAR_WIDTH = 20`、`MAX_SIDEBAR_WIDTH = 60`、`MAIN_COLUMN_MIN = 30`；`begin_sidebar_resize()`、`resize_sidebar(screen_x: int)`、`end_sidebar_resize()`；`_sync_chrome` 里设 `sidebar.styles.width = self._sidebar_width` 与把手 display。

- [ ] **Step 1: 写失败测试** —— `app.resize_sidebar(width-10)` → 26 左右；极端 x 钳到 20/60 且主列 ≥30；120 列开侧栏把手 display True、90 列 False；pilot `mouse_down(handle)` 后 `app.mouse_captured is handle`，`mouse_up` 后释放。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** 把手 widget + compose/#root 顺序 + chrome 应用宽度
- [ ] **Step 4: 跑测试通过 + 全量闸门**
- [ ] **Step 5: 提交** `feat(tui): drag-resizable sidebar`

### Task 3: Todo 全量内联

**Files:**
- Modify: `zenagent/cli/tui/widgets/sidebar.py`（Todo 区）
- Test: `tests/test_tui_sidebar.py`

**Interfaces:**
- Todo 区行：每个 phase 一条 `phase:{i}`（`name closed/total`，`SECTION_ACCENTS["Todo"]` 暗色），每个任务一条 `task:{i}:{j}`（`✓/▶/○/⊘ + _clip(content)`，状态色来自 `STATE_COLORS`）；无 todo 时整区隐藏。所有行点击沿用现有 `on_row_click` → app 的 `action_show_todo_list`。

- [ ] **Step 1: 写失败测试** —— 两 phase（3 任务）→ 侧栏 `phase:0/phase:1/task:0:0…` 共 5 行且文本含 `✓`/`▶`；点任务行 → `TodoListScreen` 压栈；HUD 不变。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 通过 + 闸门**
- [ ] **Step 5: 提交** `feat(tui): inline todo list in the sidebar`

### Task 4: Job 日志浮层

**Files:**
- Create: `zenagent/cli/tui/screens/job_log.py`
- Modify: `zenagent/app/views.py`（`JobLogView`）、`zenagent/app/chat.py`（`job_log()`）、`zenagent/cli/tui/widgets/sidebar.py`（jobs 行 click）、`zenagent/cli/tui/app.py`
- Test: 新增 `tests/test_tui_job_log.py`

**Interfaces:**
- `JobLogView(id: str, command: str, status: str, duration_ms: int | None, exit_code: int | None, output: str, truncated: bool)`（views.py）。
- `ChatController.job_log(job_id) -> JobLogView | None`：`get_job_manager().snapshot(id)`（RuntimeError → None）。
- `JobLogScreen(DialogScreen[None])`：`set_view(view)`，头行 `bg_1 · running · 4s`，命令行、`VerticalScroll` 输出（`truncated` 提示）、`esc` 关闭。
- `Sidebar.set_handlers(on_job=...)`；jobs 行 clickable → `app.show_job_log(job.id)`；app 保存 `_job_log_screen`，`_refresh_status` 时若在栈上且是 running 则 `set_view(chat.job_log(id))`。

- [ ] **Step 1: 写失败测试** —— 假 manager 返回带 `output/truncated` 的 job：点 jobs 行 → screen 内容含命令与输出、truncated 提示；running 时改输出 → `app._refresh_status()` 后 screen 更新；`snapshot` 返回 None 时点击只 notify 不崩；esc 关闭。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 通过 + 闸门**
- [ ] **Step 5: 提交** `feat(tui): read-only job log overlay`

### Task 5: ctx 数据通路

**Files:**
- Modify: `zenagent/providers/limits.py`（公开 `estimate_text_tokens`）、`zenagent/app/runtime.py`（保留 `RenderedPrompt`）、`zenagent/app/views.py`（`RunStatus.context`）、`zenagent/app/chat.py`（组装）
- Create: `zenagent/app/context.py`
- Test: 新增 `tests/test_app_context.py`

**Interfaces:**
- `estimate_text_tokens(text: str) -> int`（limits.py 公开别名，`__all__` 更新）。
- `Runtime.prompt_report: RenderedPrompt | None`：`_build_prompt` 里保存；`_prompt_with_mode` 若有 plan 附注则追加伪块 `mode_note`。
- `ContextSlice(id, label, chars, tokens, pct)`、`ContextReport(slices, total_tokens, window, measured, prompt_tokens)`、`build_context_report(prompt_report, tools, messages, last_usage, context_used, window) -> ContextReport`：
  - 分组：`system.*`→`system`；`context_files`→`AGENTS.md`；`skills`→`skills`；`extensions`→`extensions`；`plan_mode`→`plan`；`tools`→工具 schema（`estimate_tool_tokens`）；`session`→消息（`estimate_prompt_tokens(messages, None)`）；
  - `total_tokens = context_used`（沿用状态带口径），`measured = last_usage is not None`；
  - 残余 `other = max(0, total - sum(est))`，百分比以 `total` 为分母，空总量时全 0。
- `RunStatus.context: ContextReport | None = None`；`ChatController.status` 填充。

- [ ] **Step 1: 写失败测试** —— 构造伪 `RenderedPrompt` + tools + messages + usage：分组、字符数、`≈` 估算、百分比和 ≈100、残差 ≥0；无 usage → `measured is False`；window=0 / 空 prompt → 不除零。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 通过 + 闸门**
- [ ] **Step 5: 提交** `feat(app): context composition report`

### Task 6: ctx UI

**Files:**
- Create: `zenagent/cli/tui/screens/context_breakdown.py`
- Modify: `zenagent/cli/tui/widgets/sidebar.py`（Context 区）、`zenagent/cli/tui/app.py`
- Test: 新增 `tests/test_tui_context.py`

**Interfaces:**
- 侧栏 Context 区（位置在 Runtime 之后、Todo 之前）：行 `ctx {pct}% · {total}/{window}`；一行 20 列堆叠条（按 slice 顺序用 `█` + `palette` 色）；`top1 {pct}% · top2 {pct}%`；无报告时 `ctx 0%`。行点击 → `ContextBreakdownScreen`。
- `ContextBreakdownScreen(DialogScreen[None])`：每 slice 一行 `label  chars  ≈tokens  pct`，底部注脚“分段为估算（≈），总计为 provider 实测/估算”。
- `ZenApp.action_show_context_breakdown()`。

- [ ] **Step 1: 写失败测试** —— 注入带 `context` 的 status（直接构造 `ContextReport`）→ 侧栏行文本含 `ctx`/`%`；点击 → screen 出现，含 `AGENTS.md` 与 `≈`；esc 关闭。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 通过 + 闸门**
- [ ] **Step 5: 提交** `feat(tui): context breakdown section and overlay`

### Task 7: 文档与收尾

**Files:**
- Modify: `docs/feature-map.md`、`README.md`
- Test: 全量闸门

- [ ] **Step 1: 更新 feature-map 的 TUI 行（拖拽/ctx/job log/inline todo/ palette）与 README 一句话**
- [ ] **Step 2: `bash scripts/check.sh` 全绿**
- [ ] **Step 3: 提交 `docs: TUI panel polish notes`**
