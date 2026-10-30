# Overlay 与全屏 surface：三层弹层体系

> 调研对象：can1357/oh-my-pi（omp）的 TUI 设计。
> 目标：只记录**设计与交互事实**，用于用 Textual 复刻；不贴大段实现代码。

## 1. 一句话概括

omp 把一切"不是主对话流"的交互收敛成**三档弹层**：**内联**（占据输入区/停靠区，不遮 transcript，分 A1 编辑器槽与 A2 dock 面板两个槽位）、合成在 viewport 上的**锚定浮层**（anchored overlay）、借备用缓冲区独占整屏的**全屏 surface**（fullscreen alt-screen）；另有一类**不属于弹层的常驻 HUD**（错误横幅、subagent 徽标、jobs pill）。三档弹层共用同一套骨架（标题嵌在上边框 / 主体 / 分隔线 / dim 提示行 / 下边框）和同一套 Esc 分层语义；栈里的层**不后台收键**，返回靠"恢复焦点 + 恢复槽位内容"，不重渲染整页。

## 2. 结构与层级

```
终端
├── 主屏（inline screen：transcript 一次性写进 scrollback，之后不可变）
│   ├── transcript 区（只追加：消息 / 工具块 / 分节线）
│   ├── dock 面板槽位（A2：各自一个容器；不抢编辑器槽、编辑器仍可打字）
│   │   ├── btwContainer / reportContainer / omfgContainer / cleanseContainer
│   │   └── (17 层 dock 的完整顺序见 `02-layout.md` §3)
│   ├── 常驻 HUD（非弹层，不抢焦点）：错误横幅（≤4 行）/ subagent 徽标与跳转列表 / jobs pill
│   ├── 编辑器槽 editorContainer（同一时刻只放一个"可见者"）
│   │   ├── 常态：composer（8 种 shape，默认 band：无框，只有状态带 + `╰─` 提示符）
│   │   │   └── composer 下方：autocomplete 下拉（属于编辑器自身渲染，不是 overlay）
│   │   └── A1 内联面板：**替换/压在**编辑器槽里（FIFO 串行，同一时刻只有一个）
│   └── 状态行（模型 / 思考档 / 目录 / git / 上下文 / 花费）
│
├── 浮层栈 overlayStack（合成在 viewport 上，永不写 history）
│   ├── 每个 entry = { component, options, hidden }
│   ├── options 只描述"带子"：anchor(center|top|bottom) / size(sm|md|lg|full) / margin / fullscreen
│   ├── B 层：锚定浮层（sm|md|lg，可带 margin，下方 transcript 隐约可见）
│   └── C 层：全屏 surface（size=full，modal=true）
│
└── 备用缓冲区（alt screen）：C 层独占；关闭后原样回到主屏，主屏内容不变
```

**层级判定规则（源码里的实际换算）**

- `modal = overlay 当前持有焦点 || options.fullscreen === true`；全屏层因此必定 modal。
- `size` 由 `width` 与终端列数的比例推出：≥75% → `lg`，≥45% → `md`，否则 `sm`；`fullscreen: true` → `full`。
- **窄屏降级**：终端 < 100 列时，`anchor: center` 且 `size=lg`（或组件自带 `md`）自动升成 `full`，不再"浮动留白"。
- 全屏层的挂载参数是固定的四件套：`anchor: "bottom-center", width: "100%", maxHeight: "100%", margin: 0, fullscreen: true`（个别用 `top-left`，语义相同，因为整屏都是它）。

**三层共用骨架**（`OverlayPanel` / `HubFrame` / 各 dashboard 一致）

```
╭─ 标题 ─────────────────────────────┬──────────────╮   ① 上边框嵌标题；两栏时用 ┬ 压在分隔列
│ [header 行：新鲜度 / 聚合统计 / 作用域]              │   ② 可选，右上角或整行右对齐
│ Search: ▏ 或  Type to search                       │   ③ 可选搜索行（Input）或列表下方状态行
│ ▸ 分组标题（accent）                                │   ④ 分组列表：标题 + 行
│   ❯ 选中行 label            muted 描述   right 列   │   ⑤ 行 = 光标 + 图标 + 主列 + 描述 + 右对齐列
│     ...                                            │
│ ────────────────────────────────────               │   ⑥ 可选分节线 ├──┤（两栏 ├──┴──┤，与 ① 的 ┬ 对齐）
│ 详情 pane：key-value 事实网格 + 正文 + 分隔线 + 预览 │   ⑦ 两栏时的右栏
├────────────────────────────────────────────────────┤   ⑧ footer：dim 键位 + muted 说明
╰────────────────────────────────────────────────────╯   ⑨ 下边框
```

- 单栏选择器的实测形状（`select()` 走的那条路）：`上边框(含标题)` → 空行 → 标题续行(accent) → 空行 → 列表 → 空行 → dim 提示行 → 空行 → `下边框`。
- 全屏 dashboard 的实测形状：`上边框(含标题)` → header 行 → 内容窗口 → `├──┤` → 提示行 → `下边框`，内容行预算 = `height - 5`。
- 全屏双栏 hub 的形状：`┬` 顶边 → `侧栏 │ 主体` → `├──┴──┤` → 提示行（或 chip 条） → `┴` 底边，内容行预算 = `height - 4`。

## 3. 布局与视觉规则

