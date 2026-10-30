# omp 主屏布局解剖

> 调研对象：`can1357/oh-my-pi`（omp）main 分支。
> 只记录设计与交互事实，用于 Textual 复刻。行号不写死，引用符号名 + 文件路径。
> **证据分级**：仓库 `assets/*.webp` 是 pi 时代截图（欢迎页还是 "Welcome back!" + 右栏 LSP/Recent sessions，当前源码里已不存在），只能当视觉参考；**布局结论一律以源码为准**。

## 1. 一句话概括

omp 的主屏不是"侧栏 + 聊天 + 输入框"的三栏式，而是一条**底部 dock 主轴**：上面是可被终端原生 scrollback 吸收的对话流，下面是 17 层可独立出现/消失的"HUD 带"，最底部是 composer（输入区），而状态栏**不是**固定的一行，它是 composer 的一部分（默认挂在输入区**上方**）。

## 2. 垂直解剖（默认 band 形状）

```
┌ 终端原生 scrollback 区（提交过的历史行，滚出去就归终端管）───────────────┐
│  ╭ 欢迎块：渐变 omp logo + 词标 + 版本 + 「Tip: …」                    │
│  ╭ What's New（changelog，可选）                                        │
│  ╭ 配置告警（可选）                                                     │
└───────────────────────────────────────────────────────────────────────┘
  ▸ 转录区 TranscriptContainer（活的尾部，宽度变化时重排）
  ▸ 排队消息 pendingMessages（用户按 Ctrl+Q 排进来的消息）
  ▸ todo HUD（任务清单，最多几行）
  ▸ 子代理 HUD（subagents）
  ▸ btw / omfg / cleanse / report 面板（按需）
  ▸ 错误横幅 errorBanner
  ▸ 模型切换提示 modelCycle
  ▸ 延迟命令预览 deferredCommand
  ▸ 进度 HUD（下载、评判批次）
  ▸ 工作行 statusContainer：`⠋ Working... (esc to interrupt)` / 重试提示
  ▸ 附件 chips（[Image #1, 800x600]、[Paste #2, +30 lines]）
  ▸ 扩展挂件 hookWidgetAbove
═══ 状态带（band：整行填色的 powerline，左边缘齐平，无边框）═══
╰─ 你的输入…                          ← 唯一的输入行，前端只有 `╰─ ` 这个弯角
  ▸ 扩展挂件 hookWidgetBelow
```

- 默认形状下，**屏幕最后一行就是输入行**，输入区下方什么都没有。
- 状态带在输入区**上方**、紧贴着。
- 转录区之上的一切（欢迎块、changelog）都不是固定面板，而是"还没滚出去的历史"，随对话增长被顶进 scrollback 后就永久消失（`Composer.acknowledgeHistory` 里 `#headerRetired = true`）。

## 3. dock 的层级顺序（这是最硬的布局事实）

`packages/coding-agent/src/modes/interactive-mode.ts` 里 `composer.setRuntimeChildren(...)` 的数组就是从上到下的顺序，每条注释都说明了它为什么在这一层：

| # | root | 作用 | 何时出现 | 塌陷是否抬高"chrome 地板" |
|---|------|------|----------|--------------------------|
| 1 | `chatContainer` | 转录（可伸缩） | 永远 | — |
| 2 | `pendingMessagesContainer` | 已排队未发送的消息 | 用户排队时 | transient |
| 3 | `todoContainer` | 任务清单 HUD | 模型用 todo 工具 | self-clearing |
| 4 | `subagentContainer` | 子代理进度 HUD | 起子代理 | self-clearing |
| 5 | `btwContainer` | /btw 侧问答面板 | 按需 | transient |
| 6 | `reportContainer` | 停靠的报告 | 按需 | transient |
| 7 | `omfgContainer` | /omfg 规则面板 | 按需 | transient |
| 8 | `cleanseContainer` | /cleanse 进度 | 按需 | transient |
| 9 | `errorBannerContainer` | 错误横幅 | 出错 | transient |
| 10 | `modelCycleContainer` | Ctrl+P 切模型提示 | 切换时短暂出现 | self-clearing |
| 11 | `deferredCommandContainer` | 延迟执行命令的预览 | 按需 | persistent |
| 12 | `progressHudContainer` | 下载/评判进度 | 按需 | self-clearing |
| 13 | `statusContainer` | 工作行（spinner）、重试行 | 运行中/失败后 | persistent |
| 14 | `attachmentChipsContainer` | 附件 chip 带 | 有附件 | transient |
| 15 | `hookWidgetContainerAbove` | 扩展挂件（编辑器上方） | 扩展 | transient |
| 16 | `editorContainer` | composer/编辑器本体 | 永远 | transient |
| 17 | `hookWidgetContainerBelow` | 扩展挂件（编辑器下方） | 扩展 | transient |
| — | `#statusHost` | 状态栏宿主（band 形状下为空，pi 形状下是底栏） | 视形状 | — |

