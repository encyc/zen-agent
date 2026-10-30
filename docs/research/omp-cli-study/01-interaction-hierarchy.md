# omp 交互层级（Interaction Hierarchy）

> 调研对象：`can1357/oh-my-pi`（omp）main 分支，`packages/tui` + `packages/coding-agent`。
> 只记录交互与设计事实，用于 Textual 复刻；引用符号名 + 文件路径，不贴实现代码。

## 1. 一句话概括

omp 的交互模型是**"一条主轴 + 一个可切换的焦点会话 + 一套单实例弹层栈"**：屏幕只有一个主表面（底部 dock 上挂着转录区和输入区，见 `02-layout.md`），所有额外界面都是从这条主轴**向上长出来**（浮层）、**接管输入区**（inline）、或**借整块屏幕**（全屏）三种方式之一，且都遵循"同一种菜单只允许开一个、重复请求变成聚焦"的规则。

## 2. 三层职责

| 层 | 位置 | 负责 |
|----|------|------|
| 渲染引擎 | `packages/tui` | `Component`（`render(width)` 返回行数组）、`Focusable`、输入解析、焦点、`OverlayStack`、光标定位、终端安全 |
| 集成层 | `packages/coding-agent/src/modes/interactive-mode.ts` | 装配主屏、把 session/事件接到组件、决定每个动作开哪种表面 |
| 控制器层 | `packages/coding-agent/src/modes/controllers/*` | 一个交互域一个 controller：`input`、`selector`、`command`、`event`、`extension-ui`、`session-focus`、`todo-command`、`live`… |

要点：**表面选择是集成层的决定，不是组件自己的决定**。组件只知道自己怎么画、怎么收键；"这东西该做成浮层还是全屏"由 `SelectorController` / `InteractiveMode` 统一裁决——所以整个产品只有 3 种视觉层级，没有第 4 种。

## 3. 五个核心概念

### 3.1 表面（surface）——"三档弹层 + 一套槽位"

> 本节以 `05-overlays-and-surfaces.md` 为准（它逐文件核对了 37 个 overlay 与 22 个全屏 surface）；早期"4 种"的说法把两个不同的内联槽位混在一起了。

| 档 | 宿主方式 | 视觉 | 实例 | 在浮层栈里？ |
|----|----------|------|------|--------------|
| **主表面** | 常驻 | dock + 转录 + composer | 对话、HUD | — |
| **A1 内联：替换编辑器槽** | `#presentDialog` / `showSelector` 把组件放进 `editorContainer`，**同一时刻只允许一个**（其余 FIFO 排队） | 占据 composer 的位置，上下屏幕不变 | 审批弹窗（"in the composer's place"）、`ask`、`select`、`input`、`editor`、tree、历史搜索、主题、思考档、插件、登录账号 | ❌ 不走浮层栈 |
| **A2 内联：停靠容器** | 各自一个 dock 容器（`btwContainer` / `reportContainer` / `omfgContainer` / `cleanseContainer`） | 插在 dock 序列里，不抢焦点 | `/btw`、报告、`/omfg`、`/cleanse` | ❌ 属于 dock |
| **B 锚定浮层** | `ui.showOverlay(c, { anchor: "bottom-center", width: "100%", maxHeight: "100%", margin: 0 })` | 合成在 viewport 上，贴底向上长，下方转录仍可见 | 临时模型选择器（`#showModelPicker`）、jobs sheet、确认框 | ✅ |
| **C 全屏** | `#showFullscreenMenu` / `showOverlay(..., { fullscreen: true })` | 借 alternate screen 整屏绘制，关闭后原样恢复 | Settings、Model Hub、Agent Hub、Usage Dashboard、Extensions Control Center、Session Selector、Git UI、Debug | ✅ |

浮层锚点统计（`grep anchor:` 全仓）：`bottom-center` 10 处、`center` 3 处、`top-left` 2 处。**默认就是 bottom-center**，这是 omp 最常用的弹层姿态。