- **边框**：所有描边 chrome（工具块、overlay、代码栅栏、编辑器、欢迎面板）统一用 `boxRound`（圆角 `╭╮╰╯` + 尖角 tee `├┤┬┴┼`）；Markdown 表格是唯一使用全尖角 `boxSharp` 的例外。**ANSI 路径下带框面板一律画圆角框**（`OverlayPanel` 强制），pi 时代截图里的"无框、只有横线"观感属于旧版/原生渲染，不作为复刻依据。
- **两栏几何**：侧栏宽度按内容测量后夹在 `[min,max]`（hub 通常 18–40 列），行内布局是 `│ 侧栏 │ 主体 │`，每个内容两侧各留 1 列内缩；主体有最小宽度，窄屏时先压到最小、再考虑放弃侧栏。分隔列位置固定为 `sidebarWidth + 3`。
- **高度**：全屏面板不吃满，用 `maxHeight`（90%/100%）+ `margin: 0`；内容窗口按"可见行数"分配，chrome 行数写死在布局里（4 或 5 行），不逐帧算。
- **列表窗口**：按**视觉行**居中而非条目数（session 行高 3~4 行、可换行描述更高），窗口永远以选中项为中心，超出才出现滚动条；滚动条固定占最右 1 列且只在溢出时占。
- **选中态三重表达**：① 光标字形（`theme.nav.cursor`，unicode `❯` / nerd 箭头 / ascii `>`；截图中该主题显示为 `→`）；② 文字用 `accent`（加粗）；③ 可选整行 `selectedBg` 底色带——用于"accent 前景在暗色主题里不够显眼"的场景（如 hook selector 的 outlined 列表）。未选中行用同宽空白补齐光标列。
- **hover**：`selectedBg` 整行底色，与选中态区分（hover 不改文字色）。
- **颜色语义（只记 token 名）**：`accent` 选中/标题/主操作；`border` 框线与分隔；`borderAccent` 强调框（如对话框聚焦）；`muted` 说明文字与元数据；`dim` 键位提示与占位；`selectedBg` 选中/hover 带；`success`/`warning`/`error`/`pending`/`aborted` 状态点与文字；`text` 正文。
- **状态点语法**：`✔ done` / `⚠ interrupted` / `⏹ aborted` / `✘ error` / `⏳ pending`（`unknown` 一律不显示，避免噪声）。
- **提示行语法**：`dim(键位) + muted(" 说明")`，两组之间**两个空格**或 ` · ` 连接；键位全部走 keybinding 注册表（用户重映射后提示自动跟随），替代键用 `/` 连接（`↑/↓`）；说明一律小写动词（`navigate` / `select` / `cancel` / `back` / `close` / `filter` / `go back`）。
- **空态分两套**：数据为空 → `No items` / `No sessions in current folder. Press Tab to view all.`；搜索无匹配 → `No matching items`。都单行、2 空格缩进、用 `noMatch` 样式，并在有搜索时把 `Search: <query>`/`Type to search` 状态行留在原位。
- **截断**：主列固定宽度（默认 32 列 + 2 列间隔，描述列最少 10 列），描述截断而非换行；标题行单行、连续空白折叠、超宽截断。
- **窄屏**：< 100 列时 centered sheet 变 full；Agent Hub 窄屏时 `Tab` 把 inspector 顶替 roster（而不是挤压）；表格列带 `priority`，窄屏从低 priority 往高隐藏。
- **native 变体的差异**：同一组件还能描述成 Tern 原生节点（`card` + `head` 标题 + `hintsRow` + `list`），此时框、玻璃底、ring、placement 由终端画。注意 `/tmp/omp-assets/*.webp` 是 **pi 时代的老截图**（与当前源码已有出入，见 `02-layout.md` 的证据分级），只能当视觉参考：它们展示的是"标题独立成行、只有横线没有圆角"的旧观感，**不能**据此推断当前 ANSI 渲染没有圆角框。复刻只取语义骨架，不取具体渲染细节。

## 4. 交互契约

### 4.1 进入方式

| 入口类型 | 例子 |
| --- | --- |
| slash 命令 | `/settings` `/models` `/agents` `/usage` `/extensions` `/resume` `/copy` `/tree` `/context` `/jobs` `/pause` `/mcp add` `/move` `/theme` `/effort` `/queue` `/plugins` `/btw` `/cleanse` `/plan-review` `/annotate` `/hotkeys` |
| 快捷键 | `Alt+A`/`Ctrl+S` Agent Hub；`Alt+M` 模型 hub；`Alt+P` 临时模型；`Ctrl+R` 历史搜索；`Ctrl+O` 展开工具；`Ctrl+G` 外部编辑器；`esc esc` rewind；`Alt+O` 链接标签 |
| 隐式 | 工具需要审批 → approval dialog；工具 `ask` → ask dialog；plan 模式结束 → plan review；OAuth 登录 → login dialog |
| CLI 子命令 | `omp git`、`omp ps`、`omp --resume`、`omp setup speech`、`omp play`、`omp bench`、`omp cleanse`、`omp if-bench` |

### 4.2 退出与 Esc 语义（核心）

**通用规则**

