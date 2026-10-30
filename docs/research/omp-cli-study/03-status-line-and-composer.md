# 状态栏与输入区（Status Line + Composer）

> 调研对象：can1357/oh-my-pi（omp）的 TUI 设计。
> 目标：只记录**设计与交互事实**，用于用 Textual 复刻；不贴大段实现代码。

## 1. 一句话概括

omp 的底部不是"一行状态栏 + 一个输入框"，而是**一个可配置的 segment 条 + 一套 8 种可换的 composer 外形（composer shape）**：状态条既可以嵌进输入框的**上边框**（box）、作为**贴左 powerline 色带**压在输入区上方（band）、挂在**横线右端做成 chip**（claude / rule），也可以完全脱离输入框变成独立的**底部一行**（pi / borderless / field / rail）；输入框本身由"外形对象"统管上下 chrome、每行左右 chrome、gutter 与内边距，上层只决定状态栏挂在哪。

## 2. 结构与层级

```
终端屏幕（从上到下）
├─ 对话流 / 覆盖层（不属于本文件）
└─ dock（输入区及紧邻它的行，自下而上是一个固定顺序的栈）
   ├─ hookWidgetContainerBelow      ← 扩展 hook 挂在输入框下方的行
   ├─ editorContainer               ← composer 本体（输入行 + 自己的上下 chrome）
   │   ├─ [box] 上边框 = ╭─ <左 group> <context 刻度线> <右 group> ─╮
   │   ├─ 内容行 × N（1..maxHeight，逐行增高）
   │   └─ [box] 末行下边框并入内容行：╰─ text ───────╯
   ├─ hookWidgetContainerAbove
   ├─ attachmentChipsContainer      ← 附件 chip 卡片带（在输入框正上方）
   ├─ statusContainer               ← "工作行"（spinner/耗时/意图/停止键）+ retry 提示行
   ├─ pendingMessagesContainer      ← 排队消息 band（Steering / After yield）
   ├─ progressHudContainer          ← 进度 HUD
   ├─ deferredCommandContainer      ← 延迟命令输出
   ├─ modelCycleContainer           ← 模型切换提示
   ├─ errorBannerContainer          ← 固定错误条
   ├─ todoContainer                 ← 待办 HUD（自清除）
   ├─ subagentContainer             ← 子 agent HUD（自清除）
   ├─ chatContainer                 ← 对话流
   └─ （以上为 ANSI 顺序；TSP/原生终端另有 nativeDock 顺序，见 §3.9）
   
另有 "HUD pills row"（原生终端最上面的一条右对齐小胶囊：agents / jobs 计数），仅在 nativeDock 里出现。
```

状态栏本身的两组：

```
status line（一行，宽 = 终端宽）
├─ 左 group：按配置顺序把可见 segment 用分隔符串起来，前后各 1 空格，左侧 end cap
├─ 中间 gap：box/band 画"context 刻度线"（用上下文占用率上色的横线）；plain 布局只填空格
└─ 右 group：同上，右侧 end cap
   （宽度不够时按 §3.5 的顺序丢段/截断）
```

## 3. 布局与视觉规则

### 3.1 segment 装配与顺序

- 有效配置来自 `statusLine.preset` 或 `leftSegments` / `rightSegments` 覆盖（`status-line/schema.ts` 的 `STATUS_LINE_SEGMENT_IDS`，27 个 id）。
- 渲染时先按配置顺序逐段 `renderSegment()`，**只保留 `visible && content` 非空的段**（`status-line/component.ts` 的 `#renderStatusLine`）。所以"段在配置里"和"段在屏幕上"是两件事：空段直接不占位、不留分隔符。
- 左 group 渲染顺序 = 配置顺序；右 group 同样，但整体右对齐贴终端右边。
- 两个**非配置段**会被动态 unshift 到右 group 的**最前面**（即右 group 靠内侧）：
  - `subagent-badge`：有运行中子 agent 时，`agents` 图标 + 数量（此时配置里的 `subagents` 段被跳过，避免重复）。
  - `jobs`：有后台任务时，`job` 图标 + 数量，后面可再跟最新 job widget 的 `formatJobWidgetGlance()` 8 格 sparkline。
- band 布局下 `session_name` 段被移出状态条（标题改到"工作行"的右侧 trailer），因为它要把整行留给色带。

### 3.2 分隔符

`status-line/separators.ts` 的 `getSeparator()`，7 种风格 + 布局强制覆盖：

| 风格 | 左/右字形 | 端帽 | 说明 |
|---|---|---|---|
| `powerline` | 实心三角 | 有，`useBgAsFg` | 需要不透明背景，最"重" |
| `powerline-thin` | 细三角（默认） | 有，`useBgAsFg` | 默认 preset 用它 |
| `slash` | `/` | 无 | |
| `pipe` | `│` | 无 | |
| `block` | 方块 | 无 | |
| `none` | 空格 | 无 | 只剩内边距 |
| `ascii` | ASCII 箭头 | 无 | 无 Nerd/Unicode 依赖 |

- **plain 布局（独立底部条）无视配置，固定用 `PLAIN_SEPARATOR = ·`**（`component.ts` 常量），且不画端帽。
- `transparent: true` 或 `statusLineBg` 为空时**强制丢弃端帽**（端帽需要一个对比填充才能"接住"色带，否则是游离字符）。
- 组内布局固定为：` ${part1} ${sep} ${part2} ... ` —— 段与段之间是"空格 + 分隔符 + 空格"，组两侧各 1 空格。宽度计算 `groupWidth()`：`Σ段宽 + (n-1)*(sep宽+2) + 2 + cap宽`。
- **段内部**另有一层浅分隔符 `theme.sep.dot`（`·`）：用在 `model · <thinking>`、`usage` 的多窗口之间、`status` 的多条 hook 状态之间。

### 3.3 颜色语义

- 13 个命名 token（`theme.md` 明确列出）：`statusLineSep`、`statusLineModel`、`statusLinePath`、`statusLineGitClean`、`statusLineGitDirty`、`statusLineContext`、`statusLineSpend`、`statusLineStaged`、`statusLineDirty`、`statusLineUntracked`、`statusLineOutput`、`statusLineCost`、`statusLineSubagents`。
- 语义色 `accent` / `muted` / `dim` / `warning` / `error` / `success` 与上面并列使用；**session accent**（`statusLine.sessionAccent`，默认开）会用会话名的 hash 派生一个色相，只涂"身份类"文本（pi 图标、model 名、PR 链接、mode 徽标、会话标题、hostname），状态类颜色不受影响。
- 上下文占用率是**四档语义色**（`chrome/context-thresholds.ts`）：normal → `statusLineContext`，warning（≥50% 或 ≥150K）→ `warning`，purple（≥70% 或 ≥270K）→ `thinkingHigh`，error（≥90% 或 ≥500K）→ `error`。阈值取"百分比阈值与 token 阈值折算百分比中的较小者"。
- git 段用 dirty/clean 两个整段色，内部三个计数各用 `statusLineDirty` / `statusLineStaged` / `statusLineUntracked`。
- `pi` 段品牌色在 idle 的 dim 与 working 的 accent 之间做 450ms tween（40ms 一帧）。

