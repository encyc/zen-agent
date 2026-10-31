# TUI 工作面板 · 第二轮打磨设计（2026-10-31）

状态：待审批
作者：Lead（与用户讨论后整理）
基线：v0.4.4（`c9cd880`，工作面板已合入 main）
相关：`zenagent/cli/tui/{app.py,theme.tcss,widgets/{sidebar,todo_hud,work_strip,subagent_banner}.py,screens/{todo_list,modals}.py}`、
`zenagent/app/{chat.py,views.py,runtime.py}`、`zenagent/prompts/{__init__,spec}.py`、
`zenagent/providers/limits.py`、`zenagent/config.py`、`zenagent/tools/jobs.py`

## 1. 意图

工作面板已能显示 todo/subagent/jobs，但五处体验不够：侧栏宽度写死、ctx 只有百分比、
todo 侧栏只有一行摘要、job 只能看状态看不到日志、配色散落在各 widget 里。本轮只做这五件，
不新增抽象层。

## 2. 目标 / 非目标

**目标**
1. 侧栏宽度可鼠标拖拽：1 列拖拽把手，宽 20–60，默认 26，双击把手复位（可选）。
2. 侧栏出现 Context 区：总占用 + 分量堆叠条 + 前几项占比；点击打开完整明细浮层。
3. 侧栏 Todo 区列出**全部**任务（阶段头 + 每任务一行、超宽截断），点击任意行打开完整浮层。
4. 侧栏 Jobs 行可点，打开只读执行日志浮层；运行中的日志随现有 1s 轮询刷新。
5. 统一语义色板：分区标题、运行/成功/失败状态、cost、job id 有区分度，风格保持安静。

**非目标**：真实 provider tokenizer；ctx 明细进入模型上下文；拖拽宽度跨启动持久化；
job kill/重跑；侧栏多栏；主题系统重构（只做一套内置语义色）。

**反目标**：app 层 import Textual；把估算数字标成实测；拖拽破坏"输入行贴底"与窄屏单家规则；
配色改造引入闪烁或让信息密度下降。

## 3. 约束

- 基线 v0.4.4；`zenagent/app/**` 不得 import textual/rich。
- 窄屏阈值不变：`NARROW_WIDTH = 100`，<100 列侧栏隐藏、todo 回落 dock HUD。
- 全部估算必须标 `≈`；字符数是精确值。
- 不新增依赖；离线测试；`bash scripts/check.sh` 为闸门。

## 4. 方案

### 4.1 拖拽调宽

- `#root` 里在 `#main-column` 与 `#sidebar` 之间插 1 列 `SidebarResizeHandle`；
  `_sync_chrome` 同步它的 display，并给 `#sidebar` 设内联 `styles.width = _sidebar_width`。
- 把手：`on_mouse_down` → `capture_mouse()` + 记起点；`on_mouse_move` → `app.resize_sidebar(screen_x)`；
  `on_mouse_up` → `release_mouse()`。
- 宽度：`new = clamp(20, 60, screen.width - screen_x)`，同时保证主列 ≥ 30；
  测试直接调 `app.resize_sidebar(x)`（pilot 没有 mouse_move，避免依赖内部事件注入）。
- 默认 26、进程内记忆；不写 config（本轮不做持久化）。

### 4.2 Context 组成（回答"为什么测不准"）

provider 只返回一个 `usage.input` 总数，不返回分段 token 数；本仓库不引入真实 tokenizer
（内核零依赖，且 DeepSeek/Qwen 的分词器也不在依赖集里）。**能精确的是字符**——prompt 是
我们自己拼的：`compose_system()` 已经返回 `RenderedPrompt.blocks`（每块 `id/chars/cache_class`，
覆盖 system.*、`context_files`=AGENTS.md、`skills`、`extensions`、`plan_mode`），tool schema
和消息也在手里。所以：

- 每段字符数 **精确**；
- 每段 token 用仓库现成估算器（`providers/limits.py`：拉丁 4 字/token、CJK 约 1 字/token）；
- 总量优先用 provider 实测（`token_estimate()`：`last_usage.input + cache_read`，无 usage 时估算）；
- 显示为「实测总量 + 估算分段」，分段标 `≈`，并给一个"其他（生成/框量/误差）"残差；
  百分比以实测总量为分母。

数据通路：`Runtime._build_prompt` / `_prompt_with_mode` 保留 `RenderedPrompt`（新增
`runtime.prompt_report`，plan 提示作为伪块追加）；新增 `zenagent/app/context.py` 纯函数
`build_context_report(prompt_report, tools, messages, last_usage, context_used, window)`
→ `ContextReport(slices, total_tokens, window, measured)`，挂到 `RunStatus.context`。

