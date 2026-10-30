# 对话流与工具卡渲染（Transcript & Tool Cards）

> 调研对象：`can1357/oh-my-pi`（omp）的 TUI 设计。源码镜像 `/tmp/omp-src`，路径均相对 `packages/`。
> 目标：只记录**设计与交互事实**，用于用 Textual 复刻；不贴大段实现代码。
> **证据分级**：截图 `/tmp/omp-assets/*.webp` 是 **pi 时代**（`(Ctrl+O to expand)` 这种字面提示、`⠋ Working... (esc to interrupt)`、双栏 welcome 都属旧版），**凡是与源码冲突的，以源码为准并在文中标注「截图版」**；布局骨架见 `02-layout.md`，本文件只负责对话流内部与工具卡。

## 1. 一句话概括

omp 的对话区不是"一个可滚动列表"，而是**两条渲染带**：上面是**已提交行的永久 scrollback（终端原生，写出去就 immutable）**，下面是一条**底部对齐、可随时重排的活动 viewport**；每次渲染只把活动区里"滚出屏幕"的行追加进 scrollback，**渲染器从不探测用户的滚动位置**。

在这条带子里，一行不是"一条消息"，而是一个**有生命周期的 block**（`active → settled → committed`），行的形态由"block 生命周期 + 工具渲染器"共同决定；工具卡的统一骨架是**状态头（图标+动词+目标+徽章+meta）/ 正文分区 / 边框内 footer 元信息**，三种拓扑（framed / inline / plain tinted）覆盖全部 61 个工具。

## 2. 结构与层级

### 2.1 两条渲染带

```
终端屏幕（从上到下）
├─ 历史带 history（TerminalFramePlan.history，append-only，terminal scrollback）
│  └─ 已 committed 的 block 的物理行（重排 = 清屏重放，见 4.4）
└─ 活动带 viewport（可替换、逐行 diff）
   ├─ 欢迎块（未退休前是"活动区顶部的 header"，不是固定面板）
   └─ TranscriptContainer 的 live blocks（active + settled）
      └─ block（一个 Component）→ render(width) → rows[]
```

- 应用层显式决定"哪些行算定稿"：`TranscriptContainer.peekFinalizedBatch()` 产出 `HistoryBatch`，TUI 只写一次并回执 id（`tui-core-renderer.md` §1）。
- 因此 **"滚动"不是 UI 功能，而是终端 scrollback 的副作用**：`#composeViewport` 只负责"当前屏幕底部这一屏显示哪些行"，从不读滚动位置，也从不把"用户上滚"当成事件。

### 2.2 block 生命周期（决定"这一行还能不能变"）

| 状态 | 含义 | 视觉后果 |
|---|---|---|
| `active` | 还在变（流式、工具执行中） | 活区最下方；计入"工具活动配额"（`MAX_LIVE_BLOCKS = 256`） |
| `settled` | 已定稿但还在活区 | 仍可被 `Ctrl+O` 展开/重排；等压力把它顶出屏幕 |
| `committed` | 已写进 scrollback | **永久冻结**；`Ctrl+O` 不再影响它 |

- 定稿由组件自报：`isTranscriptBlockFinalized()`；还在等异步资源（SVG 位图、图片转换）的块用 `isTranscriptBlockPending()` **阻止退休**，避免把占位符冻进历史。
- 边界情况：一个 settled 块只把**头部若干行**献给历史（`RowSplit`），剩余部分仍活 —— 这样"上面已冻结、下面还能长"的卡片不会让屏幕跳。
- 退休有时间预算：`RETIREMENT_BUDGET_MS = 8`（一帧最多花 8ms 铺历史），超出的下一帧继续。

### 2.3 一个 block 的内部解剖

```
block
├─ 前缀/状态头（1 行，多数情况）
│  └─ [状态图标] [动词 标题] [: 目标摘要] [徽章] [ · meta1 · meta2]
├─ 正文
│  ├─ 分区（labeled section）→ 画一条带标题的分隔栏
│  ├─ 树行（├─ / └─ / │ 续行）
│  └─ 代码/输出面板（可能被截断成 tail 窗口）
└─ footer 元信息（framed 卡片画在**下边框内部**；plain 卡片画成尾行）
```

### 2.4 工具卡的两种拓扑（第三种是 inline）

**A. framed（自绘边框）** —— `renderOutputBlock` / `framedToolCard`（bash、read、write、edit、task、ask、todo…）：

```
╭─── ✔ Edit src/app.py  ⟨+8/-1⟩ ─────────────────────────╮   ← 上边框带 head label
│  313│ def main():                                      │   ← 1 格左右内边距
│ -315│     print("hi")                                  │
├─── Output ─────────────────────────────────────────────┤   ← 分区栏（teeRight/teeLeft）
│  …                                                     │
╰─── Wall 0.16s · Timeout 300s ──────────────────────────╯   ← footer meta 骑在下边框里
```

**B. plain tinted（无边框，整块底色）** —— 自定义工具 / 扩展工具 / 通用兜底卡：

```
<toolPendingBg / toolSuccessBg / toolErrorBg 铺满整块，含上下各 1 行 paddingY>
● Bash                        ← 状态头
 └ command --flag             ← 折叠时的参数摘要（dim，带 tree.last 拐角）
…输出 4 行…
(ctrl+o: Expand)              ← dim 提示
```

**C. inline（无框无底色，直接排在正文流里）** —— grep / glob / find / wait 等搜索类：只有一行头 + 树行/列表行，**不加任何框**（`ToolRenderer.inline = true`），因为它出现在 assistant 正文中间时不该打断阅读节奏。

## 3. 布局与视觉规则

### 3.1 垂直节奏：1 行间隔，且"块自己带的空行会被吃掉"