### 3.4 中间 gap：context 刻度线（box / band 独有）

`statusLine.contextLine` 四档（`#buildContextGaugeFill`）：

| 值 | 画法 |
|---|---|
| `off` | 整条 accent 实线，不反映占用 |
| `percentage` | 已用量涂 accent，剩余用边框色 |
| `annotated` | 再加两个标记：推测压缩起点（`context.speculation` 符号，muted）、自动压缩触发点（`context.compaction` 符号，accent 降饱和色）；线宽 < 8 格不画标记 |
| `embedded`（默认） | 在 `annotated` 基础上把 `context_pct` / `context_total` 段**吸收进 gap**：右侧写 `200K`（accent 降饱和），再右写百分比；两个 context 段从两侧 group 中移除 |

细节规则：
- 至少亮 1 格（新会话不满 1% 也有一小截 accent，而不是整条暗）。
- >100% 时：线满格，百分比标签**越过**窗口标签，且用 `error` 色（`──200K─120%`）。
- 百分比标签优先放"已用量右端 +1"，并与标记位置避让（左右交替找空位）。
- 长度不够容纳标签时退化为无标签刻度线；只有 1 个段存活时退化为 1 格 gap，而不是把整条状态栏丢掉。
- 一格一块的渲染会被合并成"连续区段"输出（每个颜色变化才换 escape），这是渲染性能处理，不是设计事实。

### 3.5 宽度不足时的降级顺序（最重要的一条布局规则）

`#renderStatusLine` 内的顺序是**硬编码**的，依次尝试：

1. **截断 `session_name`**：右 group 里唯一的弹性段，先把它截到最小 8 格。
2. **从右 group 尾部整段丢弃**：`while (total > width) right.pop()` —— 即右 group 最右边（最靠外）的那段最先消失，`session_name` 是最后被丢的。
3. **压缩 `path` 段**：把 `path.maxLength` 从当前值往下压，最小 8 格（"图标 + 省略号 + 几个字符"），最多迭代 8 次直到刚好够。
4. **丢左 group**：`leftOverflowDropIndex()` 从数组尾部往前找第一个**不是 `path`** 的段，丢掉它。也就是左 group 从**靠内**（紧邻 gap 的那侧）往外丢，**`path` 永远最后丢**（"保住当前工作目录"）。

原生（TSP）终端的规则不同，用的是**声明式优先级**（`component.ts`）：
- `statusSegmentPriority(side, index, count)`：左 = `count - index`（越靠左越重），右 = `index + 1`（越靠右越重）——**两端边缘最重要**。
- `PINNED_NATIVE_SEGMENTS` 把 `model` / `context_pct` / `context_total` / `git` 顶到 `PINNED_NATIVE_PRIORITY = 1000`，即"窄到极限也留这四段"。
- `path` 段带 `min: { w: "12ch" }`，即"先截断、后丢弃"。
- 注意：ANSI 条与原生条在"右 group 谁先丢"上口径相反（ANSI 丢最右，原生保最右）。复刻时需二选一——**建议采用原生口径**（边缘优先 + 四段 pin），因为它有显式的设计注释，且与"状态栏两端是锚点"的视觉意图一致。

### 3.6 额外行：hook 状态

- 配置 `showHookStatus`（默认开）时，扩展/钩子注册的状态文本按 key 字典序排序，**排在状态栏下方各占一行**（不是塞进段里）。ANSI 实现是 `render()` 里追加 `lines.push(...)`；原生实现是 `col([bar, ...hookLines])`。
- 段级 `status` 段是另一回事：那是把 hook 状态用 `·` 串成**一段**（accent 色），默认 preset 不含它。

### 3.7 composer 外形（8 种）

`components/composer/types.ts` 的 `ComposerStyle` 契约 + `registry.ts` 注册表。核心字段：`sideBorders`（内容行有没有左右边框字形）、`verticalChrome`（上下 chrome 占几行）、`statusAttachment`（状态栏挂哪）、`bottomBar`（独立底栏显示哪组）、`bottomBarGap`、`defaultPromptGutter`、`defaultPaddingX`、`sideChromeWidth`、`renderTop/renderRow/renderBottom`。

| shape | 上 chrome | 下 chrome | 状态栏挂点 | gutter | paddingX | 备注 |
|---|---|---|---|---|---|---|
| `box` | `╭─` + 状态栏嵌在上边框 + `╮` | **并入末内容行**（`╰─ text ─╯`） | `top-border` | 无 | 2 | 经典 omp 外形；单行输入只占 2 行 |
| `band` | 满宽 powerline 色带 + 左侧软帽 | 无 | `top-band` | `╰─ ` | 0 | rust omp 默认；无框、贴终端左边缘 |
| `claude` | 满宽横线，右 group 做 bg chip 贴右 | 满宽横线 | `top-rule-chip`（右组）+ 独立底栏（左组） | `❯ ` | 0 | Claude Code 风格 |
| `pi` | 满宽横线 | 满宽横线 | 无（独立底栏 full） | 无 | 1 | 上游 pi 风格 |
| `borderless` | 无 | 无 | 无（独立底栏 full） | `❯ ` | 0 | hook 编辑器 / agents hub 兜底；`setBorderVisible(false)` 也落到它 |
| `rule` | 一条横线带右组 chip | 无 | `top-rule-chip`（右组）+ 独立底栏（左组），且底栏上方留空行 | `❯ ` | 0 | 最省高度的"半框" |
| `field` | 无 | 无 | 无（独立底栏 full，上方留空行） | 无 | 1 | 每行是一个填充条：`▐ … ▌` accent 端帽 |
| `rail` | 无 | 无 | 无（独立底栏 full，上方留空行） | 无 | 1 | 左侧单条 accent 竖轨 `▎`，右侧填充 |

- `renderTopRule()`（`rule.ts`，claude 复用）：chip 宽度 > 宽-2 时**截断而不是丢弃**（"保留左右各一格横线，chip 在窄终端/预览里也能活"）。
- `box` 的末行会把下边框并入内容行；光标溢出到右侧 chrome 时按溢出格数先吃 padding 再吃 `─`，**绝不吃掉角或竖线**。
- `box` 右侧边框在滚动时把细竖线换成 `█` 拇指，充当滚动条。
- `verticalChrome` 决定 `maxHeight` 预算：box 是 2，band/rule 是 1，field/rail/borderless 是 0。

### 3.8 输入区自身