1. 在选择器/浮层里，`Esc` 与 `Ctrl+C` 同绑 `tui.select.cancel`；但**分层的 Esc 与"立即关闭"的 Ctrl+C 是两件事**：Ctrl+C 直接关，Esc 逐层退。主屏上二者则是两个动作（`app.interrupt` / `app.clear`），见 13 级阶梯。
2. **Esc 分层顺序**：清搜索 → 退子视图（Details → Overview；子菜单 → 父菜单）→ 关面板。全屏 hub 用 footer 文案把当前语义写清（`esc back` / `esc cancel` / `esc close`）。
3. 全屏里再开 overlay：**压栈**，Esc 只关最上层；下层不隐藏也不收键。
4. **没有 raise API**：若菜单 A 被 B 覆盖，再次请求 A 不会把它"提到前面"，而是 `B.hide()` 后把 A **重新挂到栈顶**；只有最上层允许收键（避免隐藏层抢键盘）。
5. **单实例**：每个菜单种类登记一条 `OpenMenu`，重复请求 = 聚焦/重挂，不叠第二份同名菜单。
6. **返回后恢复什么**：`overlay.hide()` 把焦点还给"打开时聚焦的组件"；若那个组件已被换掉（打开期间弹了审批对话框），必须再调 `focusActiveEditorArea()` 把焦点指向编辑器槽当前可见者，否则按键落到隐藏的编辑器上。内联层用 `showSelector` 保存并恢复 `editorContainer.children` + 焦点。
7. **内联层的新开层规则**：内联面板内部再需要"确认框/重命名框"时，**不新开 overlay**，而是在同一个 body 槽里**替换**（session picker 的删除确认、ask 的自定义输入都是这么做的），关闭后恢复原内容。理由是保证外层框高度恒定——历史上有过"对话框把框撑爆 → header 被提交进 scrollback"的缺陷。
8. **内联层串行化**：编辑器槽同一时刻只能有一个 dialog，其余 **FIFO 排队**；因为 selector/input/editor 三者都会 `clear()` 槽位，第二个进来会把第一个孤立（promise 永久挂起）。

**主屏 Esc 的 13 级阶梯**（顺序即优先级，任何一级命中就 return）

1. 正在取消一次提交 → abort 流 + 暂停 loop + 取消待提交
2. 聚焦在某个 subagent 视图 → 有文字就清字，没文字就**退回主会话**（Esc 从不打断 subagent 的回合）
3. collab guest → 请求宿主 abort（本地副本不会 streaming）
4. loading 中且能取消待提交 → 取消
5. loading 已停且没有待异步工作 → 把排队消息**退回编辑器**（不丢）
6. 有 bash 在跑 → abort bash
7. bash 模式 → 退出模式（清空该行）
8. 有 eval 在跑 → abort eval
9. python 模式 → 退出模式（清空该行）
10. 在等异步工作 → 结束等待（后台 jobs 继续跑，结果留给下一轮，不当场打断）
11. 流式输出中 → abort 本回合
12. **编辑器有草稿 → 什么也不做**（Esc 绝不销毁未发送的草稿，并重置双击计时）
13. 编辑器为空 → 500ms 内再按一次 Esc → 执行配置的动作：默认 **rewind selector**，可选 session tree 或 none

### 4.3 键位表（通用）

| 键 | 作用 | 备注 |
| --- | --- | --- |
| `↑`/`↓`、`j`/`k` | 移动选择 | 越界不环绕（选择器默认 wrap=false；SelectList 可选 wrap） |
| `PageUp`/`PageDown` | 翻页（一次一窗口） | 部分面板用于滚动详情 pane |
| `Home`/`End` | 跳到首/末 | |
| `Enter` | 确认选择 / 激活 | 破坏性行需要**两次** Enter（第一次把确认文案写到状态行） |
| `Esc` | 取消 / 逐层退 | 见上文 |
| `Ctrl+C` | 立即关闭 | 跳过"清搜索"这一步 |
| 可打印字符 | 直接进入搜索（过滤列表） | 搜索策略：默认 `overflow`（列表超过窗口高度才开搜索）、`always`、`never` |
| `Tab`/`Shift+Tab`、`←`/`→` | 切 tab / 切 pane | 全屏 hub 内是 provider tab；Agent Hub 窄屏时 `Tab` 换 inspector |
| `/` | 进入过滤 | Agent Hub / Extension dashboard |
| `Space` | 勾选/跟随切换 | ask 多选、Agent Hub 跟随最新活动 |
| `Ctrl+O` | 展开/收起详情 | 工具输出、inspector 展开 |
| `r` | 刷新 / revive | 依上下文 |
| `x` | 销毁（二次确认） | kill agent / discard |
| `?` | 打开快捷键说明 sheet | git TUI |

### 4.4 状态机

- **选择器**：`idle` →（输入）`searching` →（Enter 到需要确认的行）`pending-confirm`（状态行显示确认语，Esc 先撤销确认而不是关闭）→ `confirmed`。
- **全屏 hub**：`list` ↔ `detail`（Enter/Right 进，Esc 退一层），`Esc` 在 list 才是关闭；`roster` ↔ `tree`（`t` 切），`agents` ↔ `activity`（`1`/`2` 切）。
- **运行面板**（btw / omfg / cleanse / live）：`running` → `complete` | `error` | `aborted`，Esc 取消但**不**销毁内容（保留可读）。
- **全屏应用的子视图**（`omp ps` 的 info/logs）：原地换页并把 `Esc/q` 变成"返回"，不是新开一层。

### 4.5 鼠标

默认关闭（`tui.mouse`）。全屏 surface 大多数在自己的生命周期内开启 mouse tracking（如 `/git` 的 `mouseTracking: true`，`omp ps` 显式 `false`），`hide()` 时释放。约定：单击 = select（选中），双击 = activate（等价 Enter）；只对**当前 viewport 内**的行做命中（滚进 scrollback 的行点不到）。悬停先高亮，让人确认点击会打开什么。

### 4.6 层级不是写死的：高度自适应升级（`showCommandReport`）

同一份只读报告会根据"塞不塞得下"落到三个不同层级，这是 omp 里最值得抄的一条自适应规则：

1. 报告高度 ≤ 编辑器上方剩余行数 → **停靠进 `reportContainer`**（dock 槽，编辑器保持焦点，`Esc` 收起，像 `/btw`）；
2. 超过剩余行数 → **升成全屏 alt-screen 页**（箭键/翻页/滚轮滚动，主屏完全不动，`Esc` 原样返回）；
3. native 渲染下 → **90% 居中 focused sheet**。

