# Textual 复刻方案（ZenAgent TUI v2）

> 前置阅读：`01-interaction-hierarchy.md`（层级）、`02-layout.md`（布局）、`03-status-line-and-composer.md`（状态带/输入区）、`04-transcript-and-tools.md`（转录/工具）、`05-overlays-and-surfaces.md`（浮层/全屏）、`06-feature-inventory.md`（功能取舍）。
> 本文只讲"我们怎么改"，不讲 omp 是什么。

## 0. 现状 vs omp：差距表

| 维度 | ZenAgent 现在 | omp | 结论 |
|------|---------------|-----|------|
| 主屏结构 | `Horizontal(Sidebar, ChatLog)` + StatusBar + PromptInput 三段 | 单列：转录 + dock（HUD 序列）+ composer，**无侧栏** | **重构**：默认无侧栏 |
| 状态栏位置 | 屏幕最底部独立一行 | 默认 `band` 形状：整条色带在**输入区上方** | **改** |
| 状态栏实现 | `StatusBar._format()` 把字段写死 | 27 个 segment 的有序装配 + 4 步宽度降级 + 不可见即不占位 | **重写为数据驱动** |
| 输入区 | 3 行圆角框 | 无边框 + `╰─ ` 弯角前缀；8 种可换形状 | **改 band** |
| 用户消息 | `› ` 前缀 + 正文色 | 整行填色块（暖底），无前缀 | **改** |
| 工具卡 | 单行 `▸ name args · status · ms` + 展开（已很接近） | 单行 + **状态底色** + 状态点 + `(Ctrl+O to expand)` + 分组 | **增强** |
| thinking | `Collapsible` 折叠 | 斜体 muted 正文（prose-only）或折叠 | **改** |
| 欢迎块 | 无 | 渐变 logo + 词标 + 版本 + Tip，**随对话滚走** | **新增** |
| 浮层 | `push_screen(ModelPicker/SessionPicker)`，各写各的 | 三档：内联槽(单实例+FIFO) / 贴底浮层 / 全屏，一套骨架复用；7 种单实例菜单 | **重做骨架 + 菜单栈 + 内联槽** |
| 键位 | `Ctrl+B/S/C/Q/X` | `Esc` 中断、`Ctrl+C` 清草稿、`Ctrl+D` 退出、`Ctrl+Q` 排队、`Ctrl+O` 展开… | **重排 + 迁移表** |
| 宽度降级 | 只有 `width>=100` 一个阈值 | 状态带四步丢段、Tip<50 列隐藏、锁定优先级 | **按段表实现** |

## 1. 目标架构（Textual 组件树）

```
ZenAgentApp(App)
└── Vertical #root
    ├── TranscriptView (VerticalScroll, height: 1fr)     ← 可滚，自动贴底
    │   ├── WelcomeBlock        (首次进入，滚走后不回收)
    │   ├── UserBlock           (整行填色)
    │   ├── AssistantBlock      (Markdown)
    │   ├── ThinkingBlock       (斜体 muted，可折叠)
    │   ├── ToolCard            (单行 + 展开，状态底色)
    │   ├── NoticeRow / ErrorBlock / UsageRow / CompactionDivider
    │   └── TodoHud / SubagentHud（可选，或放 dock）
    └── ComposerDock (Vertical, height: auto)
        ├── HudSlot             (Vertical, height: auto, 空时 0 行)
        │   ├── QueuedMessages
        │   ├── TodoHud
        │   ├── SubagentHud
        │   └── WorkingRow      (⎋ Working… + tok/s)
        ├── AttachmentChips     (Horizontal, height: auto)
        ├── ComposerBand        (Vertical, height: auto)
        │   ├── StatusBand      (1 行，填色，segment 装配)
        │   └── Horizontal      (ComposerRow)
        │       ├── PromptGutter (width: 3, 自绘 `╰─ ` / 空格)
        │       └── PromptInput  (TextArea, 无边框, height: 1..max)
        └── HintRow             (窄屏/首次使用的键位提示，可退役)
```