- **高度**：单行起，随内容增长。上限由 `interactive-mode.ts` 的 `computeEditorMaxHeight(rows)` 给出：`clamp(rows - 12, 6, 18)`，再夹在 `[3, rows - 4]` 之间——**6~18 行是常规带，3 行是地板（box 上下边框 + 1 内容行），至少给对话流+状态栏留 4 行**。
- **placeholder**：`editor.ts` 描述为"**在光标行右对齐**绘制，仅在编辑器为空且无 autocomplete 时显示，且必须与光标保持 `PLACEHOLDER_MIN_GAP = 2` 格，否则不显示"。内容是**轮换的 tip**（`prompt/tips.txt` + `welcome.ts` 的 key 占位符展开），不是静态文案。原生终端用固定短句 `"What are we cooking?"`（`custom-editor.ts` 的 `NATIVE_COMPOSER_PLACEHOLDER`），有会话标题时改成斜体引号标题 `“<title>”`。
- **光标**：软件光标；行末是细条 `▏`（ascii 预设为 `|`），Vim Normal 用方块，Visual 高亮选区。可选用终端硬件光标（`showHardwareCursor`），并支持 `imeSafeCursor`：末行右侧留空，避免输入法预编辑串行移动边框。
- **附件 chip**：不需要打开面板——**输入框正上方一条 chip 卡带**（`prompt/attachment-chips.ts`）。每张卡是 12×4 内容区 + 1 格圆角边框；图片/视频显示实时缩略图，底部 caption 是像素尺寸；文本粘贴显示前 4 行 snippet，caption 是 `+N lines` 或 `N chars`；顶部 caption 是与缓冲区里同色的 `<icon> #N` token。**放不下的卡片直接不画（不换行）**。
- **autocomplete 弹层**：渲染在**编辑器行之后（下方）**，和输入框属于同一个组件输出。可见行数 = `max(3, min(autocompleteMaxVisible(默认 10), 终端行数 - 编辑器行数 - 2))`。独立底部状态栏在 autocomplete 打开时**让出自己那一行**（`#autocompleteActiveProbe`）。
- **bash / python 模式**：由草稿前缀触发——`!` → bash，`!!` → bash 且不进上下文；`$ ` → Python，`$$ ` → Python 且不进上下文（`$` 后必须跟空白才算，避免吃掉 `$HOME` 这类散文）。原生终端在输入行左侧显示 `bash` / `python` 模式 chip（accent 色，`bashMode` / `pythonMode` 两个 token），`!!` / `$$` 再补一个 `eye-off` 图标"不发给模型"。ANSI 终端不显示 chip，**改用整个边框颜色**表达。
- **Vim 模式**：软件光标形状 + 边框颜色 + 状态栏 `vim` 段三处同时表达。

### 3.9 边框颜色的优先级链（`updateEditorBorderColor()`）

这一条决定了输入区"看起来什么状态"，顺序不可换：

1. bash 模式 → `theme.bashMode`
2. Python 模式 → `theme.pythonMode`（**shell 模式优先于 Vim 模式**）
3. Vim visual / visual-line → `warning`
4. Vim normal / replace → `accent`
5. Vim insert → `success`（"给 Insert 单独一个色，否则 Normal/Visual 都有色时 Insert 读不出模式"）
6. 无 Vim 时：session accent（会话名 hash 色）+ 否则 → 当前 **thinking level** 对应色（`thinkingOff/Minimal/Low/Medium/High/Xhigh[/Max]`，共 8 个 token）
7. 正在看子 agent（focus-proxied）→ 在以上结果外面再套一层 `\x1b[2m` 变暗

### 3.10 输入区上方的 transient 行

顺序（`interactive-mode.ts` 的 `setRuntimeChildren({ nativeDock })` 与注释）：

```
HUD pills（右对齐小胶囊：agents 计数 / jobs 计数，均为 0 时整行隐藏）
btw / omfg / cleanse / 错误条 / 模型切换 / 延迟命令 / 进度 HUD
工作行（statusContainer）        ← spinner·耗时·意图·停止键
排队消息（pendingMessages）       ← 你原话："queued messages sit between the working row and the composer"
hook widget（上）
composer
hook widget（下）
```

- **工作行**（`components/loader.ts` 的 `describeWorkingRow` + `interactive-mode.ts` 的 `#workingRowSpec`）：
  - 原生：`<starburst spinner> <elapsed> · <意图 shimmer> <弹性空白> <esc> Stop`；retry 变体把 spinner 换成倒计时环、按钮文案变 `Cancel`；compact 变体在 Stop 前多一条不确定进度条。
  - ANSI：**不画第二个 spinner**（品牌 spinner 已经在状态栏 `pi` 段里），这一行**以中断键帽起头**：`⎋ Working…`，右侧 trailer 停靠 `x.x tok/s` +（band 模式下）会话标题。行首多一个空格让键帽比下面贴左的状态行缩进一列。
  - 出现时机：`ensureLoadingAnimation()` 在 turn 开始/loading 时挂载，turn 结束 `#stopLoadingAnimation()` 卸载。
  - idle 时如果 trailer 还在，会渲染一条 `renderIdleStatusHud()`：空行 + 右对齐 trailer（tok/s + 标题），保持"同一位置读数不跳"。
- **排队消息**（`prompt/queued-messages.ts` 的 `QueuedMessagesBand`）：上方先空 1 行；每组一个标题 `<label>·<count>`（label 为 `Steering` / `After yield`）；每条一行 `  1. <text>`（dim，换行折成 ` ↵ `）；最后一行 `  └ <dequeue key> to edit`。原生形态是"胶囊栈"：`corner-down-right` 图标 + 文本 + `⌥↑ Edit`，多于 1 条时第一条带 `N queued` 徽标（**是总数不是序号**）。
- **提示条（hint）**：空 composer 的**右对齐**提示，`prompt/composer-hints.ts` 的 `COMPOSER_HINTS`，**按优先级取第一条命中的**，同一手势用满 `learnedAfter = 3` 次后**永久退役**（计数持久化，id 不可改名）：
  - `agents`：有后台 agent 且不在子会话视图 → `←← to see N running agents`
  - `effort`：会话还没开始 → `<thinking key> to change thinking effort`
  - 样式：键 accent 色，描述 dim + italic。
- **retry 提示行**：`#retryHintRow`，失败后挂在 `statusContainer` 里（和工作行互斥：工作行在时不挂）。
- 覆盖层自己的提示行是另一套（如 `↑/↓: navigate  Space: toggle  Tab: next provider  Esc: close`），由各 overlay 自带，不属于 composer。

## 4. 交互契约

### 4.1 进入 / 退出