推论：复刻时不要把"某个命令用哪种表面"写死成常量，而要允许同一内容在 dock / 全屏之间按可用高度升级（Textual 里就是 `if content_height <= available: mount into dock else push_screen(...)`）。

## 5. 完整清单

### 5.1 Overlay（弹层）清单

层级：**A1** = 内联/替换编辑器槽；**A2** = dock 面板槽（`btwContainer` 等）；**B** = 锚定浮层（center/top/bottom，sm–lg）；**C** = 全屏 alt-screen。

> 重要澄清（合并时以此为准）：**历史搜索、主题、思考档、会话树、插件选择、登录/登出账号选择这些"小选择器"走的都是 A1 编辑器槽替换（`showSelector`），不在浮层栈里**；浮层栈只装 B/C 两层。

| 名称 | 作用 | 触发 | 层级 |
| --- | --- | --- | --- |
| `hook-selector` | 通用选项列表（`select()` 的实现）：标题续行 + 选项行（光标/编号/marker）+ dim 提示行；可带描述、禁用项、二次确认、单选或复选标记、左右滑块、倒计时、外部编辑器 | 命令/扩展调用 `ui.select()`（如 `/review` 的评审模式） | A1 |
| `hook-input` | 单行文本输入提示 | `ui.input()` | A1 |
| `hook-editor` | 多行编辑器提示（hook 模式 Enter 换行、followUp 键提交；prompt 模式 Enter 提交、Shift+Enter 换行；`Ctrl+G` 外部编辑器） | `ui.editor()`、ask 的自定义输入 | A1 |
| `approval-dialog` | 工具审批：标题 + 详情 + 选项列（授权开关 / Approve / 本次会话批准 / Deny）+ 动作条；叠在 composer **上方**，打字仍进草稿 | 工具需要审批 | A1 |
| `ask-dialog` | `ask` 工具问答：`?` 标记 + 问题 + 选项（radio/checkbox）+ Other/自定义输入；多问题用 tab；`Alt+H` 压成一行 | 工具 `ask` | A1 |
| `thinking-selector` | `/effort` 思考档选择（当前档高亮） | `/effort`、`/thinking` | A1 |
| `theme-selector` | 主题选择，移动即实时预览；选择后确认 | `/theme`、设置里的主题行 | A1 |
| `tree-selector` | `/tree` 会话树（ASCII 树 + 选中 + 折叠 + 分支跳转） | `/tree`、双击 Esc（可配置） | A1 |
| `history-search` | 提示历史搜索：搜索行 + 多行条目预览 + 滚动 | `Ctrl+R` | A1 |
| `queue-mode-selector` | 排队模式选择（带边框紧凑选择器） | `/queue` | A1 |
| `show-images-selector` | 图片显示策略选择（带边框紧凑选择器） | `/images`、设置项 | A1 |
| `plugin-selector` | 市场插件选择（安装/卸载） | `/plugins` | A1 |
| `plugin-settings` | 插件设置层级界面（插件列表 → 详情 → 字段），自身可以再套一层子列表 | `/plugins` 深入 | A1（多级列表） |
| `login-dialog` | OAuth 登录流（登录期间**替换编辑器**） | `/login` | A1 |
| `oauth-selector` | provider 选择 + 校验状态 | `/login`、`/logout` | A1 |
| `logout-account-selector` | 登出账号选择 | `/logout` | A1 |
| `session-account-selector` | 会话固定账号选择 | `/session` 账号相关 | A1 |
| `reset-usage-selector` | `/usage reset` 账号选择（列出已保存的重置额度） | `/usage reset` | A1 |
| `plan-save-overlay` | 计划保存路径确认（Enter 保存并退出 / Esc 取消） | plan 模式保存 | A1 |
| `debug-selector` | 调试入口菜单（终端信息 / 协议探测 / 日志查看） | `/debug` | A1 |
| `live-visualizer`（`apps/`） | 实时语音通话可视化：频谱 + 阶段标签 + 尾部字幕 + 底部提示，固定高度；进入时关掉硬件光标 | `/live`、`Ctrl+L` | A1 |
| `btw-panel` | `/btw` 旁路问答的流式面板（running/complete/branching/aborted/error） | `/btw` | A2（`btwContainer`） |
| `omfg-panel` | `/omfg` 生成-校验-确认面板 | `/omfg` | A2（`omfgContainer`） |
| `cleanse-panel` | `/cleanse` 运行看板（宿主实时 board） | `/cleanse` | A2（`cleanseContainer`） |
| `report-panel` | 只读报告：**高度自适应**——塞得下就停靠 dock，塞不下升全屏 | 各命令的报告输出、`/hotkeys`（ANSI 路径） | A2 / C（自适应） |
| `session-info-overlay` | 会话信息卡：模型 / 花费 / 上下文 / 路径等事实网格 | `/session`、状态栏交互 | B（bottom-center 100%） |
| `jobs-panel` | 后台 jobs 主从表（树 + 选中项的状态/命令/事实/实时输出） | `/jobs`、jobs pill 点击 | B（center 90%） |
| `model-picker` | 临时模型选择（`Alt+P` / `/switch`）：只有 model browser，无 provider 侧栏，贴底全宽；搜索以 `@` 起头则切到快捷角色 | `Alt+P`、`/switch` | B（bottom-center 100%） |
| `move-overlay` | `/move` 路径输入 + 实时目录补全（Tab 接受 / Enter 确认） | `/move` | B（center） |
| `mcp-add-wizard` | 多步向导：添加 MCP server（自带 `center/md/modal` sheet 声明） | `/mcp add` | B（center md） |
| `btw-history-panel` | `/btw` 历史面板（问题/答案列表 + 详情） | `/btw` 历史 | B（center） |
| `hotkeys sheet` | 快捷键说明：分组 + `key  description` 两列表 | `/hotkeys` | B（center 90%，native）/ A2（ANSI 走 markdown 报告） |
| `codex-reset-fireworks` | 重置额度成功的庆祝动画（顶部锚定，短暂） | `/usage reset` 成功 | B（top） |
| `error-banner` | 常驻错误横幅，最多 4 行，下一轮开始自动清除 | 回合以 provider 错误结束 | dock（非弹层，不抢焦点） |
| `running-subagent-badge` / Subagents 跳转列表 | 常驻 subagent 徽标与跳转行，折叠 3 档（collapsed / full / off） | 有 subagent 运行 | dock（非弹层，可点击跳转） |