- 活区拼装时，**块与块之间固定插 1 个空行**（`#composeViewport` 里 `if (output.length > 0) output.push("")`）；没有 margin/padding 概念。
- 每个块渲染后都会 `trimBlankEdges()`：**首尾的"纯空白行"被剥掉**再计高。于是：
  - framed / inline 卡片天然紧凑（它们自带的上下 padding 被剥掉）；
  - **带底色的行不是纯空白**（有 SGR 背景），所以穿过裁剪保留下来 —— 这就是 tinted 卡片看起来"上下有呼吸"、而无底色卡片贴得紧的机制（`ToolExecutionComponent` 注释里写死了这个理由）。
- 结论（复刻要点）：**块间距由容器统一给 1 行，块内部不要自己加空行**；要"呼吸感"就启用底色，而不是加空行。

### 3.2 缩进台阶

| 层级 | 缩进 | 用途 |
|---|---|---|
| L0 | 0 | 卡片边框、状态头、welcome 块、分隔线 |
| L1 | 1 | 卡片内正文（`paddingX = 1`）、悬浮单行（notice/usage/working/recap 全部 `paddingX = 1`） |
| L1.5 | tree 前缀 | `├─ ` / `└─ ` 各占 2 列；续行用 `│ ` 或 `  ` 对齐（`nestedMarkers()`） |
| L2 | 2 | 错误正文的续行（`CONTINUATION_INDENT = "  "`，让换行落在前缀下方） |

- 树形约定：`tree.branch = "├─"`、`tree.last = "└─"`、`tree.vertical = "│"`（unicode 预设）。工具输出的多文件/多 agent/多匹配全部用这一套。
- 注意一些"元信息行"用 `tree.last` 当**拐角符**而不是列表符：兜底卡折叠时的参数摘要是 `└ <args>`。

### 3.3 边框、底色与状态

边框颜色由状态推导（`renderOutputBlock`）：

| 状态 | 边框色 | 底色 token | 状态图标 |
|---|---|---|---|
| pending / running / partial | `accent` | `toolPendingBg` | `⏳` 或 spinner 帧 / `⟳` |
| success | `dim`（不是绿！） | `toolSuccessBg` | `✔`（或工具自己的字形） |
| error | `error` | `toolErrorBg` | `✘` |
| warning | `warning` | `toolErrorBg` | `⚠` |
| info / skipped | `borderMuted` | （不染色） | `ⓘ` |

- **成功态刻意用 dim 边框**：成功是默认结果，不该在视觉上抢戏；只有失败/警告才着色。
- 底色只在"不画框"的 plain 卡片上用，且用 `bgFill`（不是 `bg`）—— 因为内层文本会带 `\x1b[0m`，普通 `bg` 会被打洞。
- framed 卡片**不染底色**（边框已经表达了卡片边界），这是 A/B 两种拓扑的互斥关系。
- 每个卡片横向至少留 1 格内边距；工具卡即使全局开了 tight 布局也强制保留（`setIgnoreTight(true)`），否则会贴边。

### 3.4 顶部 welcome / landing 块

**当前源码（`prompt/welcome.ts` + `prompt/composer.ts`）**

```
████████████    ▄▀▀▄ █▀▄▀▄ █▀▀▄        ← 12 列块状 logo + 4 列 gap + 词标（3 行，半块字符）
   ██  ██       ▀▄▄▀ █ █ █ █▄▄▀
   ██  ██                  █           ← 词标的 p 下落一行
   ▒▒  ██
       ██       v0.9.12                 ← 版本号（dim）压在词标最后一行之下
         Tip: <tips.txt 里的一句话，italic；`Tip:` 用 customMessageLabel 色、正文 muted，整块居中，最大 72 列宽>
```

- 组成：**只有** logo + 词标 + 版本 + 一条 tip。logo 是 3 段渐变（洋红 → 紫 → 青）逐帧"高光扫过"的入场动画；动画期间 welcome 保持 active，**结束后才允许退休**。
- 宽度降级：`< 50` 列（`TIP_MIN_COLUMNS`）只留 logo 锁排里放得下的部分；再窄只剩 logo；tip 全程居中（`centerBlock`）。
- **何时消失**：它只是活区最上面的一段 header。`Composer.#offerHistory` 的判据是"header 高度 + chrome 高度 + 活区行数 > 屏幕行数"——一旦对话把屏幕填满，**header 先退休进 scrollback（只此一次，`#headerRetired = true`）**，之后永远不再出现（重放时用 `#reflowRetiredHeader` 把已退休的行按当前宽度硬换行重画）。唯一的开关是 quiet mode（`/settings`）：关掉后整个 welcome 都不出现。

**截图版（pi 时代，已不存在）**：双栏 —— 左栏 `Welcome back!`（accent）+ logo + 模型名 + provider；右栏 `? for keyboard shortcuts` / `/ for commands` / `! to run bash` + 分隔线 + `LSP Servers`（绿点列表）+ 分隔线 + `Recent sessions`（UUID + 相对时间）。中间一条竖分隔线。**这是旧版导航面板，当前实现没有**；复刻时若要"信息密度"，按右栏那三段的自定义列表来做，但不要假设源码里有。

### 3.5 明度层级（颜色语义，不写 hex）

| 层 | token | 用途 |
|---|---|---|
| 主体 | `assistantMessageText`（缺省=终端默认前景） | assistant 正文 prose |
| 用户 | `userMessageText` on `userMessageBg` | 用户气泡 |
| 强调 | `accent` | 标题、状态头动词、pending 边框、光标、选中 |
| 工具 | `toolTitle`（动词）/ `toolOutput`（输出） | 卡片标题与正文 |
| 弱 | `muted` | 描述、路径、说明 |
| 更弱 | `dim` | meta、提示、规则线、树线 |
| 语义 | `success` / `warning` / `error` | 状态图标与错误正文（**成功态一般不上色**，只在图标上用） |
| 思考 | `thinkingText` | 思考正文（同时 italic） |
| diff | `toolDiffAdded` / `toolDiffRemoved` / `toolDiffContext` | diff 行 |

- assistant 正文的"思考味"不靠底色，靠 **italic + thinkingText**；工具卡靠**框/底色**区分；notice 靠**前缀词 + 颜色**区分。三者互不借用。