- composer 常驻，无需"进入"；`Esc` 在草稿非空时中断当前 turn，草稿为空时聚焦到子 agent 视图会退回主会话。
- `Ctrl+C`：清空未发送草稿（进可召回历史）→ 空时第二次 `Ctrl+C` 退出。`Ctrl+D` 退出。
- `/settings` 的 Interaction → Composer / Status Line 分组切换 shape、preset、分隔符、transparent、compactThinking、contextLine；`/composer` 类覆盖层提供 shape 实时预览（`overlays/composer-shape-preview.ts`）。**预览与真身共用同一个 style 对象与同一条状态栏渲染管线，因此不会漂移。**

### 4.2 焦点与输入路由

- 焦点始终在 editor；`Esc` 的第一优先级是**关掉 autocomplete 弹层**（而不是中断 turn），弹层关掉后才轮到中断语义。
- 点击路由：状态栏 `model` / `context_*` / `git` / `cost` / `path` 段分别触发 `status.model` / `status.context` / `status.git` / `status.cost` / `status.path`（`NATIVE_SEGMENT_ACTIONS`）；composer 的 model chip → `status.model`，effort chip → `thinking.cycle`，send → `submit`，Stop → `interrupt`，mic → `dictation.toggle`，排队消息的 Edit → `queue.edit`。
- 双击 `←`（空编辑器）打开 Agent Hub；聚焦子 agent 时双击 `←` 回父级。

### 4.3 键位表

编辑/输入键（`tui.*`，`keybindings.ts`）：

| 动作 | 默认键 | 说明 |
|---|---|---|
| `tui.input.submit` | `Enter` | 提交 |
| `tui.input.newLine` | `Shift+Enter`、`Ctrl+J` | 换行（`Ctrl+J` 是终端不支持 Shift+Enter 时的兜底） |
| `tui.input.tab` | `Tab` | 触发/接受 autocomplete |
| `tui.input.copy` | `Ctrl+C` | 有选区时复制（不是清空） |
| `tui.editor.undo` | `Ctrl+-`、`Ctrl+_` | |
| `tui.editor.yank` / `yankPop` | `Ctrl+Y` / `Alt+Y` | kill-ring |
| `tui.editor.spellingSuggestions` | 见源码 | 拼写候选 |
| 光标/删词/翻页 | 方向键、`Alt+←/→`、`Ctrl+Home/End`、`PageUp/Down` | |

应用键（`app.*`，`app-keybindings.ts` / `keybindings.md`）：

| 动作 | 默认键 | 说明 |
|---|---|---|
| `app.interrupt` | `Esc` | 中断；空编辑器+子会话视图时回主会话 |
| `app.clear` | `Ctrl+C` | 清空草稿（可召回） |
| `app.exit` | `Ctrl+D` | 退出 |
| `app.suspend` | `Ctrl+Z` | 挂起 |
| `app.display.reset` | `Alt+L` | 重置终端显示 |
| `app.thinking.cycle` | `Shift+Tab` | 循环 thinking 等级 |
| `app.model.cycleForward` / `cycleBackward` | `Ctrl+P` / `Shift+Ctrl+P` | 循环角色模型 |
| `app.model.select` / `selectTemporary` | `Alt+M` / `Alt+P` | 选模型 / 临时选模型 |
| `app.message.followUp` | `Ctrl+Enter`（Windows 终端另加 `Ctrl+Q`） | 把当前草稿**排队**为 follow-up |
| `app.message.dequeue` | `Alt+Up`、`Shift+Up` | 把排队消息取回编辑器 |
| `app.editor.external` | `Ctrl+G` | 用 `$VISUAL`/`$EDITOR` 编辑草稿 |
| `app.local.open` | `Alt+G` | 选 session `local://` 文件用外部编辑器改 |
| `app.retry` | `F5`、`Alt+R` | 重试上一个失败的助手回合 |
| `app.clipboard.copyLine` / `copyPrompt` | `Alt+Shift+L` / `Alt+Shift+C` | 复制当前行 / 整个 prompt |
| `app.clipboard.pasteTextRaw` | `Ctrl+Shift+V`、`Alt+Shift+V` | 粘贴但不折叠成 chip |
| `app.clipboard.pasteImage` | `Ctrl+V`（macOS 另加 `Cmd+V`；Windows 另加 `Alt+V`） | 优先图片，否则文本 |
| `app.stt.pushToTalk` | `Space`（**按住说话**） | 置空数组即禁用；可另配 `app.stt.toggle` |
| `app.live.toggle` | `Ctrl+L` | 实时语音模式 |
| `app.history.search` | `Ctrl+R` | 搜索 prompt 历史 |
| `app.agents.hub` | `Alt+A` | Agent Hub |
| `app.links.hint` | `Alt+O` | 给屏上链接打标签，输入标签跳转 |
| `app.tools.expand` / `toggleVisibility` | `Ctrl+O` / `Ctrl+Shift+O` | 展开工具输出 / 显隐工具活动 |
| `app.plan.toggle` | `Alt+Shift+P` | plan 模式 |
| `app.approval.approve` | `Ctrl+Enter`、`Ctrl+Q` | 批准待确认的工具调用（`Esc` 拒绝） |
| `app.ask.hide` | `Alt+H` | 把 ask 问题压成一行 |

**私有语法（不是键位但同样重要）**：

| 前缀 | 含义 |
|---|---|
| `!` / `!!` | 作为 bash 运行 / 运行且输出不进上下文 |
| `$ ` / `$$ ` | 作为 Python 运行 / 运行且输出不进上下文 |
| `->` / `=>` | 排队简写（`parseQueueShorthand()`，把后面内容作为队列消息） |
| `#` | 行内快速动作菜单 |
| `/` | 斜杠命令 |
| `@` / `^` | 文件 mention / 模型 mention |

### 4.4 状态机

| 状态 | 状态栏 | 输入区 | transient 行 |
|---|---|---|---|
| idle（空草稿） | 正常段 + 会话名；`pi` 段静止 | 边框 = session accent / thinking 色；右对齐轮换 tip | 只有 idle trailer（tok/s + 标题），无工作行 |
| composing（有草稿） | 同上；`time_spent` 不动 | 边框同上；有 `!`/`$ ` 时切 bash/python 色 | 同上 |
| working（turn 运行中） | `pi` 段变 braille spinner + turn 计时；context 刻度线随占用走动 | 原生：send 键帽变 `Stop`（error tone） | 工作行出现（spinner·耗时·意图·Stop）；排队消息 band 若有多条 |
| streaming | 同上；`token_rate` 每秒刷新；`context_pct` 增长 | 同上 | 工作行 trailer 显示 tok/s |
| compacting | `context_pct` 的 `auto` 图标脉冲（running）/ 常亮 accent（armed） | 正常 | 工作行的 compaction 变体：进度条 + "Compacting context…" |
| retrying | 正常 | 正常 | 工作行 retry 变体：倒计时环 + "Retrying · attempt 1 of 3" + `Cancel` |
| vim normal / visual | `vim` 段显示模式 + 待输入操作 + `nL` 选区行数 | 边框换色（accent / warning），光标变方块 | 正常 |
| bash / python | 不含（无对应段） | 边框换色；原生另有模式 chip | 正常 |