UI：
- 侧栏 Context 区：`ctx 37% · 74k/200k`、一行 20 列堆叠条（按段着色）、前 2 大项
  `sys 8% · AGENTS 4%`；空 prompt（首轮前）显示 `ctx 0%`。
- 点击 → `ContextBreakdownScreen`：表格（label / 精确字符 / ≈tokens / 占比 / 条），
  底部注脚说明估算口径。

### 4.3 Todo 全量内联

- 侧栏 Todo 区改为：每个 phase 一行暗色标题（key `phase:i`），每个任务一行
  `✓/▶/○/⊘ + 文本`（`_clip` 到内容宽，key `task:i:j`，保持 keyed 复用）。
- 列表不限条数，靠 `Sidebar(VerticalScroll)` 滚动；行点击 → 复用现有 `TodoListScreen`
  （完整文本、只读）。dock HUD 与单家规则不变。

### 4.4 Job 日志

- `views.py` 新增 `JobLogView(id, command, status, duration_ms, exit_code, output, truncated)`；
  `ChatController.job_log(job_id)` 从 `get_job_manager().snapshot(id)` 取值（无 loop/已 retire 返回 None）。
- 新增 `JobLogScreen(DialogScreen[None])`：头部状态行 + 命令 + `VerticalScroll` 输出
  （`truncated` 时给截断提示），`esc` 关闭。
- 侧栏 Jobs 行加 click（`set_handlers(on_job=...)`），点击打开；运行中时 `_refresh_status`
  若该 screen 在栈上则调 `update_view(...)`（1s 轮询已在跑）。

### 4.5 语义色板

- 新增 `zenagent/cli/tui/palette.py`：`INK/SURFACE/LINE/TEXT/MUTED/DIM/ACCENT/OK/ERROR`
  + `SECTION_ACCENTS`（Session/Model/Runtime/Todo/Subagent/Jobs 各一色）+ 状态色映射。
- 把 `sidebar.py`、`todo_hud.py`、`work_strip.py`、`subagent_banner.py`、
  `screens/todo_list.py` 里的硬编码 hex 换成 import 常量；`theme.tcss` 保持布局，
  颜色 token 注释同步更新。
- 层级：分区标题用各自 accent 的暗色，值用 `TEXT`，次要信息 `MUTED`，状态符号上语义色；
  只加粗标题与当前项，避免满屏彩色。

## 5. 验收

- 拖拽：`app.resize_sidebar(x)` 钳制到 20–60 且主列 ≥30；把手 hidden 时随侧栏隐藏；
  窄屏（90 列）不出把手。
- ctx：单测 `build_context_report`（有/无 usage、块分组、残差、百分比和为 100）；
  TUI：侧栏 Context 区行存在、点击开浮层、esc 关闭。
- todo：侧栏出现 N 个任务行（含 done 符号），点击行开 `TodoListScreen`，HUD 行为不变。
- job 日志：点击 jobs 行开屏，内容含命令与输出；`truncated` 有提示；运行中刷新可见；
  esc 关闭返回。
- 配色：palette 常量被 widgets 引用（单元断言），TUI 冒烟测试全绿。
- `bash scripts/check.sh` 全绿；现有 1452+ 测试不回归。

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 拖拽把手吃掉点击/焦点 | 只捕获鼠标；`ALLOW_SELECT` 已关；焦点不变 |
| 侧栏过宽挤坏转录/输入行 | 两侧钳制 + 主列 ≥30 + 现有窄屏规则 |
| 估算被误读为实测 | 明示 `≈`、残差行、浮层注脚 |
| Todo 行数大导致侧栏滚动过长 | 侧栏本就滚动；不裁剪是用户明确要求 |
| 颜色改造过度 | 只改状态与标题层级，保持 harbor 基底 |

## 7. 拆分

一份短计划，T1 palette 常量 → T2 拖拽 → T3 todo 内联 → T4 job 日志 → T5 ctx 数据通路 →
T6 ctx UI → T7 收尾（文档/闸门）。T5/T6 依赖 prompts 报告保留（T5 内完成）。

## 8. 留给实现者的决定

- 把手的视觉（竖线/空白 + hover 高亮）；是否双击复位（用户未要求，默认不做）。
- 堆叠条用 `█` 还是 `▁▔` 系；切段颜色具体值。
- Context 浮层的分栏宽度、注脚文案。
- Todo 阶段标题是否显示 `closed/total`（建议显示，22 列内截断）。