### 5.2 全屏 surface 清单

| 名称 | 作用 | 进入 | 退出 | 布局分区 |
| --- | --- | --- | --- | --- |
| `/settings` | 设置编辑器（tab + 侧栏 + 子菜单钻取），是全屏 idiom 的参照实现 | `/settings` | `Esc`（子菜单逐层退）/ `Ctrl+C` | 顶边标题 + tab 条 + 左设置行侧栏 + 右表单/预览 + 提示行 |
| `/models`（model hub） | 模型中心：作用域侧栏（最近 / 角色管理 / 全部 / 各 provider）+ model browser 主体 | `/models`、`Alt+M` | `Esc` | 顶边标题 + `┬` 双栏 + `├──┴──┤` + 提示行/chip 条 |
| `/agents`（agents hub） | agent 定义管理：作用域侧栏 + 列表 + 属性 strip → 值 strip → 真模型选择 | `/agents` | `Esc` 逐层退 | 同 hub 骨架；底部在配置时变成 chip 条 |
| Agent Hub | subagent 实时花名册与控制：`1 Agents` / `2 Activity` 两个 section，`Flat`/`By parent` 两种视图，宽屏右侧 inspector，窄屏 `Tab` 顶替 | `Alt+A`、`Ctrl+S`、编辑器空时双击 `←` | `Esc`（先清过滤 → 再退窄屏 inspector → 再关闭） | header 聚合（状态汇总 + 用量）+ tab 条 + roster/activity 主体 + inspector 侧栏 + 提示行 |
| Agent transcript viewer | 文件级 transcript 查看器（tail 增量 + 可输入行），给没有可聚焦会话的 agent（collab guest / advisor 只读） | Agent Hub 内 Enter | `Esc` 回 Hub（Hub 原样在下层） | 整屏：标题 + 元信息 + 内容 + 输入行/提示行 |
| `/usage` | 用量仪表盘：订阅卡网格（每 provider 一卡，取最差窗口）+ GitHub 风格日活热力图 | `/usage` | `Esc`（Details → Overview → 关闭） | 顶边标题（`Usage` / `Usage · Details`）+ header（刷新时间）+ 内容窗口 + `├──┤` + 提示行 |
| `/context` | 上下文占用分析：Overview（用量归因 + 图例 + 占用图）→ 分类预览 → 条目全文，三级钻取共享同一导航状态 | `/context` | `Esc`（逐级退回，最后关闭） | 顶边标题 + 内容窗口 + `├──┤` + 提示行；与 `/usage` 共用 `#showFullscreenSheet` |
| `/extensions` | 扩展控制中心：provider tab 条 + 分组清单（Skills/Context…）+ inspector 详情 | `/extensions` | `Esc`（先清搜索 → 再关闭） | 标题行 + tab 条 + `Search:` 行 + 双栏（清单 │ inspector）+ 提示行 |
| `/resume`（session picker） | 会话选择：搜索 + 按天分组列表（Pinned/Today/Yesterday/This week/Earlier）+ 行内元数据（时间/大小/状态/当前/分支/cwd）+ 预览 | `/resume`、`--resume` 无参 | `Esc` / `Enter` 选中 | 顶边标题（Resume Session）+ 搜索行 + 空行 + 列表 + 固定底部提示行 |
| `/copy` | 复制选择器：在 alt screen 上**重放当前分支的 transcript**，用点线外框框住"将会复制的块"，Right 下钻到块内部（代码栅栏/引用/bash/工具输出/链接） | `/copy` | `Esc` / `Enter` 复制 | 整屏 transcript 复刻 + 点线选中框 + 提示行 |
| esc-esc rewind | 回滚选择器：同样的 transcript 复刻 + 点线框，Left/Right 跳用户回合，`f` 过滤，`a` 更早回合 | 编辑器空时 500ms 内双击 `Esc` | `Esc` | 同 `/copy`，提示行换文案 |
| plan review | 计划审批：计划按 heading 切节、可滚动、可批注，宽屏带 Contents 目录，审批选项（+ 模型档滑块）在同一个描边框内的下方 | plan 模式结束、`/plan-review` | 选择后关闭（回调只跑一次） | 整屏 + Contents 侧栏 + 节内容 + 底部选项条 + 提示行 |
| `/pause` | 暂停屏：冻结全进程 agent loop（不 abort），画大号暂停字形与说明 | `/pause` | 恢复键 / `Esc` | 整屏居中 |
| `/advisor configure` | advisor（WATCHDOG.yml）编辑器：advisor/动作 侧栏 + 可滚动预览 | `/advisor configure` | `Esc` | 从第 0 行起画满整屏（为了让鼠标行号直接可索引） |
| `/annotate` | 代码审查标注：diff/source 行 + 批注列 + 编辑/删除批注 | `/annotate` | `Esc` 逐层退 | 整屏三栏（文件/行/批注）+ 提示行 |
| `omp git` / `/git` | git TUI：header（路径/编码/stage 按钮/关闭）+ 工具栏（作用域 chip、视图切换、hunk 导航、空白/换行开关）+ 中部 diff（带 minimap 滚动条）+ 右侧栏（暂存 + commit 表单 / HEAD 详情 + 头像）+ footer 提示行 | `omp git`、`/git` | `q`/`Esc` | 五段式；`Tab` 在 diff 与侧栏间切焦点；`?` 开居中帮助 sheet |
| `omp ps` | 进程监控（btop 味儿）：实时进程表 + 就地操作（info/logs/stop/kill/restart） | `omp ps` | `q`/`Esc`/`Ctrl+C`；子视图 `Esc/q` 返回 | 表格 + 子视图（info/logs）原地换页 |
| `omp --resume`（standalone picker） | 独占终端的会话选择器（同一个 `SessionSelectorComponent` 的 `standalone` + `fillHeight` 模式） | CLI | `Esc` | 与 `/resume` 相同，但 footer 钉在最后几行、整屏填满 |
| `omp setup speech` | 一次性模型选择器，属于"独立程序"型 surface（自己起 TUI、选完即销毁） | CLI | 选择/取消 | 单列表 |
| debug viewers | 诊断日志查看器 / 终端信息 / 协议探测，共用 `DebugViewerFrame`（header 行 + 滚动 body + footer 行，高度钉死） | `/debug` 深层 | `Esc` | 顶边标题 + header + 滚动体 + footer |
| `omp bench` / `omp if-bench` / `omp cleanse` / `omp autoresearch` | 运行中的"活看板"：原地重绘的行（spinner、阶梯、进度），非 TTY 降级为一行一条 | CLI | 进程结束 / `q` | 定高 app 区，不写 scrollback |