## 5. 完整清单

### 5.1 状态栏 segment 全清单（27 个可配置 id + 2 个动态段）

数据来源统一为 `status-line/types.ts` 的 `SegmentContext` 字段；"隐藏条件"均为"返回 `visible: false`，即完全消失、不留分隔符"。

| id | 显示什么 | 数据来源 | 隐藏/降级条件 | 语义色 |
|---|---|---|---|---|
| `pi` | `π` 品牌字（可带 `-<profile>`）；turn 中变 braille spinner + `12s`/`3m`/`2h`（上限 99h）；聚焦子 agent 时变 `👻 <agentId>` | `theme.icon.omp`、`getActiveProfile()`、`turnElapsedMs`、`focusedAgentId` | 永不隐藏 | dim→accent tween；子 agent 视图 = warning |
| `status` | 多条 hook 状态用 `·` 串成一段 | `hookStatuses`（按 key 排序、`sanitizeStatusText` 净化） | 全为空时隐藏 | accent / session accent |
| `model` | `<icon> <name>` + advisor 徽标 + fast 图标 + `· <thinking>` + `· <slow-mode 文案>` | `session.state.model`、`thinkingLevelWord()`、`isFastModeActive()`、`getAnthropicSlowModeLabel()` | 永不隐藏；`showThinkingLevel: false` 去掉 thinking 尾巴；`compactThinkingLevel` 把 thinking 字形提为 model 图标 | `statusLineModel` / session accent；advisor 按最差状态 success/warning/error/dim；slow-mode 用 warning |
| `mode` | 按优先级取**第一个**命中的模式徽标：`Plan`(`(paused)`)、`Prewalk`、`Goal`(+`used/budget`)、`Vibe`、`Loop: <state> <limit> <condition>` | `planMode`/`prewalk`/`goalMode`/`vibeMode`/`loopMode` | 全部为空时隐藏 | accent；paused = warning + pause 图标；goal complete = success；dropped = dim |
| `path` | `<icon> <路径>`，可加 ` ↳ <repo>`（嵌套仓库）；worktree 场景塌缩成 `project` 或 `project/worktree` | `getProjectDir()`、`activeRepo`、`worktree` | 永不隐藏（只是会被截断/压缩） | `statusLinePath`；原生把父目录画 dim、叶子画 strong |
| `git` | `<branch icon> <branch>` + `*n`(unstaged) `+n`(staged) `?n`(untracked) | `ctx.git`（`git status` TTL 10s，HEAD 变动立即刷新） | 既无 branch 又无 status 时隐藏；各计数按 `showStaged/Unstaged/Untracked` 选项 | dirty 用 `statusLineGitDirty`、clean 用 `statusLineGitClean`；计数各用 `statusLineDirty/Staged/Untracked` |
| `pr` | `<pr icon> #123`，带 OSC8 hyperlink | `git.pr` | 无 PR 时隐藏 | accent / session accent |
| `subagents` | `<agents icon> <count>` | `subagentCount` | `count === 0` 时隐藏；若动态 badge 已在右组则本段跳过 | `statusLineSubagents` |
| `token_in` | `<input icon> <n>` | `usageStats.input` | 0 时隐藏 | `statusLineSpend` |
| `token_out` | `<output icon> <n>` | `usageStats.output` | 0 时隐藏 | `statusLineOutput` |
| `token_total` | 总 token（**不含 cacheRead**：它每轮重读整个上下文，累计会变成 N×窗口） | `input+output+cacheWrite+orchestrationInput+orchestrationOutput` | 0 时隐藏 | `statusLineSpend` |
| `token_rate` | `x.x tok/s` | `usageStats.tokensPerSecond` | 0/null 时隐藏 | `statusLineOutput` |
| `cost` | 计费摘要：`$1.23` 或订阅 mark + `(+0.45)` 子 agent 树 + `↑/↓` 峰谷价 + `★ 12` 高级请求 + `+ (adv)` advisor 花费 | `usageStats.cost/subagentCost/premiumRequests`、`subagentTreeCost`、`getAdvisorCost()`、`getTimeBasedPricingPeriod()` | 什么都没有时隐藏 | `statusLineCost` |
| `context_pct` | `42.3%/200K`（窗口未知写 `?`）；后面可跟 `auto` 图标 | `getContextUsage()` + `formatContextUsage()` | 永不隐藏；窗口未知写 `<tokens>/?`；启动预绘时 pct 为 null 只显示窗口数字 | 四档阈值色（见 §3.3）；`auto` 图标 running 时脉冲、armed 时常亮 accent |
| `context_total` | `<context icon> <窗口大小>` | `contextWindow` | 窗口为 0 时隐藏 | `statusLineContext` |
| `time_spent` | `<time icon> <duration>`：**agent 实际工作时间**（所有 `agent_start→agent_end` 窗口的并集，idle 墙钟不计） | `activeMs` | `< 1000ms` 时隐藏（避免开局闪 `0s`） | 默认 |
| `time` | 时钟 `14:30`（可选 12h / 带秒） | `now` + `segmentOptions.time` | 永不隐藏 | 默认 |
| `session` | 会话 id 前 8 位，无会话时 `new` | `getSessionId()` | 永不隐藏 | 默认 |
| `hostname` | 短主机名（首个 label） | `os.hostname()`（进程内只取一次） | 永不隐藏 | session accent（有则）否则默认 |
| `cache_read` | `<cache icon> <n>` | `usageStats.cacheRead` | 0 时隐藏 | `statusLineSpend` |
| `cache_write` | `<cache icon> <n>` | `usageStats.cacheWrite` | 0 时隐藏 | `statusLineOutput` |
| `cache_hit` | `<cache icon> xx.xx%`，命中率 = `cacheRead/(cacheRead+cacheWrite+input)` | `usageStats` | `cacheRead === 0` 时隐藏 | `statusLineSpend` |
| `session_name` | 会话标题 | `getSessionName()` | 未命名时隐藏（此时右组常为空） | accent / session accent |
| `usage` | 提供商配额：`<tier>` + `5h 42% (12m)` + `1d` + `7d` + `mo` + 重置点数 `✦ 3 (2 usable) ▲ 1 exp 5h` | `fetchUsageReports()`（后台刷新，2s 超时） | 所有窗口与点数都为空时隐藏 | 百分比 <50 muted、≥50 warning、≥80 error；重置点数 redeemable>0 success，否则 warning；即将过期 imminent=error / soon=warning |
| `collab` | `⇄ collab:N`（host）/ `⇄ collab guest:N` | `CollabStatus` | 非协作会话时隐藏 | accent |
| `stream` | `● LIVE <viewers>` 和/或 `● REC` | `stream`、`recording` | 两者都无时隐藏 | `thinkingHigh`（原生 tone = error） |
| `vim` | `INSERT`/`NORMAL`/`VISUAL`/`V-LINE`/`REPLACE`，+ `nL` 选区行数，+ 待输入操作（showcmd） | `tui.vimMode` 的 vim 状态 | `vimMode` 关闭 或 `vimModeDisplay: "none"` 时隐藏 | insert=success、normal=accent、visual/visual-line=warning、replace=accent；pending 为 muted |
| `subagent-badge`（动态，非配置） | `<agents icon> N` | `subagentCount > 0` | 无子 agent 时不存在 | `statusLineSubagents` |
| `jobs`（动态，非配置） | `<job icon> N` + 最新 job widget 的 8 格 sparkline 摘要 | `runningBackgroundJobCount()`、`getRunningJobWidgets()` | 无后台任务时不存在 | `statusLineSubagents` |