### 3.6 截断 / 折叠 / 溢出的四档降级

omp 把"内容放不下"分成四档，各有固定文案，复刻时值得原样抄：

1. **内容内部折叠**（块还完整）：折叠行数 `PREVIEW_LIMITS` =
   `COLLAPSED_LINES=3` / `EXPANDED_LINES=12`、`COLLAPSED_ITEMS=8`、`OUTPUT_COLLAPSED=3`、`OUTPUT_EXPANDED=10`、diff `8 hunks / 40 lines`。
   折叠后加一行 dim 提示：
   - 通用：`(ctrl+o: Expand)`
   - 命令/代码 tail 窗口：`… N earlier lines (ctrl+o: Expand)`（`capPreviewLines`，**保留尾部**，因为尾部是活边）
   - 列表：`… N more items` / `… N more lines`
   - diff：`… (3 more hunks, 12 more lines) (ctrl+o: Expand)`
   - 报错：`… +12 more lines (ctrl+o to expand)`
2. **窗口压力降级**（活区放不下所有块）：每块先给 1 行，富余行**优先给非工具活动块**（工具卡先被压扁），块内显示**尾部切片**（`rendered.slice(-allocated)`）。见 4.3。
3. **单块极压降级**（`allocation < 3`，`ToolExecutionComponent.#renderCompact`）：
   - 1 行：`• Bash · npm test 3s`（spinner 帧或 `•`）
   - 2 行：`╭─ Bash · npm test` / `╰`
4. **应急布局**（连"每块 1 行"都放不下）：`#renderEmergency`，用组件自报的 `renderTranscriptBlockEmergencyRow()`（如用户气泡取第一条有内容的行）凑一屏。

窄屏：卡片按 `visibleWidth` 逐行 `truncateToWidth`，长行不裁而是**换行**（`wrapTextWithAnsi`），所以窄屏是"变高"而不是"被切"；只有 footer meta 放不进下边框时才折行到正文上方（宁可多一行，也不丢 exit code）。

## 4. 交互契约

### 4.1 展开是**全局开关**，不是逐卡展开（最重要的交互事实）

`Ctrl+O`（绑定动作 `app.tools.expand`）做的是：

1. `ctx.toolOutputExpanded = !ctx.toolOutputExpanded`；
2. 遍历**活区所有**可展开子组件 `setExpanded(expanded)`；
3. 状态行提示 `Tool output expansion: enabled / disabled`；
4. 只重绘活区 —— **已经进 scrollback 的块保持提交时形态**（`setToolsExpanded` 注释：*"Toggling expansion mutates every live block; blocks already committed to terminal history stay at their committed presentation."*）。

原因就是 §2 的 immutable 历史带：一行的字节写进终端后无法回滚，所以"逐卡展开"在 omp 里只有**新块**才保证有效。逐卡点开只存在于原生终端（TSP）里由终端自己管的 disclosure。

- `Ctrl+O` 是**全局输入监听器**（`addInputListener`），**不依赖焦点**：审批框/选择器持有焦点时也能展开（`#7837`）。
- 例外：有浮层时让位；`TreeSelector` 自己用 Ctrl+O 做过滤循环时让位；`AskDialog` / `ApprovalDialog` 先吃掉它展开自己的问题/详情。
- 关联开关：`Ctrl+T` 切思考块显隐、`Ctrl+Shift+O` 切 `display.hideToolActivity`（隐藏整个工具活动；两者都会 `resetDisplay()`，因为已提交字节要重画）。

### 4.2 键位表（本文件相关）

| 键 | 动作 | 作用域 |
|---|---|---|
| `Ctrl+O` | `app.tools.expand` | 全活区展开/收起；浮层下让位；Ask/审批框先吃掉它展开自己 |
| `Ctrl+Shift+O` | `app.tools.toggleVisibility` | 整块隐藏/显示工具活动（触发 reset display，因为要清 scrollback 重画） |
| `Ctrl+T` | `app.thinking.toggle`（`thinking` 块显隐） | 全局，触发 reset display；只影响新块与活区块 |
| `Shift+Tab` | `app.thinking.cycle` | 推理档位循环 |
| `Esc` | `app.interrupt` | 中断当前回合；工作行的停止控件等价 |
| `Alt+L` | `app.display.reset` | 清屏重放（改变已提交字节的设置后使用） |
| `Ctrl+R` | `app.history.search` | 与对话流无关 |
| `PgUp/PgDn/滚轮` | **由终端 scrollback 处理** | omp 不接管 |
| `Enter`/`Space` | （无） | *omp 没有逐卡展开键*；Textual 移植时自行添加，见 §6 |

### 4.3 流式渲染协议（为什么"边流边出"不会闪烁）

- 只有两类块声明 `transcriptBlockMode = "appendOnly"`：**assistant 正文**和**同一条消息里的 thinking**。它们发布"语义行"（`TranscriptStableRow`，带 key），容器把它们**逐行**推进历史带。
- 契约：后续发布的数组必须是之前的**严格前缀扩展**；每行在当前宽度下渲染结果必须是对整块渲染的前缀。违反（主题切换改了已发字节、稳定的行不再前缀）→ **冻结**该块的继续发射（`#freezeStableRows`），已发出的字节无法回收，只能等它整体退休。
- 当前未闭合的 markdown/最后一个不完整行留在活区继续变 —— 所以观感是"文字一行行长出来"，而不是"整段重排"。
- 工具卡的流式状态：`isPartial = true` → 状态头用 spinner 帧、底色 `toolPendingBg`、`args` 还在流也能增量渲染（`updateArgs` 每 delta 一次，引用相等才短路）。

### 4.4 滚动与重排策略