### 5.3 层级选择依据（什么时候用哪层）

| 判据 | 选 A 内联 / dock | 选 B 锚定浮层 | 选 C 全屏 |
| --- | --- | --- | --- |
| 是否要边看 transcript 边操作 | 要（评审/审批/输入，甚至要边打字） | 要（看 sheet 后面的上下文） | 不要 |
| 内容规模 | 十几行内（A2 的 btw/report/cleanse 可到半屏） | 半屏以内 | 多区块 / 主从 / 需要 tab |
| 是否会改动别处状态 | 单值输入 | 单值输入 + 少量事实 | 大量浏览 + 编辑 + 反向钻取 |
| 失败代价 | 低（可重来） | 低 | 中（会遮住工作区，必须有清晰的 Esc） |
| 实例 | ask/approval/select/input/editor、tree、history、theme；A2：`/btw`、报告、`/cleanse`、`/omfg` | `/jobs`、`/move`、`/mcp add`、模型快选、会话信息、`/hotkeys`、`/btw` 历史、烟花 | `/settings`、`/models`、`/agents`、Agent Hub、`/usage`、`/context`、`/extensions`、`/resume`、`/copy`、rewind、plan review、`/pause`、`/annotate`、git、ps |

## 6. 复刻判断（给 Textual）

### 6.1 值得复刻（高价值）

1. **三档弹层 + 明确的升降判据**：内联（A1 编辑器槽 / A2 dock 面板）、浮层、全屏，把"要不要遮住 transcript""塞不塞得下"变成可判定规则（见 5.3 与 4.6），而不是每个功能各自拍脑袋。这是 omp 视觉上最稳的地方。
2. **统一骨架**：`上边框嵌标题 / 可选 header / 主题 / 可选分节线 / dim 提示行 / 下边框`。所有 list 类界面共享一套 `ListScreen` 基类，换的是数据源和详情 pane。
3. **Esc 分层 + 主屏 13 级阶梯**：尤其"Esc 永不销毁草稿"和"清搜索优先于关闭"这两条，直接决定手感。
4. **单实例 + 槽位替换 + FIFO 串行**：同名面板不叠、内层对话不新开层、编辑器槽同时只有一个 dialog。这三条能消掉绝大多数"焦点飞到隐藏组件"的 bug。
5. **提示行由键位注册表生成**：footer 不是硬编码字符串，而是 `action → 当前绑定键 → 展示`，用户改键后提示自动变。Textual 里用 `BINDINGS` + 一个 `format_hints()` 即可。
6. **列表窗口按视觉行居中**（多行条目），窄屏按列 priority 隐藏。
7. **状态/空态文案成对**：`No items` vs `No matching items`；`search` 策略 `overflow/always/never`。

### 6.2 可简化 / 可放弃

- **native(Tern) describe 双路径**：我们只做 ANSI/Textual，不需要为每个组件再写一套语义描述。
- **SGR 鼠标逐行命中**：可以先只保留"click=select，双击=activate"，不做 hover/拖拽/滚轮路由。
- **长尾视觉件**：`codex-reset-fireworks`（烟花）、`pause-screen` 的大字形、8 种 composer shape 的实时预览、splash。可做成静态版本或直接省略。
- **`/copy` 与 rewind 的"transcript 复刻 + 点线框"**：效果很好但实现重（要在 alt screen 重放 branch）。建议一期用"列表 + 右侧预览"替代，二期再做复刻。
- **双栏 hub 的 chip 条 footer**（`[Tab] [Model] [Prewalk]`）：可先用普通提示行 + 一个选择列表替代。
- **71 个 overlay 不必逐个移植**：真正天天用的是 settings / model / agents / resume / jobs / context / tree / 各类 confirm-select，其余按 §5.1 的清单择期。

### 6.3 Textual 落点建议