关键决定：
1. **dock 用 `height: auto` 的 Vertical 自然堆叠**，不用 `dock: bottom`——Textual 的 `1fr` + auto 已经给出"转录吃掉剩余高度、dock 贴底"的效果，且不会和 ScrollView 打架。
2. **HUD 一律常驻但高度为 0**（`display: none`），不做动态 mount/unmount。原因：mount 会让 `scroll_end` 抖动，omp 的 chrome floor 算法本质上就是为了解决这个问题；我们用一个更简单的等价手段（HUD 常驻 + 显示时手动保持贴底）。
3. **状态带是独立 widget，不写进输入框的 border-title**（Textual 的 `border_title` 撑不起"左组 + 刻度线 + 右组"三段式）。
4. **浮层用 `ModalScreen`**，`CSS` 里 `align: bottom; width: 100%; height: auto; max-height: 80%` 复刻"贴底向上长"。
5. **滚动要重新设计，不要移植 omp 的 scrollback 机制**（`04-transcript-and-tools.md` §2.1）：omp 的"已提交行写进终端原生 scrollback 后永久不可变"是它做全局展开、且从不探测滚动位置的原因。Textual 是 alt-buffer，没有原生 scrollback 可蹭，正确移植是 **stick-to-bottom**（在底部才跟随新内容；用户上滚即锁定并给"有新内容 ↓"提示）。**不要**移植 append-only 稳定行 ledger / `RowSplit` / replay——那些是为"不可回滚的字节"付的性能税。
6. **逐卡展开为主，`Ctrl+O` 只做"全部展开/收起"的批量开关**：omp 的全局语义是上面那条妥协，我们能真正逐卡（现有 `ToolCard` 的 Enter/space 方向是对的）。
7. **工具卡三种拓扑要分开做**（`04` §2.4）：`framed`（自绘边框，用于有 diff/多分区的重工具）、`plain tinted`（整块底色，通用兜底，**优先做这个**）、`inline`（无框无底色，用于 grep/glob 等搜索类，避免打断阅读节奏）。第一版只做 `plain tinted` + `inline`；`framed` 留给 edit/bash 两个重工具。
8. **观感的来源是"骨架 + 折叠文案 + 状态色"，不是颜色本身**：整套折叠/降级文案要照抄——`(ctrl+o: Expand)`、`… N earlier lines`（保留尾部）、`… N more items`、`… (N more hunks, M more lines)`；行数上限 3/12、items 8、diff 8 hunks/40 行。另外块间固定 1 空行、且渲染后剥掉首尾纯空白行（`trimBlankEdges`），带底色的卡因为有 SGR 不被剥——所以 tinted 卡有呼吸、framed 卡紧凑，这条比配色更影响观感。

## 2. 模块改造清单

### 2.1 新增（`zenagent/cli/tui/`）

| 文件 | 职责 |
|------|------|
| `widgets/status_band.py` | segment 装配 + 宽度降级；对外只暴露 `update_from(status)` 与 `.plain`（退出摘要继续可用） |
| `status/segments.py` | `Segment` 协议：`render(ctx) -> Text | None`；`None` = 不占位 |
| `status/presets.py` | 3 个 preset：`default` / `minimal` / `compact`（左右段表 + 分隔符） |
| `widgets/composer.py` | `ComposerDock`：band + gutter + 输入 + hint |
| `widgets/prompt_gutter.py` | `render_line(y)`：第 0 行 `╰─ `，其余 3 空格 |
| `widgets/welcome.py` | 词标（`zen` ASCII）+ 版本 + 一行 Tip，居中，宽度不足逐级降级 |
| `widgets/rows.py` | `UserBlock` / `ThinkingBlock` / `NoticeRow` / `ErrorBlock` / `UsageRow` / `CompactionDivider` |
| `widgets/hud.py` | `TodoHud` / `SubagentHud` / `WorkingRow` / `QueuedMessages` |
| `widgets/composer_slot.py` | A1 内联面板槽：**单实例 + FIFO 排队 + 打开前焦点保存/关闭后恢复**（审批、`ask`、小选择器都挂这里，不挂 Screen） |
| `screens/palette.py` | 统一的"贴底浮层"骨架：标题嵌上边框 + 搜索行 + 列表 + footer 提示 + 无结果态；尺寸三档 `sm/md/lg`（按宽度占比 ≥45% / ≥75% 推导），**<100 列一律升为 full**（`05-overlays-and-surfaces.md` §2） |
| `screens/settings.py` | 全屏 Settings（左分组 + 右详情） |
| `screens/context_view.py` | 全屏上下文占用浏览 |
| `menu_stack.py` | `MenuKind` 注册表 + `open_or_focus(kind)` + `release(kind)` |

### 2.2 改写

| 文件 | 改什么 |
|------|--------|
| `app.py` | `compose` 换成 §1 的树；`BINDINGS` 换成 omp 键位；加 `action_*` 与 Esc 语义链 |
| `widgets/status_bar.py` | 删除，能力迁到 `status_band.py`（保留 `plain` 契约给 `app.exit_summary`） |
| `widgets/chat_log.py` | 改名 `transcript_view.py`；行类型分发；滚动策略（贴底/用户上滚打断） |
| `widgets/tool_card.py` | 加状态底色、状态点、折叠文案（`(enter: expand)` / `… N earlier lines`）、被 `Ctrl+O` 批量驱动的展开态 |
| `widgets/prompt_input.py` | 去边框；支持 `max-height`；`Ctrl+J` 换行兜底 |
| `screens/modals.py` | 用 `palette.py` 骨架重写 ModelPicker / SessionPicker |
| `theme.tcss` | 加 band/gutter/tool 状态底色/welcome 样式；删输入框圆角框 |
| `widgets/sidebar.py` | **降级为可选**：默认隐藏，`Ctrl+B` 打开 |