**"chrome 地板"（chrome floor）**：composer 会记住"运行结束后 dock 至少保留多少行"，这样一轮对话结束后输入框不会突然跳到屏幕中间，而是等新内容把空位填满才自然上移。三类 root 的归类（`RuntimeChildrenOptions`）：
- `transientChrome`：用户开关的东西（编辑器、附件、报告、队列、可关闭面板、错误横幅）→ 塌陷**不**抬高地板；
- `selfClearingChrome`：自己会清空的 HUD（todo、子代理、模型提示、进度）→ **首帧最高**，所以完全不参与地板；
- 其余为 persistent：一旦撑住高度，地板就抬到它那里。

> 注释里还有一句很实用的判断：`progressHudContainer` 特地从上面挪到工作行上方，因为"评判批次和自动下载紧贴输入区更新、且独立于转录输出"。

## 4. 状态栏到底在哪：8 种 composer 形状

`packages/tui/src/components/composer/` 下 8 个形状各一个文件，`ComposerStyle` 用四个字段决定"状态栏挂哪"：

- `statusAttachment`: `top-border` | `top-band` | `top-rule-chip` | `none`
- `bottomBar`: `none` | `left` | `full`
- `sideBorders`：内容行左右是否有边框字符
- `verticalChrome`: 0/1/2 行固定 chrome

| 形状 | 面板 | 左右边框 | chrome 行 | 状态栏挂载 | 底栏 | 底栏前空行 | 输入前缀 |
|------|------|----------|-----------|------------|------|-----------|----------|
| **band（默认）** | 无 | 无 | 1 | `top-band` 整行状态带 | 无 | — | `╰─ ` |
| box | 圆角框 | 有 | 2 | `top-border` 嵌在顶边里 | 无 | — | — |
| claude | 上下横线 | 无 | 2 | `top-rule-chip` 贴在顶线上 | left | 否 | `❯ ` |
| pi | 上下横线 | 无 | 2 | 无 | full | 否 | — |
| borderless | 无 | 无 | 0 | 无 | full | 否 | `❯ ` |
| rule | 单条顶线 | 无 | 1 | `top-rule-chip` 贴在顶线上 | left | 是 | `❯ ` |
| field | 填色单行 | 有 | 0 | 无 | full | 是 | — |
| rail | 填色单行 + 左侧强调竖条 | 有 | 0 | 无 | full | 是 | — |

默认 `band` 的官方描述（`band.ts` 头注释）：**"整条状态栏作为填充色 powerline 带，左边缘齐平于终端，带一个柔和的开头帽子，没有框、横线或角；下面是无框输入行，由一个弯曲的 `╰─ ` 提示符锚定"**。

这条对复刻影响最大：omp 的默认外观**没有输入框边框**，状态信息是**输入区上方的一整条色带**。

## 5. 面板 chrome 的唯一词汇表

`packages/tui/src/chrome/overlay-box.ts` 定义了所有带框面板共用的绘制规则，全系统只此一套：