| omp 概念 | Textual 落点 | 说明 |
| --- | --- | --- |
| 全屏 surface（settings/models/agents/usage/context/extensions/resume/copy/rewind/plan-review/pause/annotate/git/ps） | `Screen` + `push_screen()` / `pop_screen()` | 天然独占一屏；`pop_screen` 即"恢复下层"。Tab 条用 `Tabs`，双栏用 `Horizontal` + `Vertical`，滚动用 `ScrollableContainer` |
| 锚定浮层（jobs/move/mcp-add/model-picker/session-info/hotkeys/btw-history） | `ModalScreen` + 居中/贴底容器（`width: 90%; max-height: 90%`），`dismiss()` 返回结果 | 需要"下层可见"就用 `ModalScreen` 的半透明背景（`background: $background 60%`），不要 `Screen` |
| 内联面板 A1（ask/approval/select/input/editor/tree/history/theme/effort） | 直接挂进 composer 容器上方的 `Vertical`（**不用 Screen**），通过一个 `ComposerSlot` 控制器做"同一时刻一个 + FIFO 队列" | 这是最关键的一条：用 Screen 会丢掉 transcript 上下文，也会破坏"叠在 composer 上方、打字仍进草稿"的行为 |
| dock 面板 A2（`/btw`、报告、`/cleanse`、`/omfg`） | 主 Screen 里 composer 上方的固定槽位（各自一个 `Vertical` 子容器），`display` 切换 | 与 A1 的区别：不抢编辑器槽、不抢焦点时的 Esc，编辑器仍可输入 |
| 常驻 dock（错误横幅 / subagent 徽标 / jobs pill） | 主 Screen 里 composer 上方的固定 `Vertical`，`display` 切换 | 不可聚焦 |
| 高度自适应升级（报告类） | `if rendered_height <= available_rows: mount into dock(A2) else push_screen(C)` | 对应 `showCommandReport`；同一份内容按可用高度换层级 |
| 统一骨架 | 抽象 `SurfacePanel(Widget)`：`title` → `#header` → `#body` → `#footer`，子类只实现 `compose_body()` / `hints()` | 对应 omp 的 `OverlayPanel` / `HubFrame` |
| 提示行 | `format_hints([(binding, label), ...])`，从 `App.BINDINGS` 反查当前键 | 对应 `keyHint()` / `hintsRow()` |
| 列表 + 搜索 + 窗口 | 一个 `FilterList(Widget)`：query / fuzzy filter / 跳过 disabled / 两段式确认 / 视觉行居中窗口 / 溢出滚动条 | 对应 `SelectList` + `MenuSelection` + `getMenuWindow` |
| 两段式确认（删除会话、discard 文件） | `FilterList` 的 `pending_confirm` 状态 + 状态行文案 | 不要用弹二次框 |
| 层叠 | `ModalScreen`/`Screen` 的 push 栈天然有序；"被覆盖的面板不接键"由模态语义保证 | 无需自己写 `overlayStack` |
| 窄屏降级 | `on_resize` 里按宽度切布局（<100 列 center→full；<84 列双栏→单栏 + Tab 切换） | |

## 源码对照