尺寸不是手写的：`size` 由"宽度 / 终端列数"推出（≥75% → `lg`，≥45% → `md`，否则 `sm`），`modal = 持焦点 || fullscreen`；**终端 < 100 列时 `center`+`lg` 自动升为 `full`**（不再浮动留白）。

还有一条很实用的自适应规则（`showCommandReport`）：同一份只读报告，**塞得进编辑器上方剩余行就停靠 dock，塞不下就升成全屏**——不要把"某命令用哪种表面"写死。

### 3.2 dock（底部装配带）

17 层有序 root，顺序即布局，详见 `02-layout.md` §3。关键性质：每层都能独立出现/消失，且"消失后是否抬高 chrome 地板"被显式分类（`transientChrome` / `selfClearingChrome` / persistent）。HUD 不是弹层，永远不和焦点抢输入。

### 3.3 会话焦点（session focus）——最容易被忽略的一层

`SessionFocusController.focusAgent(id)` 把**另一个 agent（子代理）的会话挂到同一套 UI 上**；`unfocus()` 回到 parent 并提示 `Returned to main session`。

含义：主表面显示的"会话"是可换的数据源，UI 结构不变。这是 omp 把"多 agent"做成第一公民的方式——不是开新窗口，而是**在同一屏幕里切换焦点**。Agent Hub（`Alt+A`）是这个切换的入口和名册。

### 3.4 菜单栈（menu stack）——单实例 + 无 raise

`SelectorController` 维护 `#openMenus: Map<MenuKind, OpenMenu>`，7 个"最多开一个"的菜单：

```
settings · model-picker · model-picker-select · model-hub · agents-dashboard · agent-hub · usage-dashboard
```

规则：
- **重复请求 = 聚焦**（`#focusOpenMenu` 返回 true 并直接返回），不会叠第二个；
- 底层 `OverlayStack` **没有 raise API**，所以"被别的浮层盖住的菜单再请求"= 先 `hide()` 再重新 mount 到最上层（`#isCovered` 检测）；
- 关闭时必须 `#releaseMenu`，否则该 kind 永远认为自己开着。

### 3.5 模式（mode）——正交状态，不是屏幕

模式不改变布局，只改变：**边框/状态段颜色、Esc 的含义、提交时的解释器、可用工具集**。

| 模式 | 进入 | 视觉标记 | 退出 |
|------|------|----------|------|
| plan（只读规划） | `Alt+Shift+P`（`app.plan.toggle`） | 状态栏 `mode` 段 `Plan`（暂停时 `Plan (paused)` + warning 色） | 同键 |
| vibe（快速 worker） | `/vibe` | 状态栏 mode 段 | `/vibe` |
| goal（自主目标） | `/goal`、`--goal` | mode 段带 icon：active=accent、paused=warning、complete=success、budget-limited=warning、dropped=dim | `/drop` |
| bash 模式 | 输入以 `!` 起头 | **输入框边框 = bash 色**（优先级最高） | Esc / 提交 |
| python 模式 | 输入进入 python 解释态 | 边框 = python 色（次高） | Esc / 提交 |
| vim 编辑 | `/settings` 或 `tui.vimMode` | 边框随模式：normal/replace=accent、insert=success、visual=warning；状态栏 `vim` 段 | Esc 链 |
| live voice | `Ctrl+L` / `/live` | 状态栏段 | 同键 |
| collab（共享会话） | `/collab` | 状态栏 `collab` 段 | `/stop` |
| record | `/record` | 无（后台） | 同键 |
| pause | `/pause` | 全屏 pause 画面，显示 draining turns / running jobs | 解除 |

**边框颜色优先级链**（`updateEditorBorderColor()`，不可换序）：
`bash → python → vim(visual=warning / normal&replace=accent / insert=success) → session accent 或 thinking level 色 → 聚焦子 agent 时整层再降暗`。

## 4. 焦点与输入路由

omp 不用"冒泡"，用**输入监听链（input listener chain）**：