### 5.2 preset 清单（`status-line/presets.ts`）

| preset | 左组 | 右组 | 分隔符 | 关键选项 |
|---|---|---|---|---|
| `default` | pi, vim, model, mode, collab, stream, path, git, pr, context_pct, cost | session_name | powerline-thin | path 缩写 maxLen 40；git 全计数；model 显示 thinking |
| `minimal` | vim, path, git | session_name, mode, context_pct | slash | path maxLen 30；git 只留 branch |
| `compact` | vim, model, mode, git, pr | session_name, cost, context_pct | powerline-thin | 不显示 thinking；git 无 untracked |
| `full` | pi, vim, hostname, model, mode, path, git, pr, subagents | session_name, cache_hit, token_in, token_out, token_rate, cache_read, cost, context_pct, time_spent, time | powerline | path maxLen 50；24h 时钟 |
| `nerd` | pi, vim, hostname, model, mode, path, git, pr, session, subagents | session_name, token_in, token_out, cache_read, cache_write, token_rate, cost, context_pct, context_total, time_spent, time | powerline | 全 Nerd 图标；path maxLen 60；带秒 |
| `ascii` | vim, model, mode, path, git, pr | session_name, token_total, cost, context_pct | ascii | 无 Nerd 依赖 |
| `custom` | vim, model, mode, path, git, pr | session_name, token_total, cost, context_pct | powerline-thin | 用户覆盖 |

### 5.3 transient / 辅助行清单

| 名称 | 作用 | 触发 | 是否模态/全屏 |
|---|---|---|---|
| 工作行 `omp.working` | spinner·耗时·意图·Stop | turn 运行 / 压缩 / 重试 | 否，1 行 |
| retry 提示行 | 重试入口 | 上一回合失败且工作行不在 | 否，1 行 |
| idle trailer | 保持 tok/s + 标题读数不跳位 | turn 结束后仍有 trailer | 否，1 行（内容右对齐，行本身是空行） |
| 排队消息 band | `Steering` / `After yield` 分组 + 编号行 + dequeue 提示 | 有排队消息 | 否，N+2 行 |
| 附件 chip 带 | 图片/粘贴预览卡 | 草稿里有附件 | 否，1 行（放不下的卡不画） |
| HUD pills | agents / jobs 计数胶囊 | 有运行中 agent 或 job | 否，1 行，右对齐 |
| composer hint | 教手势，3 次后退役 | 空编辑器且条件命中 | 否，与 placeholder 同一行（右对齐） |
| hook 状态行 | 扩展状态文本 | `hookStatuses` 非空且 `showHookStatus` | 否，每条 1 行 |
| autocomplete 弹层 | 补全候选 | 输入触发（`/`、`@`、路径、Tab） | 否，但会**顶掉**独立底部状态栏那行 |
| composer shape 预览 | `/settings` 里实时看 8 种外形 | 打开外形选择 | 否，内嵌预览面板 |

## 6. 复刻判断（给 Textual）

### 6.1 值得复刻（高价值，按性价比排序）

1. **segment 是可配置的一段段，而不是硬编码字符串**。当前 `StatusBar._format()` 把"model / thinking / tokens / cost / context / queued / steering"写死在一处；建议改成 `dataclass Segment(id, render(ctx) -> str|None)` + 有序 id 列表（`left`/`right`）+ 三个 preset（`default`/`minimal`/`compact`）。这是后续所有能力的地基。
2. **"不可见即不占位"**：每段返回 `None` 就完全消失，分隔符也不会留下。成本极低，视觉收益很大（不会出现 `· · ·`）。
3. **宽度不足的降级顺序**（§3.5）：先截 `session_name` → 丢右组 → 压 `path` → 丢左组但保 `path` → 四段 pin（model/context/git/pr）。Textual 里可以用 `widget.size.width` 在 `render()` 里做，或用 `on_resize` 预算一次。
4. **box 外形的"末行并入下边框"**：单行输入时 `╰─ text ──╯`，视觉上把 2 行压缩成 1 行的观感，是 omp 最有辨识度的一处。Textual 的 `border` 做不到，但可以用 `render_line()` 自己画。
5. **边框颜色 = 状态**（§3.9 的 7 级优先级链）：现成的 `#prompt-input:focus { border: ... }` 只表达聚焦，把它扩成"模式色"信息量翻倍，改动却只是 CSS class + 一个 `_sync_border()`。
6. **工作行以中断键帽起头**（`⎋ Working…`）：不需要额外发明 spinner，把"怎么停"直接摆在最显眼处。
7. **排队消息的 `N queued` 用总数而非序号**，并且 `Alt+Up` 取回编辑器——这条交互比"只读队列"有用得多。
8. **hint 3 次退役**：一个很小的反噪音机制，实现成本 3 行（用计数器 + settings 持久化）。
9. **8 种 composer shape 里只需要 3 种**（见 6.2），但**必须保留"shape → 状态栏挂点"这层抽象**，否则以后加外形要重写状态栏。

### 6.2 可简化 / 可放弃