- `packages/tui/src/chrome/overlay-box.ts` — `OverlayPanel` / `topBorder` / `topBorderSplit` / `divider` / `dividerSplit` / `row` / `PanelRows` / `splitBodyWidth`：所有描边浮层的统一 chrome 与两栏几何。
- `packages/tui/src/overlays/hub-frame.ts` — `HubFrame` / `describeHubFrame` / `renderSidebar` / `renderFooter` / `renderChips` / `locate`：全屏双栏 hub 的骨架、侧栏跟随滚动、chip 条与鼠标命中。
- `packages/tui/src/components/select-list.ts` — `SelectList` / `SelectItem` / `SelectListTheme`：列表渲染、窗口、搜索策略、空态/无匹配态、二次确认、hover、滚动条。
- `packages/tui/src/components/menu-selection.ts` — `MenuSelection` / `getMenuWindow`：查询→模糊过滤→选择保持、disabled 跳过、两段式激活、按视觉行居中窗口。
- `packages/tui/src/chrome/selector-helpers.ts` — `clampSelection` / `centeredWindow` / `contentRowWidth` / `handleTabSwitchKey` / `padLinesToHeight`：列表窗口与滚动条宽度的公共算法。
- `packages/tui/src/keybindings.ts` — `TUI_KEYBINDINGS`（`tui.select.*`：cancel = escape + ctrl+c）、`KeybindingsManager`：Esc/Ctrl+C 同源、提示可重映射。
- `packages/tui/src/app-keybindings.ts` — `KEYBINDINGS` / `formatKeyHint(s)`：应用级快捷键与提示格式化（`Alt+A` hub、`Alt+M` model、`Ctrl+R` history 等）。
- `packages/tui/src/keybinding-matchers.ts` — `matchesAppInterrupt` / `matchesSelectCancel` / `matchesAppFollowUp` / `matchesAppApprove`：各组件共用的键位判定（含 fallback）。
- `packages/tui/src/native/backend.ts` — `overlayAnchor` / `overlaySize` / `#overlayNode`：带子换算（center/top/bottom × sm/md/lg/full）、`modal` 判定、窄屏升 full。
- `packages/tui/src/native/overlay.ts` — `NARROW_SHEET_COLS` / `overlayCard` / `hintsRow` / `statusHintsRow` / `actionButton`：native 侧的 overlay 语义（标题/提示行/按钮 tone）。
- `packages/tui/src/overlays/session-selector.ts` — `SessionSelectorComponent`：搜索行 + 按天分组 + 3~4 行条目 + 固定在底部的提示行 + 内层确认框**替换 body 槽**。
- `packages/tui/src/overlays/settings-selector.ts` — 全屏设置：tab 条 + 侧栏 + 子菜单（Esc 逐层退）+ 搜索横幅。
- `packages/tui/src/overlays/usage-dashboard.ts` — `render()`：全屏面板的高度预算（`height-5`）、Overview ↔ Details 原地换视图、footer 文案随视图变。
- `packages/tui/src/overlays/extensions/extension-dashboard.ts` — `handleInput` / `#clearSearch`：tab 切换、`Esc` 先清搜索再关闭、`Ctrl+C` 直接关。
- `packages/tui/src/overlays/model-hub.ts` / `agents-hub.ts` / `agent-hub.ts` — 三个全屏 hub 的侧栏作用域、section tab、列 priority、inspector、hint 集随视图切换。
- `packages/tui/src/overlays/agent-hub.ts` — `AGENT_COLUMNS`（priority 1–6）/ `SECTION_TABS` / `agentHints()` / `ROSTER_MIN_WIDTH=48` / `DETAIL_MIN_WIDTH=34`：窄屏列隐藏与视图切换的具体参数。
- `packages/tui/src/overlays/jobs-panel.ts` / `overlays/job-tree.ts` — 主从 sheet：树 + 状态/命令/事实/实时输出。
- `packages/tui/src/overlays/context-viewer.ts` + `context-viewer-overview.ts` — Overview → 分类 → 条目 的三级钻取与共享导航状态。
- `packages/tui/src/overlays/copy-selector.ts` / `rewind-selector.ts` — alt screen 上的 transcript 复刻 + 点线框 + 左右下钻/跳回合。
- `packages/tui/src/overlays/plan-review-overlay.ts` / `plan-toc.ts` — 按 heading 切节 + 目录 + 在同一描边框内的底部选项条。
- `packages/tui/src/overlays/hook-selector.ts` — `HookSelectorComponent`：内联选择器的完整骨架（Spacer/标题续行/outlined 或普通列表/提示行）与 outlined 选中带。
- `packages/tui/src/overlays/approval-dialog.ts` / `ask-dialog.ts` — 叠在 composer 上方、输入转发给草稿的内联对话。
- `packages/tui/src/overlays/error-banner.ts` — `MAX_BANNER_ROWS=4`：常驻横幅（非弹层）。
- `packages/tui/src/apps/git/git-tui.ts` / `apps/git/help.ts` — 五段式全屏 app + `?` 居中帮助 sheet（全屏里再开浮层的实例）。
- `packages/tui/src/apps/ps-top.ts` — `runPsTop`：以 `top-left/full/fullscreen` 挂载的全屏 app、子视图原地换页。
- `packages/tui/src/apps/debug/viewer-frame.ts` / `log-viewer.ts` — `DebugViewerFrame`：header/body/footer 三段 + 钉死高度。
- `packages/coding-agent/src/modes/controllers/selector-controller.ts` — `#showFullscreenMenu`（全屏四件套）/ `#focusOpenMenu` / `#isCovered`（无 raise，重挂到顶）/ `#openMenus`（单实例）/ `showSelector`（保存并恢复编辑器槽 children + focus）/ `focusActiveEditorArea`。
- `packages/coding-agent/src/modes/controllers/extension-ui-controller.ts` — `#presentDialog`（编辑器槽 FIFO 串行、abort 未轮到的请求）/ `guestAskHelpText`（`↑↓ navigate  enter select  esc cancel` 的提示行原型）/ ask 与 approval 的槽位挂载与恢复。
- `packages/coding-agent/src/modes/controllers/command-controller.ts` — `showCommandReport`（**层级按高度自适应**：dock → 全屏 → native sheet）/ `#showFullscreenSheet`（全屏四件套的第二个封装）/ `#closeReportSheet`（关闭后把焦点还给编辑器槽可见者）/ `/context` 用 `#showFullscreenSheet` 挂载。
- `packages/coding-agent/src/modes/interactive-mode.ts` — `showSessionInfo`（`bottom-center` 全宽浮层）/ `showPlanReview`（`bottom-center / 100% / 100% / margin 0 / fullscreen`，`#hidePlanReview` + `onHide` 只跑一次）/ `showJobsSheet`（`center / 90% / 90%`）：三种挂载姿态与返回契约的实例。
- `packages/coding-agent/src/modes/controllers/input-controller.ts` — 主屏 Esc 的 13 级阶梯、500ms 双击窗口、`doubleEscapeAction`（rewind / tree / none）。
- `packages/coding-agent/src/modes/controllers/live-command-controller.ts` — `/live` 把可视化挂进编辑器槽、暂停硬件光标、结束恢复。
- `packages/coding-agent/src/modes/controllers/btw-controller.ts` — `btwContainer.clear()/addChild()`：A2 dock 面板的挂载/卸载方式（`/btw` 面板）。
- `packages/tui/src/components/composer/*` — 8 种 composer shape（`band` 为默认：无框、状态带 + `╰─` 提示符），决定"内联层要接在哪"。
- `docs/keybindings.md`（`/tmp/omp-docs`）— 应用快捷键表、Esc 与 Vim 的交界、双击 Esc 语义。
- `docs/tui.md` / `docs/tui-runtime-internals.md` / `docs/tui-core-renderer.md` — overlay 合成规则（浮层合成在 viewport、不写 history；全屏层用备用缓冲区）、focus/OverlayHandle、mount 与返回契约。
- `docs/agent-hub.md` — Agent Hub 的视图、键位、窄屏 inspector、Esc 顺序、transcript viewer 的 alt-screen 借用与"关闭后回到 Hub"。
- `docs/session-switching-and-recent-listing.md` — resume picker 的两种装载方式（编辑器内紧凑 / `--resume` 全屏）与面板字段。