- `ui.addInputListener(fn)`，`fn` 返回 `{ consume: true }` 表示吃掉这个键，返回 `undefined` 表示继续；
- 监听器按注册顺序执行，**聚焦组件自己的绑定最权威**（注册时会显式检查 `ui.getFocused() === editor`）；
- 某些应用级动作在**有浮层时主动让位**：代码里直接写 `if (this.ctx.ui.hasOverlay()) return undefined;`（例：`app.thinking.toggle`、`app.history.search`）。

焦点只有一个：`ui.setFocus(component)`；浮层会自己接管焦点，关闭时 `overlayHandle.hide()` 把焦点还给输入区（`focusActiveEditorArea()`）。

### Esc 的语义链（主屏 13 级阶梯，`05-overlays-and-surfaces.md` §4）

按顺序判定，命中即停（这里列关键级，完整 13 级见 05）：

1. bash 模式 → 清空并退出 bash 模式
2. eval 运行中 → 中止 eval
3. python 模式 → 清空并退出
4. 正在等待异步作业 → 结束等待（作业继续跑，结果留到下一轮）
5. 正在流式输出 → 中止本轮
6. **输入框有内容 → 什么都不做**（原注释：`Esc must not destroy an in-progress draft`）
7. 输入框为空 → **500 ms内第二次 Esc** → 打开"回到某条消息"选择器（默认）或会话树

在弹层内部 Esc 也是分层的：**先清搜索 → 再退子视图 → 最后才关面板**；而 `Ctrl+C` 是硬关（直接关掉整层）。`overlay.hide()` 会把焦点还给"打开它时的聚焦者"，若那个聚焦者已被替换，必须显式 `focusActiveEditorArea()` 兜底。
### 键位命名空间与可重映射

四组前缀，全部可在 `~/.omp/agent/keybindings.yml` 覆盖（值是 chord 或 chord 数组；空数组 = 禁用）：

- `app.*`（45 个应用动作）
- `tui.editor.*`（24 个编辑动作：移动/删除/撤销/yank/jump/page）
- `tui.select.*`（7 个选择器动作：上/下/翻页/确认/取消）
- `tui.input.*`（4 个：submit / newLine / copy / tab）

默认键（与当前 py-agent 冲突的已标注）：

| 动作 | 默认键 | 说明 | 冲突 |
|------|--------|------|------|
| `app.interrupt` | `Esc` | 中断 | ⚠ py-agent 用 Ctrl+C |
| `app.clear` | `Ctrl+C` | 清屏/清草稿（**不退出**） | ⚠ py-agent 用 Ctrl+C 中断 |
| `app.exit` | `Ctrl+D` | 退出 | — |
| `app.message.followUp` | `Ctrl+Q`（兜底 `Ctrl+Enter`） | 排队追加消息 | ⚠ py-agent 用 Ctrl+Q 退出 |
| `app.message.dequeue` | `Alt+Up` / `Shift+Up` | 把排队消息取回编辑器 | — |
| `app.model.cycleForward` | `Ctrl+P` | 循环模型 | — |
| `app.model.select` | `Alt+M` / `Alt+P` | 选模型 / 临时选模型 | — |
| `app.thinking.cycle` | `Shift+Tab` | 循环 thinking 等级 | — |
| `app.thinking.toggle` | `Ctrl+T` | 折叠/展开 thinking | — |
| `app.tools.expand` | `Ctrl+O` | 展开工具输出 | — |
| `app.tools.toggleVisibility` | `Ctrl+Shift+O` | 隐藏/显示工具活动 | — |
| `app.history.search` | `Ctrl+R` | 搜历史 | — |
| `app.editor.external` | `Ctrl+G` | 外部编辑器 | — |
| `app.agents.hub` | `Alt+A` | Agent Hub | — |
| `app.plan.toggle` | `Alt+Shift+P` | plan 模式 | — |
| `app.approval.approve` | `Ctrl+Enter` / `Ctrl+Q` | 批准（刻意不用 Enter） | — |
| `app.retry` | `F5` / `Alt+R` | 重试失败轮 | — |
| `app.suspend` | `Ctrl+Z` | 挂起 | — |
| `app.display.reset` | `Alt+L` | 重置显示 | — |