- 字符集用 `theme.boxRound`：**圆角四角 + 尖角的分叉**（`╭ ╮ ╰ ╯ ├ ┤`）；
- 颜色只用 `border` 和 `accent` 两个主题色；
- `topBorder(width, title)` → `╭─ 标题 ──────╮`，标题**内嵌在边框里**，两侧各一个空格，bold + accent；
- `divider(width)` → `├──────────┤`；
- `bottomBorder(width)` → `╰──────────╯`；
- `row(content, width)` → `│ 内容 │`，左右各**留 1 列内缩**；
- 两栏布局固定为 `│ 侧栏 │ 主体 │`，分隔竖线落在 `sidebarWidth + 3`，即"1 列内缩 + 内容 + 1 列内缩"。

## 6. 分隔与密度规则

- 主要分隔不是"空行"而是 `DynamicBorder`（横线）或 `divider`（带 tee 的横线）；
- 欢迎块与 changelog 之间用 `DynamicBorder` 包夹；
- 面板内部区块之间用 `divider`，不用空行；
- 行内字段用**空格对齐 + 右对齐列**（例：Extension Control Center 里 `user` / `project` 右对齐成一列）；
- 空行只在"段落级"出现（消息之间、警告块前后），HUD 之间不空行。

## 7. 宽度降级（narrow）

- **状态栏**：按 segment 优先级从右往左丢；`model`、`context`、`git branch` 是最后被丢的（`status-line/component.ts` 注释："Segments that outlast every other one as the native bar narrows (model, context, git branch)"）。76 列以下进入"composer 预览"式的极简渲染。
- **欢迎块**：宽度够 → logo + 词标 + 版本（lockup）；不够 → 只放 logo；再不够 → 什么都不放。`Tip:` 行在 **50 列以下**隐藏（`TIP_MIN_COLUMNS = 50`）。所有内容左右各留 1 列，整体居中。
- **两栏面板**：侧栏宽度先固定，主体吃剩余；`overlay-box.ts` 在 `width < 4` 时放弃内缩。

## 8. 给 Textual 的落点建议

| omp 结构 | Textual 落点 |
|----------|--------------|
| 底部 dock（HUD 序列） | 一个 `Vertical` dock，`dock: bottom`；HUD 用 `display: none` 切换，**不要**动态 mount/unmount（避免滚动跳变） |
| 转录区 | `ScrollView` + `Vertical`，`height: 1fr` |
| chrome 地板 | Textual 不需要复刻精确算法；用 `max-height` 限制每个 HUD，保证"运行结束后输入框不回跳"即可 |
| band 状态带 | 1 行 `Static`，`background: $surface`，`border: none`，贴在输入区上方 |
| `╰─ ` 前缀 | 输入区用 `Input` + 前置 `Static("╰─ ")` 两列布局，或用 `TextArea` 的 `prefix` |
| 面板圆角 + 内嵌标题 | Textual `border: round $border` + `border-title-align: left`，标题即 `border_title` |
| `divider()` | 1 行 `Static` 画 `├───┤`，或 `Rule` 换自定义字符 |

## 源码对照

- `packages/coding-agent/src/modes/interactive-mode.ts` — `composer.setRuntimeChildren(...)`：17 层 dock 顺序与 chrome 地板分类
- `packages/coding-agent/src/modes/interactive-mode.ts` — `setHeaderExtras` / `#headerAfter`：欢迎块与 What's New 的挂载点
- `packages/tui/src/prompt/composer.ts` — `renderFrame` / `acknowledgeHistory` / `#chromeFloor`：dock 渲染与地板算法
- `packages/tui/src/components/composer/types.ts` — `ComposerStyle`：形状契约（状态栏挂载点、边框、chrome 行数）
- `packages/tui/src/components/composer/{band,box,claude,pi,borderless,rule,field,rail}.ts` — 8 种形状参数
- `packages/tui/src/chrome/overlay-box.ts` — `topBorder` / `divider` / `row`：唯一的面板绘制词汇
- `packages/tui/src/prompt/welcome.ts` — `LOGO_WIDTH` / `TIP_MIN_COLUMNS` / lockup 降级
- `packages/tui/src/status-line/component.ts` — 窄屏丢段优先级