- **没有"自动滚到底/用户上滚暂停"这套逻辑**（`tui-core-renderer.md` §3 末：*"The renderer never probes the user's scroll position"*）。屏幕永远显示"最新的一屏"，历史行被推上去后由终端 scrollback 接管。这样用户在翻旧行时，新输出不会强行把他拽回底部 —— 因为新行只是追加进 scrollback，不重绘屏幕。
- 屏幕被填满 → header 退休 → 活区块按顺序退休；**HUD/浮层变高把行顶出屏幕时**默认直接丢弃重排，但可配 `tui.completeScrollback = true`：顶出的行直接送进 scrollback，代价是 HUD 收起后会留一片空白行等新输出填。
- 宽度变化（resize）默认 `tui.resizeScrollback = "rebuild"`：**清空终端 scrollback，按当前宽度整篇重放一次**（一次原子写，屏上不出现中间帧）。另两种模式 `append`（保留旧宽度历史，下面再放一份新宽度）与 `preserve`（历史不动，只重绘视口）可选。
- 显式重排入口：`app.display.reset`（清屏重放，用于"思考块显隐"这类改变已提交字节的设置）。

### 4.5 工作行（working row）—— 严格说不在对话流里

它是 dock 里 `statusContainer` 的一行，紧贴 composer 上方：`⠋ Working…`（braille 10 帧、~80ms/帧；shimmer 时 30fps），`paddingX = 1`，右侧可 dock 一个 tok/s 与标题 trailer；原生终端版本变成 `spinner · elapsed · 意图(shimmer) · 停止控件`。
**截图版**写作 `⠋ Working... (esc to interrupt)`；当前源码里中断键由工作行自己的 stop 控件/单帧前缀 ` esc` 表达。

### 4.6 状态与展开带来的形态差异（速查）

**状态机**（`ToolCardPhase` → 头 / 边框 / 底）：

| phase | 触发 | 头 | 边框 | 底 |
|---|---|---|---|---|
| `pending` | 调用已发出、尚未开始执行（args 可能还在流） | `⏳` + 标题 | `accent` | `toolPendingBg` |
| `running` | 正在执行 | spinner 帧（~80ms/帧）或 `⟳` | `accent` | `toolPendingBg` |
| `partial` | 已收到流式结果、未定稿 | 同 running | `accent` | `toolPendingBg` |
| `success` | 定稿成功 | `✔` 或工具自己的字形 | `dim` | `toolSuccessBg` |
| `warning` | 超时 / 截断 / 部分失败 | `⚠` | `warning` | `toolErrorBg` |
| `error` | 定稿失败 | `✘` | `error` | `toolErrorBg` |
| `info` | 被 steering 跳过的占位（没真正执行） | `ⓘ`，标题 muted | `borderMuted` | 不染色 |

- 耗时：只有声明了 `tickElapsed` 的渲染器**每秒**重绘刷新 elapsed；<1s 不显示；定稿后冻结（`executionEndedAtNow`），所以"卡住的秒数"不会继续跳。
- 流式参数（args 还在来）与"已开始执行"是两个独立标志：`argsComplete` / `executionStarted`；只有后者才启动 elapsed 计时。

**展开前后的布局差异**（折叠上限见 §3.6）：

| 行 | 折叠时 | 展开后 |
|---|---|---|
| 兜底卡 | `└ <args 摘要>` + 输出前 4 行 + `(ctrl+o: Expand)` | 空行 + `Args`（dim）+ JSON 树 + 空行 + **全量输出**（不再截断，交给终端的 disclosure 钳制） |
| Bash | 命令 tail 窗口 + `Output` 前 3 行 | 完整命令 + 完整输出（tail 窗口解除） |
| Edit diff（Write 同类） | 8 hunk / 40 行 | 全量 diff（含行内反显） |
| compaction / handoff | 只有那条分隔线 | 分隔线下方多一块 `customMessageBg` 摘要盒 |
| hook / custom 消息 | 前 N 行 + `…` | 完整 markdown |
| todo | 只留进行中/匹配的若干项（上限 8），其余折成 `… N more` | 全部 phase / 全部条目 |
| Ask 结果卡 | 问题 + 选项行（默认已全展示） | 无差别（它随打开即全量） |

## 5. 完整清单

### 5.1 转录行类型总表（24 种）

「可变」= committed 之前能否改变；`append` = append-only 流式发布。