> 注释里给了两个很实际的教训：`F5` 放首位是因为"所有终端都原样转发 F5，而带修饰的 Enter 会被各种终端吞掉"；`Ctrl+Q` 放 follow-up 首位是因为 Windows Terminal 不转发 `Ctrl+Enter`。

## 5. 事件 → 视图

- agent 事件（message start/delta/end、tool start/end、turn end…）由 `EventController` 消费；
- **转录区是单向追加的块序列**（见 `04-transcript-and-tools.md`），块自己记住"可展开/可重试/可复制"等动作；
- **HUD 独立于转录**：todo、子代理、进度各自订阅各自的数据，直接重绘自己那一层，不重建转录；
- 状态栏同理，独立订阅，segment 自己决定显示与否。

结论：**刷新粒度按层切分**，不是整屏重渲染。

## 6. 复刻判断（给 Textual）

1. **三档各用不同的 Textual 载体，别一把梭 `Screen`**：
   - A1 内联（审批/ask/选择器）→ **普通容器**，挂在 composer 上方，配一个 `ComposerSlot`（单实例 + FIFO + 保存/恢复焦点）。用 `ModalScreen` 会同时丢两样东西：transcript 上下文（用户要边看边决定），以及"审批框在上、打字仍进草稿"这条关键行为。
   - B 锚定浮层 → `ModalScreen` + `align: bottom; width: 100%; height: auto; max-height: 80%`。
   - C 全屏 → `Screen` + `push_screen`。
2. **菜单栈必须显式建模**：一个 `dict[kind, handle]` + `open_or_focus(kind)`，否则双击快捷键会叠出两个模型选择器（omp 专门为此写了 7 种单实例菜单）。注意 omp 底层**没有 raise API**，被盖住的菜单要"隐藏上层 + 重挂到顶"才能收键。
3. **Esc 语义链要照抄**：它是"一个键承担十几种意图"的解药，而"有草稿时 Esc 什么都不做"是体验分水岭；弹层内还要再分三层（清搜索 → 退子视图 → 关面板）。
4. **按键位重排是复刻的一半**：交互层级再像，`Ctrl+C=中断` 与 `Esc=中断` 的差异会让肌肉记忆完全不认。建议直接采用 omp 的 `Esc/Ctrl+C/Ctrl+D/Ctrl+Q` 四件套，并在 docs 里给出迁移表。
5. **session focus 可以先不做**，但**要预留"转录区数据源可替换"的接口**（`Transcript.set_source(session_id)`），否则将来加子代理视图要重写。
6. **报告类内容做"自适应升级"**：先尝试停靠 dock，塞不下再升全屏；不要为每个命令硬编码用哪种表面。

## 源码对照

- `packages/coding-agent/src/modes/interactive-mode.ts` — 表面装配、`setRuntimeChildren`、`setEditorComponent`、`statusLine.onNativeAction`
- `packages/coding-agent/src/modes/controllers/selector-controller.ts` — `#showFullscreenMenu` / `#focusOpenMenu` / `#isCovered` / `#claimMenu` / `#releaseMenu`：菜单栈全部规则
- `packages/coding-agent/src/modes/controllers/input-controller.ts` — `setupKeyHandlers`、Esc 语义链、`handleCtrlC`/`handleCtrlD`、双击检测
- `packages/coding-agent/src/modes/controllers/session-focus-controller.ts` — `focusAgent` / `unfocus`
- `packages/tui/src/app-keybindings.ts` — `KEYBINDINGS` 默认键与注释里的终端兼容性教训
- `packages/tui/src/overlays/approval-dialog.ts` — 输入区替换式弹窗的完整契约（含"边打字边拒绝"）
- `packages/tui/src/keybindings.ts` — `TUI_KEYBINDINGS`（`tui.editor.*` / `tui.select.*` / `tui.input.*`）