### 2.3 现有测试影响

`tests/test_tui_*.py` 共 5 个文件 16 个用例，P0 会直接打掉其中 3 个（都断言"底部有 StatusBar / 左侧有 Sidebar / 输入框在最下方"）：`test_tui_skeleton.py` 的 `test_app_mounts_and_shows_model_in_status_bar` 与 `test_screen_widgets_have_real_geometry`，以及 `test_tui_polish.py` 里的侧栏可见性断言。
改布局时**同步改断言，不要删测试**：把"状态栏在最底部"改成"状态带在输入行正上方、输入行是最后一行"——这正好是 `02-layout.md` 的核心结论，测试即规格。

### 2.4 不动

`zenagent/app/**`（UI 无关层）、`zenagent/agent/**`、内核。TUI 只消费 `RunStatus` / `MessageView` / `ToolCallView`；**新增字段（thinking 等级、context 百分比、model 显示名）先加到 `zenagent/app/views.py`，不要在 widget 里读 runtime**。

## 3. 视觉规格（harbor → omp token 映射）

omp 的 token 是**语义化**的，我们照抄语义、保留 harbor 的色相：

| omp token | 用途 | harbor 取值（建议） |
|-----------|------|---------------------|
| `colors.text` | 正文 | `#d7e0e8` |
| `colors.muted` | 次要文字 | `#8296a6` |
| `colors.dim` | 更弱（分隔线文字） | `#5f6b78` |
| `colors.accent` | 强调（模型名、运行中） | `#d9a86c` |
| `colors.border` | 面板边框 | `#263441` |
| `colors.borderAccent` | 焦点边框 | `#d9a86c` |
| `colors.success` | 成功态**边框/状态点用 dim，不用绿**（omp 的规则：pending=accent、success=dim、只有 error/warning 才着色） | `#8296a6`（若确需可辨用 `#8fbf8f`） |
| `colors.error` | 失败 | `#e08787` |
| `colors.warning` | 降级/告警 | `#d9a86c` |
| `colors.userMessageBg` | 用户消息块底色 | `#1b222a` |
| `colors.toolPendingBg` | 工具运行中 | `#141c24` |
| `colors.toolSuccessBg` | 工具成功 | `#131a21` |
| `colors.toolErrorBg` | 工具失败 | `#241a1a` |
| `colors.thinkingText` | thinking 正文 | `#8296a6`（斜体） |
| `colors.selectedBg` | 选中行底色 | `#263441` |
| `colors.mdCode` / `mdCodeBlock` | 行内码 / 代码块 | `#c9a6d9` / `#9fb8cc` |
| `colors.toolDiffAdded` / `Removed` | diff | `#8fbf8f` / `#e08787` |

**只保留一个强调色**这条不破：`accent` 就是暖沙，`success/error` 是状态色不算强调色。

## 4. 分阶段实施（每阶段独立可交付）

### P0 骨架重构（最大风险，先做）
- 去掉默认侧栏；`compose` 换成转录 + dock 树；输入区去边框 + `╰─ ` gutter。
- 新增 `WelcomeBlock`（先静态文本，不做渐变）。
- **验收**：`uv run zen` 打开后，屏幕是"转录 + 底部两条（状态带 + 输入）"；resize 到 60 列不崩；`bash scripts/check.sh` 全绿。
- **测试**：几何测试（输入区是最后 2 行、状态带在输入区上方、转录高度 = 总高 - dock 高）。

### P1 状态带 segment 化
- `Segment` 协议 + 3 preset + 四步降级 + `plain` 输出。
- **验收**：宽度 160/120/100/80/60 五档截图与 `plain` 对齐；`app.exit_summary` 仍然可读。
- **测试**：单测覆盖"不可见段不产生分隔符"、"path 永远最后丢"、"窄屏丢右组尾部"。

### P2 转录行类型 + 工具卡
- `UserBlock` 填色块（**无 `›` 前缀**）；thinking 斜体 muted + 可折叠（折叠态一行 `✻ Thinking · 1.2k`）；工具卡状态底色 + 状态点 + 折叠文案 `(enter: expand)`；`Ctrl+O` 批量展开/收起；`UsageRow`；`ErrorBlock`；块间 1 空行 + 剥首尾空行。
- **验收**：一次含 bash 失败的会话里，失败卡是红底 + 红点，**成功卡保持安静（dim，不绿）**；Enter/space 逐卡展开，`Ctrl+O` 一键全展开/收起。
- **测试**：每种行类型一个 snapshot-ish 断言（渲染文本 + 关键样式标签）。