| # | 行类型（数据角色） | 视觉形态（前缀 / 缩进 / 底色 / 边框） | 触发 / 来源 | 可变 |
|---|---|---|---|---|
| 1 | 用户提示 `user` | 整行铺 `userMessageBg`（含上下各 1 空行）、`paddingX=1` 的 markdown、`userMessageText`；**无前缀字形、无边框** | 提交 prompt | 否 |
| 2 | 合成/agent 输入 `developer` / `synthetic` | 同 1 的底色但正文 `dim`；文件/远程查看器里折叠成 1 行 dim 摘要 + `ctrl+o` | 内部注入、advisor 重放 | 否 |
| 3 | assistant 正文 | `paddingX=1` markdown，无底色无边框，`assistantMessageText` | 模型流式 | **append** |
| 4 | 思考（可见） | `paddingX=1`、**italic + `thinkingText`**，无前缀；原生端折叠成 `Thinking…`→`Thought for 12s` 的 section 头 | 模型 reasoning | **append** |
| 5 | 思考（隐藏） | 单行脉冲：`✻ Thinking · 1.2k · 45.3 toks/s`；字形循环 `✻✼❉❊✺✹✸✶`，驻留 70–230ms 缓动；速率色 `dim→accent` | `hideThinkingBlock` 且正在思考 | 是 |
| 6 | 工具调用 + 结果 | framed / inline / plain 三拓扑，见 5.2 | `toolExecution` | 是→冻结 |
| 7 | 工具组（连续 read） | 一张卡：`Read` **` (N)`** + 树行 + 末尾 dim usage 行 | 连续只读文件 | 是 |
| 8 | `!` bash 执行 | `$ cmd`（bold，`bashMode` 色；`!!` 用 `dim`）+ **上下各一条通栏横线**（不是方框）；输出 tail 窗口；footer `… N more lines` / `(cancelled)` / `(exit 1)` | 用户 `!` | 是 |
| 9 | `$` python 执行 | 同上，`pythonMode` 色，代码单元 | 用户 `$` | 是 |
| 10 | 内联错误 | 空行 + `Error: …`（error 色，`paddingX=1`，续行缩进 2；**无图标**，图标只出现在卡片头里）；助理回合自身失败则是无前缀的 error 色正文。超行数 → `… +N more lines (ctrl+o to expand)` | provider/工具错误 | 是 |
| 11 | 警告 notice | 空行 + `Warning: …`（warning 色，`paddingX=1`） | `showWarning` | 是 |
| 12 | 状态 notice | 空行 + 1 行 dim（`paddingX=1`），如 `Thinking blocks: hidden`；原生端变 2.4s toast 不入历史 | `showStatus` | 是 |
| 13 | 状态 pill（单行摘要） | 1 行、缩进 1，**parts 之间用单个空格**（不是 `·`）：`⤴ Tangent dispatched [task] abc123 — 修复登录`；每段一个语义色（muted/dim/accent） | 后台任务完成、breadcrumb、prompt 文件读取 | 是 |
| 14 | compaction 分隔线 | 空行 + `──── 📷 remote-compacted · 256K→20K · ctrl+o ────`（dim 规则、muted 标签）+ 空行；展开后下方接一块 `customMessageBg` 盒子 | 自动/手动压缩 | 是 |
| 15 | handoff / branch summary | 同 14 的分隔线家族（`handed-off` / branch 摘要） | 分支切换、handoff | 是 |
| 16 | custom / hook 消息 | `customMessageBg` 圆角框（`borderMuted`）+ 头 `icon customType`（bold，`customMessageLabel`）+ 空行 + markdown（`customMessageText`）；hook 折叠到 N 行 + `…` | 扩展 / hook | 是 |
| 17 | skill 提示 | callout/chip：`/skill:name` 当作 chip 挂在用户气泡里或单独 callout | `/skill:` | 否 |
| 18 | 图片 | 内联 kitty/sixel 图片块；预算淘汰后退化为等高文字占位；不支持图片的终端显示 `[Image #1 800x600]` | 附件、工具产出 | 否 |
| 19 | diff（edit/write 结果） | 代码框 gutter：`-315│` / ` 313│` / `+322│`；色 `toolDiffAdded/Removed/Context`，行内改动用反显；折叠 8 hunk/40 行 | edit/write | 是 |
| 20 | usage 行 | 空行 + dim 指标条（`paddingX=1`）：`18:02  Δ 6.2s  ⤵18.4K  ⤴640  💾12K  ⏱0.9s  ⚡96.1/s` | 每回合结束（`stopReason != toolUse`） | 否 |
| 21 | todo 卡 | `Todo` + `closed/total`；行 `☑ 删除线`（success）/`☐`（accent 进行中）/`☐ 删除线`（error）/`☐ 文本 (note)`（warning）；完成时有删除线逐字揭示动画 | todo 工具 | 是 |
| 22 | task/subagent 卡 | `Task` + `· N agents`；每个 agent 一行树项 `├─ • Name: 描述 [role] [done] · 12 🛠 · 2 req · 12K · $0.01 · 45.2s`（task 行图标固定为 done 圆点，失败才换 `✘`；截图版写成 `reviewer(0): …`）；嵌套再加一层树线 | task 工具 | 是（spinner ~33ms） |
| 23 | 提醒/通知卡（notice 家族） | `MessageNoticeComponent`：`severity 底色反显` 的圆角卡片（或 inline：图标+1 行+可折叠正文）；用于 todo 未完成提醒、TTSR 回退通知、版本更新、迟到诊断 | 各类后台事件 | 是 |
| 24 | 轻量标记行 | 1 行 dim italic：`※ recap: …`（会话回顾）、`3 tool calls elided — no result on this branch`（被剥离的工具调用） | 空闲回顾 / 分支裁剪 | 否 |

补充（不单独成行，但会出现在流里）：`cache invalidation marker` / `served-model mismatch marker`（都用 `MessageDividerComponent` 的短规则线）、表格图表（markdown 表格下方渲染 chart）、SVG/figure 栅格、`todo reminder`、`advisor` 卡片、`IRC/launch-completion/background-tan`（都是 #13 的 pill 或 #23 的 notice 卡）。

### 5.2 工具卡清单（标题行 / 正文 / footer）

标题行的统一语法（`renderStatusLine`）：

```
[状态图标] [动词 标题][: 目标摘要] [ [徽章] ] [ ·meta1 · meta2 ]
     ↑            ↑                ↑               ↑
  状态/工具字形  accent 或 toolTitle  括号包起来      dim，sep.dot 连接
```

- `标题` 是动词（`Bash` / `Read` / `Edit` / `Write` / `Grep` / `Task` / `Todo`），不是工具内部名；
- `目标摘要` 只放**一个主要事实**（命令、路径、pattern），且**正文里不再重复**（`NativeToolHead` 注释：*"Each fact appears once — a target named here is never repeated in the body"*）；
- `meta` 是短事实，如 `+8/-1`、`12 matches · 3 files`、`in src/`、`truncated`（warning 色）。