| omp 的 | 建议 |
|---|---|
| 8 种 shape（box/band/claude/pi/borderless/rule/field/rail） | 只做 **`band`（默认，最省行、最像 omp）+ `box`（有辨识度）+ `borderless`（降级/极简终端）**。claude/pi/rule 的差异（两条/一条横线）可用 CSS class 一个变量表达；field/rail 价值最低，放弃。 |
| powerline 端帽 + `useBgAsFg` 的色带接缝 | **保留 `powerline-thin` 与 `ascii` 两档**，`transparent` 时自动去端帽。别做 `block`/`none` 的独立分支——用空格等价。 |
| context 刻度线 4 档 | 做 `off` / `percentage` / `annotated` 三档；`embedded`（把 context 段吸收进 gap）实现复杂度高、收益集中在一个默认 preset 上，**先不做**，用普通 `context_pct` 段代替。 |
| 27 个 segment | 先做 **14 个**：`pi`(可用静态 logo 字)、`model`、`mode`、`path`、`git`、`pr`、`token_in`、`token_out`、`cost`、`context_pct`、`time_spent`、`session_name`、`subagents`、`vim`。`usage`/`collab`/`stream`/`cache_*`/`hostname`/`session`/`time`/`token_rate` 按需再加——它们都是同一接口下的 20 行实现。 |
| session accent 的 hash 派生色 | 值得做，但**先不做 tween**（§3.3 的 450ms 淡入）。Textual 里每帧改 CSS 变量代价不小，直接跳变即可。 |
| IME-safe 光标布局、终端硬件光标、右侧滚动条拇指 | 放弃。这三项都是为"手写 ANSI 渲染器"解决的具体栅格问题，Textual 的 `TextArea` 已经处理。 |
| 8 个 composer shape 各自独立的 `renderRow` | 放弃。Textual 用 `border` / `padding` / `TextArea.gutter` 表达即可。 |

### 6.3 Textual 落点建议（Widget / Container / Screen 级）

**结构**：把 `#body`（Horizontal）下面的两块整体换成一个 `Composer` Vertical，顺序固定：

```
MainScreen
├─ Horizontal#body (Sidebar | ChatLog)        1fr
└─ Vertical#composer                          height: auto
   ├─ Static#composer-status                  height: 1（band/plain：状态栏单独一行）
   ├─ Horizontal#chips                        height: auto（有附件才显示）
   ├─ Static#working-row                      height: 1（有 turn 才显示）
   ├─ Static#queued                           height: auto
   ├─ PromptInput(TextArea)                   height: auto, max-height: 18
   └─ OptionList#autocomplete                 height: auto, max-height: 10（打开时才 mount）
```

关键点：**状态栏是一个自己的 Widget，不写在 `#prompt-input` 的 `border-title` 里**。Textual 的 `border_title` 只有左/中/右对齐的单标题，放不下"左组 + 刻度线 + 右组"的三段式；用 `Composer.StatusLine(Widget)` 重写 `render()` 返回一行 `Text`，宽度取 `self.size.width`，就能完整复刻 §3.5 的降级逻辑。box 外形想要"嵌在上边框"时，再给 `PromptInput` 加一个 `render_line(0)` 覆写或者干脆用 `band` 外形 + 一行 `#composer-status`（**先做 band，别为了边框去和 Textual 的 border 打架**）。

- `StatusBar` → 改名/改造成 `StatusLine(Widget)`：
  - `SEGMENTS: dict[str, Callable[[StatusContext], Text | None]]` + `left_ids` / `right_ids` 两个元组 + `SEPARATOR` 常量（`"❯"` / `"·"` / `"|"`）。
  - `render()` 里只做：拼左组 → 算 gap → 拼右组 → 按 §3.5 四步降级。
  - 段落点：`statusBar.plain`（现在 `app.py` 的退出摘要会用到）保留为 `render_plain()`，用同一套 segment 逻辑产纯文本，防止两套口径。
- `PromptInput(TextArea)` → 保留 `TextArea` 子类（Enter 提交 / `Ctrl+J`、`Shift+Enter` 换行已经在 `_on_key` 里），加：
  - `BINDINGS` 里 `ctrl+j` → `insert_newline`；`escape` → 先关 autocomplete 再中断。
  - `height: auto; max-height: 18; min-height: 3`（对应 omp 的 6~18 带 / 3 行地板；Textual 的 `auto` 高度会随内容增长，`max-height` 就是 omp 的 `computeEditorMaxHeight`）。**用 CSS 静态上限 18 就够，不必按终端行数动态算。**
  - `placeholder` 用一个轮换 tip 的 `Static` 叠加，或者直接用 TextArea 的 `placeholder`（TextArea 的 placeholder 是左对齐的；omp 是右对齐，属于低优先级细节，**先放弃右对齐**）。
  - 模式色：给 `PromptInput` 加 `add_class("mode-bash" / "mode-python" / "vim-normal" / ...)`，CSS 里改 `border` 颜色。这是 §3.9 优先级链最省事的落法（`_watch_text` 里按前缀判断，只在 class 变化时 `add_class`/`remove_class`）。
  - 前缀识别：`text.lstrip()` 以 `!` 开头 → bash；以 `$` 且后随空白 → python；两个前缀各自的 `!!` / `$$` 记录 `excluded`。这三行判断直接搬 `pythonCommandPrefixLength()` 的规则（`$HOME` 不算 Python）。
- `Static#working-row`：`self.update(Text.assemble(("⎋ ", "dim"), ("Working…", "italic"), ...))`。用 `self.display = bool(running)` 控制显隐；**不要 `mount`/`remove`**（Textual 里频繁 mount 会抖）。
- 排队消息：`Static#queued` 用 `Text` 逐行拼（标题行 + 编号行 + 提示行），或 `ListView`——但这里只是只读展示 + 一个 `Alt+Up` 取回动作，`Static` + 一个 binding 足够。
- 附件 chip：`Horizontal#chips` 里放 `Static` 卡（`height: 5` 对应 4 行内容 + 1 行 caption；宽度 14）。图片缩略图在 Textual 里不好做，**第一版只做文本粘贴 chip**（`[Paste #1] +42 lines`），图片只用 `[Image #1]` token 占位。
- autocomplete：`OptionList` 作为 `#composer` 的最后一个子节点，**排在 TextArea 之后**（对应 omp "渲染在编辑器行下方"），`max-height: 10`；打开时给 `#composer-status` 加 `.hidden`（对应 omp "独立底栏让出该行"）。用 `Vertical` 的 `height: auto` 就能自然把它顶上去，不需要浮层。
- 中断/停止按钮：TextArea 没有右侧内嵌按钮，**不要为了"send→Stop 变形"去做自定义行**；改成：运行时把 `#composer-status` 右端加一段 `esc Stop`（error 色），并且 `enter` 行为切换为"排队 follow-up"。这保留了 omp 的信息密度而不引入新控件。
- 键位：`App.BINDINGS` 现有的 5 个（`ctrl+b/ctrl+c/ctrl+s/ctrl+q/ctrl+x`）与 omp 冲突点多（omp 用 `Ctrl+C` 清草稿、`Ctrl+Q` 排队 follow-up）。**建议按 omp 口径重排**：`Esc`=中断、`Ctrl+C`=清空（空时退出）、`Ctrl+Q`=排队 follow-up、`Ctrl+P`=循环模型、`Shift+Tab`=循环 thinking、`Ctrl+G`=外部编辑器、`Ctrl+R`=历史搜索、`Alt+Up`=取回队列。`Ctrl+S`（当前 steer）在 omp 里不存在，可以保留但不要占用 `Ctrl+Q`。