### P3 浮层体系
- `palette.py` 骨架 + `menu_stack.py`；ModelPicker / SessionPicker / 历史搜索都复用它；Settings 与 Context 两个全屏界面。
- **验收**：连按两次 `Ctrl+P` 只开一个选择器；`Esc` 关掉后焦点回到输入区。
- **测试**：菜单栈单实例（同 kind 二次 open 返回同一 handle）、Esc 关闭后焦点断言。

### P4 键位与 Esc 语义链
- 按 omp 重排（见 §5）；实现 Esc 7 步链；hint 3 次退役。
- **验收**：`Esc` 在"有草稿"时不删字；`Ctrl+C` 清草稿但不退出；`Ctrl+D` 退出；`Ctrl+Q` 排队。
- **测试**：Esc 链每条分支一个测试（bash 模式/python 模式/流式中/有草稿/空+双击）。

## 5. 键位迁移表（会破坏现有肌肉记忆，需在 release note 写明）

| 动作 | 现在 | 改为（omp） | 备注 |
|------|------|-------------|------|
| 中断 | `Ctrl+C` | **`Esc`** | omp 里 `Ctrl+C` 只清草稿 |
| 清草稿/清屏 | — | `Ctrl+C` | 空草稿时第二次 `Ctrl+C` 才退出 |
| 退出 | `Ctrl+Q` | **`Ctrl+D`** | |
| 排队 follow-up | `Ctrl+S`（steer） | **`Ctrl+Q`** | 保留 `Ctrl+S` 作为 steer 别名更安全 |
| 取回排队消息 | `Ctrl+X` | `Alt+Up` / `Shift+Up` | `Ctrl+X` 可保留为"清空队列" |
| 展开工具 | — | **`Ctrl+O`** | 全局开关 |
| 折叠 thinking | — | `Ctrl+T` | |
| 循环 thinking 等级 | — | `Shift+Tab` | |
| 循环模型 | — | `Ctrl+P` | 与"命令面板"冲突：**命令面板改用 `Ctrl+K` 或 `/`** |
| 选模型（浮层） | `/model` | `Alt+P`（临时）/ `Alt+M`（设角色） | `/model` 继续可用 |
| 外部编辑器 | — | `Ctrl+G` | |
| 搜历史 | — | `Ctrl+R` | |
| Agent/会话切换 | `/sessions` | `/sessions` + `Alt+A` 预留 | |
| 换行 | `Shift+Enter` | 保持 + **`Ctrl+J` 兜底** | 很多终端不转发 Shift+Enter |

> 冲突裁决：Textual 默认 `Ctrl+P` 是命令面板。omp 用 `Ctrl+P` 循环模型，因此命令面板让位到 `Ctrl+K`（同时保留 `/` 触发命令补全）。这是本次唯一"为复刻牺牲习惯"的地方。

## 6. 明确不做（写进 docs，避免以后反复讨论）

- composer 8 种形状 → **只做 band**（+ `borderless` 作为窄终端降级）
- 27 段状态栏 → **只做 10 段**：`model` `thinking` `mode` `path` `git` `context_pct` `cost` `tokens` `session_name` `spinner`
- 全屏界面 → **只做 3 个**：Settings、Context Viewer、Session Picker
- 工具专用渲染器 → **只做 5 个**：bash、read、edit/write、grep、通用兜底
- 不做：插件市场、技能市场、安全扫描、语音、录屏、collab、worktree、Git UI、ps-top、debug 面板、Agent Hub、goal/vibe/ratchet 模式、sixel/图片、鼠标

## 7. 总验收清单

- [ ] 默认打开：无侧栏，转录全宽，底部 = 状态带 + `╰─ ` 输入行
- [ ] 状态带 5 档宽度都不溢出、不出现双分隔符
- [ ] 工具卡单行 + 状态点 + 底色，`Ctrl+O` 全局展开/收起
- [ ] `Esc` 不毁草稿；`Ctrl+C` 清草稿；`Ctrl+D` 退出
- [ ] 连按同一快捷键不会叠出两个浮层
- [ ] 浮层关闭后焦点回到输入区，且转录仍贴在最新一行
- [ ] `app.exit_summary` 仍能给出 model/tokens/cost
- [ ] `bash scripts/check.sh` 全绿；新增几何与状态带测试

## 源码对照

- 现状：`zenagent/cli/tui/app.py`、`widgets/{status_bar,chat_log,tool_card,prompt_input,sidebar}.py`、`screens/modals.py`、`theme.tcss`
- 对照目标：`02-layout.md`（dock 顺序、shape 矩阵）、`03-status-line-and-composer.md`（段表与降级）、`04-transcript-and-tools.md`（行类型）、`05-overlays-and-surfaces.md`（浮层骨架与菜单栈）