| 工具 | 标题行 | 正文 | footer / 末行 |
|---|---|---|---|
| Bash | `❯ Bash`（带名字时 `Bash · <name>`）；**标题行不带命令**——命令在正文第一段，避免重复；运行中 spinner，成功换成 `❯` 字形 | 命令高亮（可含 `i` 意图行）+ `Output` 分区（tail 窗口） | 事实按序拼：`elapsed 0.16s · Wall 0.16s · Timeout 300s · Artifact: … · Exit: 1`（elapsed 只在 ≥1s 时出现） |
| Read（单文件） | `Read <path>:13-36`（` (summary: N elided spans)`、`(⚠ N conflicts)` 追加） | 文件内容（语法高亮）或 `Details` 分区 | 截断警告 `⟨Showing lines …⟩`（warning 色） |
| Read（多文件组） | `Read (N)` | 每文件 1 行树项，失败带状态前缀 | 末尾 dim usage 行（每文件一行） |
| Write | `Write <langIcon> <path>` | 写入内容预览 | 行数/执行后缀 |
| Edit / Create / Delete | `Edit <path> ⟨+8/-1⟩`（stats 紧跟路径）；`Create` / `Delete` 换动词；delete/move 无 diff → 1 行 inline 状态行 | diff（代码框 gutter） | diff 折叠提示 `… (N more hunks, M more lines)` |
| Grep | `🔍 Grep: <pattern> · 12 matches · 3 files · in src/`（`titleColor: toolTitle`） | 匹配列表树（inline，无框） | 0 命中：`⚠ Grep: pattern · 0 matches` + `No matches found` |
| Glob / Find | `🔍 Glob: <pattern> · N files` | 文件列表树 | `… N more files` |
| Task（子代理） | `Task · 3 agents`（运行中用 `task` 字形，不是 spinner） | 每 agent 1 行树项 + 可选 assignment/context 分区 | 每行自带 `· N req · tokens · $cost · 45.2s` |
| Todo | `Todo` + `closed/total` | checklist（phase 分组） | — |
| Ask | `✔ Ask · N questions`（framed，`borderMuted`）；分区标签 `[q1]`，问题 markdown + 选项行 `◉/○`（多选 `☑/☐`；选中 success 色、未选 dim+muted）；未作答显示 `⚠ Cancelled` | 提问**结束后**的对话卡；实时提问表单不在这里，它占 editor 槽位（截图里看着像对话卡） | 否 |
| 兜底（无渲染器） | `● <Label>` | 折叠：`└ <args 摘要>` + 输出前 4 行；展开：`Args` JSON 树 + 全量输出 | `(ctrl+o: Expand)` |
| wait / job / async | 单行 pill 或极简卡；`Task · running` 之类 | 无 | — |

### 5.3 符号表（unicode 预设，复刻时可直接用）

| 语义 | 字形 | token |
|---|---|---|
| pending / running / success / error / warning / info / aborted / done | `⏳` `⟳` `✔` `✘` `⚠` `ⓘ` `⏹` `•` | `status.*` |
| 树 | `├─` `└─` `│` `─` `└` | `tree.*` |
| 导航 | `❯` `➤` `▸` `▾` `⟵` | `nav.*` |
| 复选框 / 单选 | `☑` `☐` / `◉` `○` | `checkbox.*` `radio.*` |
| 括号（徽章、提示） | `⟨` `⟩`（nerd: `⟦⟧`；ascii: `[]`） | `format.bracket*` |
| 分隔 | `·`（前后带空格） | `sep.dot` |
| 圆角框 | `╭╮╰╯─│`；分区接枝 `├┤` | `boxRound.*` |
| 工具字形 | `❯` bash、`✎` write/edit、`🔍` search、`⇶` task、`☑` todo、`?` ask、`▶` eval、`⎇` gh | `tool.*`、`icon.search` |
| 指标图标 | `⤵` in、`⤴` out、`💾` cache、`⏱` time、`⚡` throughput、`💲` cost | `icon.*` |

## 6. 复刻判断（给 Textual）

### 6.1 值得复刻（高价值）

1. **"块"模型 + 1 行间距**：`ChatLog` 里一个 block 一个子 Widget，块间由容器统一给 1 空行，块内不加空行。这是 omp 观感的第一来源，比任何配色都重要。
2. **工具卡统一骨架**：状态图标 + 动词 + 唯一目标摘要 + `·` 连接 meta + 可选分区 + footer 事实。哪怕只实现 4 个工具（bash / read / edit / 兜底），也照这个骨架来，而不是"每工具一个自由格式"。
3. **三态视觉**：pending=running 用 accent 边框/spinner，成功用 dim，失败用 error 并加 error 底色。**成功不要用绿色高亮**（omp 的成功是"安静"的）。
4. **折叠文案与四档降级**：`(ctrl+o: Expand)`、`… N earlier lines`、`… N more items`、diff 的 `(N more hunks, M more lines)`，以及 1 行/2 行紧凑形态。文案本身就是设计资产。
5. **思考行**：`✻ Thinking · 1.2k · 45.3 toks/s` 的单行脉冲 + 完成后的 `Thought for 12s` 折叠头。成本极低、辨识度极高。
6. **usage 行**：每回合一条 dim 指标条（时间/Δ/in/out/cache/ttft/tok-s），和状态栏分工（本回合 vs 全会话）。
7. **tool group**：连续 read 合成一张卡（`Read (N)` + 树行），否则 10 个 read 会把对话流冲垮。
8. **welcome 块是"会退休的 header"**，不是固定面板；一旦产生输出就永久让位。

### 6.2 可简化 / 可放弃

- **append-only 稳定行协议**：omp 需要它是因为终端 scrollback 不可回滚；Textual 在 `VerticalScroll` 里可以整块重排，**直接采用"整块重渲染 + 底部粘性滚动"即可**，不要移植这套 ledger。
- **`Ctrl+O` 全局展开**：omp 是被 immutable 历史逼出来的全局开关；Textual 里**逐卡展开**体验更好（现仓库 `ToolCard` 的 Enter/space 是对的方向），但要保留一个**全局快捷键**（`Ctrl+O`）做"全部展开/收起"作为批量操作。
- **RowSplit / split 退休 / emergency row / 重放事务**：这是 diff 渲染器的性能工艺，Textual 用 widget 级 diff 就够。
- **TSP 原生渲染分支**（`describe()` / `native/*`）：整条线可以完全忽略，只取 ANSI 形态的视觉结论（本文件引用的注释大多在解释两条线的共性）。
- **61 个工具渲染器**：只做兜底 + bash / read / edit / grep/glob 五套；task/todo 若不做子代理与待办 HUD 可以退化成兜底卡。
- **旧版双栏 welcome（LSP + recent sessions）**：当前源码已无，别照截图抄。

### 6.3 Textual 落点建议（对照本仓库现状）