### 6.4 一句话给主设计者

状态栏的**契约**（segment 列表 + 每段"不可见即不占位" + 四步宽度降级 + 两端锚点）比它的**外观**（powerline / 端帽 / 8 种 shape）重要得多：先按 §5.1 把 segment 表做成数据，再挑 `band` 一种外形落地，剩下的都能增量加。

## 源码对照

- `packages/tui/src/status-line/schema.ts` — `STATUS_LINE_SEGMENT_IDS`（27 个 id）、`CUSTOM_STATUS_LINE_DEFAULTS`、`CONTEXT_LINE_MODE_VALUES`、`STATUS_LINE_SEPARATOR_VALUES`：可配置项的枚举真源。
- `packages/tui/src/status-line/presets.ts` — `STATUS_LINE_PRESETS`：6 个 preset 的段表与选项（§5.2 出处）。
- `packages/tui/src/status-line/segments.ts` — `SEGMENTS` 注册表 + 27 个 `render/describe` 实现；`brandTimer()`、`modelThinkingDisplay()`、`costSummary()`、`formatQuotaWindow()`、`VIM_MODE_LABELS/COLORS`。
- `packages/tui/src/status-line/separators.ts` — `getSeparator()`：7 种分隔符与端帽。
- `packages/tui/src/status-line/component.ts` — `#renderStatusLine()`（组装配 + 四步宽度降级）、`#buildContextGaugeFill()`（四档刻度线）、`statusSegmentPriority()` / `PINNED_NATIVE_SEGMENTS`（原生丢弃优先级）、`NATIVE_SEGMENT_ACTIONS`（点击路由）、`renderBottomBar()` / `getTopBorder()` / `getBandTopBorder()` / `getStandaloneTopBorder()`（按 shape 挂载）、`render()`（hook 状态行 + autocomplete 让行）。
- `packages/tui/src/status-line/types.ts` — `SegmentContext`（所有数据来源字段）、`StatusLineSettings`、`ComposerFacts`、`SeparatorDef`。
- `packages/tui/src/status-line/context-usage.ts` — `computeContextBreakdown()`、`computeCompactionBoundaries()`（刻度线标记位置）。
- `packages/tui/src/chrome/context-thresholds.ts` — `formatContextUsage()`（`42.3%/200K`）、`getContextUsageLevel()`（50/70/90% 与 150K/270K/500K 双阈值）、`getContextUsageThemeColor()`。
- `packages/tui/src/status-line/startup.ts` — `createStartupStatusLine()`：首帧预绘状态栏（复用同一组件，pct 为 null 只显示窗口）。
- `packages/tui/src/status-line/footer.ts` — `FooterComponent`：**旧版**两行 footer（路径+分支 / token 统计 + context + 模型右对齐），已被 status line 取代；仅作对照，不要复刻。
- `packages/tui/src/status-line/metrics.ts` — `formatBillingSummary()`：cost 段的组合规则。
- `packages/tui/src/status-line/loop.ts` — `summarizeLoopCondition()`：`until: bun test` 的压缩写法。
- `packages/tui/src/components/composer/types.ts` — `ComposerStyle` 契约（`statusAttachment` / `bottomBar` / `verticalChrome` / `renderTop/renderRow/renderBottom`）与 `BUILTIN_EDITOR_BORDER_STYLES`。
- `packages/tui/src/components/composer/{box,band,claude,pi,rule,field,rail,borderless}.ts` — 8 种外形实现；`rule.ts` 的 `renderTopRule()` 被 claude 复用。
- `packages/tui/src/components/editor.ts` — `PLACEHOLDER_MIN_GAP`(2)、`#getVisibleContentHeight()`、autocomplete 的 `setMaxVisible()` 视口夹取、`setMaxHeight()`、Vim 光标与 `#effectiveStyle()`。
- `packages/tui/src/components/loader.ts` — `describeWorkingRow()`（spinner·elapsed·`·`·label·spacer·Stop）、`WorkingRowSpec`（retry / compaction 变体）。
- `packages/tui/src/prompt/custom-editor.ts` — `SHELL_SIGIL_RE`、`NATIVE_COMPOSER_PLACEHOLDER`、`ComposerNativeState`、`DEFAULT_ACTION_KEYS`、`describeLayout()`（composer 的 model/effort/dictation/send/Stop 与 mode chip）、`ComposerChipDescriptor`。
- `packages/tui/src/prompt/attachment-chips.ts` — `AttachmentChipsBand`：12×4 卡片几何、`CARD_GAP`(2)、"放不下就不画"。
- `packages/tui/src/prompt/composer-hints.ts` — `COMPOSER_HINTS`、`learnedAfter: 3`、`resolveComposerHint()`。
- `packages/tui/src/prompt/queued-messages.ts` — `QueuedMessagesBand`：`Steering` / `After yield` 分组、编号行、`N queued` 徽标。
- `packages/tui/src/prompt/queue-input.ts` — `parseQueueShorthand()`（`->` / `=>`）、`splitQueuedMessages()`。
- `packages/tui/src/prompt/editor-top-gap.ts` — `EditorTopGap`：band 外形在状态行有内容时塌掉那 1 行空隙。
- `packages/tui/src/keybindings.ts` — `tui.input.submit` / `newLine`（`shift+enter`、`ctrl+j`）/ `tab` / `copy` 等默认键。
- `packages/tui/src/app-keybindings.ts` — `app.*` 动作与默认键（§4.3 出处）。
- `packages/tui/src/key-hint-format.ts` — `formatKeyHint()`：键帽按平台渲染（macOS `⌃⌥⌘` / 其他 `Ctrl`/`Alt`）。
- `packages/coding-agent/src/modes/interactive-mode.ts` — `DEFAULT_WORKING_MESSAGE`、`#workingRowSpec()`、`#workingRowTrailer()`、`renderIdleStatusHud()`、`computeEditorMaxHeight()`（6~18 / 地板 3 / 预留 12）、`updateEditorBorderColor()`（7 级边框色优先级）、`#composerNativeState()`、`setRuntimeChildren()`（dock 顺序 + transient/selfClearing 分类）。
- `packages/coding-agent/src/modes/controllers/input-controller.ts` — `onChange` 里的 bash/python 模式判定、`parsePythonCommandInput()` / `pythonCommandPrefixLength()`。
- `packages/coding-agent/src/modes/utils/ui-helpers.ts` — `QueuedMessageGroup` 的 `Steering` / `After yield` 两个 label。
- `/tmp/omp-docs/keybindings.md` — 键位全集、"prompt border 变色表示模式（bash/python 优先）"、`Ctrl+C` 清草稿再 `Up` 召回。
- `/tmp/omp-docs/theme.md` — 13 个 `statusLine*` token 名单、8 个 thinking 边框色 + `bashMode`/`pythonMode`。