| omp 概念 | 现状（`zenagent/cli/tui/`） | 建议落点 |
|---|---|---|
| `TranscriptContainer`（块顺序 + 1 行间隔 + 活区） | `ChatLog(VerticalScroll)` 直接 mount | 保留 `VerticalScroll`；块间间距改由 `#chat > * { margin-top: 1 }` 统一给，删掉各 Widget 自己的空行 |
| block 生命周期（active/settled/committed） | 无 | 只需两态：`streaming`（可 update）/ `final`（冻结，避免无谓的 `refresh`）。`ChatLog.sync` 里已有"按 index 增量"的雏形，保留 |
| append-only 流式 | `Markdown.update(text)` 全文替换 | 直接保留（Textual 可重排）；但**流式期间不要 `scroll_end` 每帧**，改成"距底部 < 2 行时才跟随"（这是 omp 靠 scrollback 白得的行为，Textual 必须显式实现） |
| 工具卡三拓扑 | `ToolCard(Static)` 单行 + `▸/▾` | 升级为 `ToolCard` 基类 + `render_head()` / `render_body()` / footer 三个钩子；`framed` 用 `border: round $border`，`plain` 用 `background: $tool-*-bg`，`inline` 无框 |
| 状态头语法 | `f"{name}  {args}  ·  {status}{duration}"` | 改为 `[icon] 动词[: 目标] [badge] · meta`；目标与正文去重；加语义色（accent/muted/dim） |
| 折叠/展开 | `ToolCard.action_toggle_card`（逐卡） | 保留逐卡；新增 `Ctrl+O` 全量展开/收起（遍历 `query(ToolCard)`） |
| 折叠文案 | `▸/▾` 前缀 | 补 `(ctrl+o: Expand)`、`… N earlier lines`、`… N more items` 三种；行数上限 3/12 |
| 状态色 | 硬编码 `_TEXT/_MUTED/_ACCENT/_ERROR` | 提成 Textual design token（`$accent` `$muted` `$dim` `$success` `$warning` `$error` `$tool-bg-*`），成功态用 dim 而非绿 |
| thinking | `Collapsible(collapsed=True)` + 纯文本 | 改成单行脉冲行 + 完成后的折叠头（`✻ Thinking · …` / `Thought for Ns`） |
| 用户气泡 | `› ` 前缀 + 无底色 | 去掉 `›`，改成整行底色 block（`Static` + `background: $user-bg` + 左右 1 格 padding + 上下 1 空行） |
| usage 行 | 底部一条 `done · in/out/cache/$` | 移到"每回合末"的独立行，按 `18:02 Δ 6.2s ⤵18.4K ⤴640 …` 排序指标 |
| 分隔线（compaction/handoff） | 无 | `MessageDivider`：`──── 标签 · ctrl+o ────`，上下各 1 空行 |
| 工作行 | 无（状态在 status_bar） | 如果要做，放在 composer 上方、`paddingX=1`、braille spinner + 意图 + `esc` 停止提示；它不属于对话流 |

### 6.4 两个设计判断（为什么这么选）

1. **不要把"滚动/自动滚底"当成 omp 的设计来做**。omp 根本没有滚动管理：它把定稿行写进终端 scrollback，屏幕只负责最新一屏。Textual 是 alt-buffer 全屏应用，必须自己实现滚动，所以**唯一正确的移植是"粘性底部"（stick-to-bottom）**：内容增长时若视口在底部则跟随，用户一旦上滚就锁定位置并显示"有新内容"提示——这是**新的设计决策**，不是抄 omp。把 omp 的"append-only 稳定行"照搬进 Textual 只会得到更复杂的代码和同样的观感。
2. **逐卡展开 > 全局展开，但两者都要**。omp 的 `Ctrl+O` 全局语义是 scrollback 不可变性的妥协（已提交卡无法展开）；Textual 可以真正逐卡展开，体验更好。保留 `Ctrl+O` 作为"全部展开/收起"的批量键（也是老用户肌肉记忆），逐卡用 Enter/Space。**真正的观感来源是卡片骨架与折叠文案，不是展开的作用域。**

## 源码对照

- `packages/tui/src/chrome/transcript-container.ts` — `TranscriptContainer`：活区容量、`active/settled/committed`、`trimBlankEdges`、`RowSplit`、8ms 退休预算、`#renderEmergency`
- `packages/tui/src/chrome/chat-block.ts` — `ChatBlock`：`mount/onCleanup/finish/dispose` 生命周期，`isTranscriptBlockFinalized()`
- `packages/tui/src/render/output-block.ts` — `renderOutputBlock`：圆角框、分区栏、footer 骑下边框、状态→边框色映射
- `packages/tui/src/render/status-line.ts` — `renderStatusLine`：`[icon] 标题: 描述 [badge] · meta` 的唯一来源
- `packages/tui/src/render/render-utils.ts` — `formatStatusIcon`（`⏳⟳✔✘⚠ⓘ⏹`）、`formatExpandHint`、`capPreviewLines`（`… N earlier lines (ctrl+o: Expand)`）、`PREVIEW_LIMITS`、`expandKeyHint`
- `packages/tui/src/render/types.ts` — `State = pending|running|success|error|warning`
- `packages/tui/src/render/utils.ts` — `getStateBgColor`（`toolSuccessBg/toolErrorBg/toolPendingBg`）
- `packages/tui/src/render/tool-card.ts` — `ToolCardSnapshot` / `ToolCardPhase` / `footerMeta` / `variant: framed|plain`
- `packages/tui/src/theme/symbols.ts` — 全部字形预设（unicode/nerd/ascii）
- `packages/tui/src/theme/theme-class.ts` — `icon.*` / `status.*` / `tree.*` / `nav.*` / `checkbox.*` 访问器
- `packages/tui/src/chat/tool-execution.ts` — `ToolExecutionComponent`：三拓扑选择、`stateBgKey`、`#renderCompact`（1/2 行降级）、`elapsedMs`、`setIgnoreTight`
- `packages/tui/src/chat/chat-transcript-builder.ts` — 消息角色 → block 的总分派（含 read 组、usage 行、compaction、custom/hook）
- `packages/tui/src/chat/assistant-message.ts` — markdown `paddingX=1`、`Thinking` 脉冲 `THINKING_DOTS_FRAMES`、`Thought for 12s`、append-only `#publishStableSnapshot`
- `packages/tui/src/chat/thinking-display.ts` — `formatThinkingForDisplay`（prose-only 折叠、`<!-- -->` 噪声剔除）
- `packages/tui/src/chat/user-message.ts` — `UserMessageComponent`：`userMessageBg` 气泡、live-steer `*`、reaction、`[Image #N]` chip 折叠
- `packages/tui/src/chat/compaction-summary-message.ts` — `DividerSummary`：`──── 标签 · ctrl+o ────`、`COMPACTION_METHOD_LABELS`、展开盒子
- `packages/tui/src/chat/bash-execution.ts` / `eval-execution.ts` / `execution-shared.ts` — `$ cmd` 头、上下通栏线、`buildStatusFooter`（`(exit N)` / `(cancelled)` / `… N more lines`）
- `packages/tui/src/chat/read-tool-group.ts` — `Read (N)` 组卡、树行、per-file usage 行
- `packages/tui/src/chat/recap-notice.ts` / `stripped-tool-calls-placeholder.ts` — `※ recap:` / `N tool calls elided` 单行 dim italic
- `packages/tui/src/chrome/message-divider.ts` — 短规则线分隔（`rule + label`，上下各 1 空行）
- `packages/tui/src/chrome/transcript-notice.ts` — `NoticeTone`（info/success/warning/error）、`noticeRow`、`TranscriptNotice.line('warning'|'error')`
- `packages/tui/src/chrome/transcript-status.ts` — `TranscriptStatusBlock`：单行 pill（parts 用空格连接，indent 1）
- `packages/tui/src/chrome/error-block.ts` — `formatErrorBlock`：`  ` 续行缩进、`… +N more lines (ctrl+o to expand)`
- `packages/tui/src/chrome/message-notice.ts` / `status-notice.ts` — notice 卡（severity 反显底/圆角）与 dim 单行 notice（原生 → 2.4s toast）
- `packages/tui/src/chrome/visual-truncate.ts` — `truncateToVisualLines`（保留尾部 N 行）
- `packages/tui/src/components/disclosure.ts` — summary / collapsedBody / lazy body 的折叠组合
- `packages/tui/src/components/loader.ts` — `⠋` 10 帧、`SPINNER_ADVANCE_MS = 80`、`setTrailer`、`describeWorkingRow`
- `packages/tui/src/tools/renderer.ts` — `ToolRenderer`（`renderCall` / `renderResult` / `expanded` / `isPartial` / `spinnerFrame` / `elapsedMs` / `inline` / `activitySummary`）、`NativeToolHead`（title/target/meta/badge/note/exit 的唯一一次原则）
- `packages/tui/src/tools/default-renderer.ts` — 兜底卡：`└ args`、`Args` JSON 树、输出前 4 行、`(ctrl+o: Expand)`
- `packages/tui/src/tools/bash.ts` — `CREATE shell renderer`：`Bash · name`、`bashStatsParts`（`Wall/Timeout/Artifact/Exit`）、`capPreviewLines`
- `packages/tui/src/tools/edit.ts` — `getOperationTitle`（Edit/Create/Delete）、`formatDiffStatsSuffix`（`⟨+8/-1⟩`）、`renderDiffSection`（8 hunks/40 行折叠）
- `packages/tui/src/tools/read.ts` — `Read <path>:13-36`、`renderStatusLine`、`framedToolCard`、truncation warning
- `packages/tui/src/tools/grep.ts` / `glob.ts` / `find.ts` — `icon.search` 作状态图标、`inline: true`、`N matches · N files` meta
- `packages/tui/src/tools/task.ts` / `agent-tree.ts` — `formatAgentStatRun`（`· 12 🛠 · 2 req · 12K · $0.01`）、`nestedMarkers`（树线）、`formatBadge`
- `packages/tui/src/tools/todo.ts` — `☑/☐`、删除线揭示动画、`Todo closed/total`
- `packages/tui/src/tools/output-meta.ts` — `formatTruncationMetaNotice`（`⟨Showing lines …⟩`）、`stripOutputNotice`
- `packages/tui/src/overlays/usage-row.ts` — `TurnUsageTally`、`formatUsageRow`（`18:02 Δ 6.2s ⤵ in ⤴ out 💾 cache ⏱ ttft ⚡ rate`）
- `packages/tui/src/prompt/welcome.ts` — `WelcomeComponent`：logo 锁排（12 列块状 + 4 列 gap）、渐变 + shine、`TIP_MIN_COLUMNS = 50`、`pickWeightedTip`
- `packages/tui/src/prompt/composer.ts` — `#offerHistory`（header 先退休、`#headerRetired`）、`#reflowRetiredHeader`
- `packages/coding-agent/src/modes/utils/ui-helpers.ts` — `addMessageToChat` 的角色分派表（行类型的生产者视图）
- `packages/coding-agent/src/modes/controllers/input-controller.ts` — `toggleToolOutputExpansion` / `setToolsExpanded` / `toggleToolActivityVisibility`（全局展开语义）
- `packages/coding-agent/src/modes/interactive-mode.ts` — `ensureLoadingAnimation`（工作行）、`#workingRowSpec`、`setRuntimeChildren` 顺序
- `packages/coding-agent/src/modes/settings.ts` — `tui.resizeScrollback`（append/rebuild/preserve）、`tui.completeScrollback`
- `docs/tui-core-renderer.md` — 历史/视口契约、"renderer never probes scroll position"、append-only 稳定行契约
- `docs/theme.md` — 颜色 token 全集（`userMessageBg`/`toolPendingBg`/`toolSuccessBg`/`toolErrorBg`/`toolTitle`/`toolOutput`/`thinkingText`/`toolDiff*`）
