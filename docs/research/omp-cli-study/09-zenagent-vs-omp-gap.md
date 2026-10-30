# ZenAgent × omp 功能对标与必要性判断

日期：2026-10-30 · 作者：Lead（与用户讨论后整理）

> **基线**
> - **omp**：`can1357/oh-my-pi` main（调研快照见本文档 01–08，完成于 TUI 复刻前）。
> - **ZenAgent**：`HEAD 9606d33` + **当前工作树**（2026-10-30 20:50）。工作树里 `wrapup-modes-sessions` 的未提交改动（plan/ask/full 模式、TUI 审批弹窗、会话 brief/改名/picker 操作）**计入本次现状**，统一标 `✅*`。
>
> **口径**：本表覆盖 06 总账里的全部可命名项：87 个 slash 命令、45 个 `app.*` 键位（归并 43 行）、69 个浮层（归并 35 个行为项）、25 个全屏 surface、24 种转录行类型、8 种 composer 形状、27+2 个状态段、17 层 dock、13 级 Esc 语义链、60 个工具渲染器（按 11 个家族归并）。
>
> **图例**
> - 现状：`✅` 已有等价能力（名字/形态可不同） · `✅*` 工作树已实现未提交 · `🟡` 部分（有骨架或单入口，缺关键能力） · `❌` 未做
> - 必要性：`P0` 立即补 · `P1` 值得做 · `P2` 可选 · `N` 不做（附一句理由）
>
> **判断原则**
> 1. 服务 ZenAgent「最小 Pythonic 内核 + harness」定位，不为观感复刻而膨胀。
> 2. 优先做**能被模型能力放大**的（todo、subagent、plan review、审批），而不是纯 UI 装饰。
> 3. 依赖链优先：`todo → todo HUD`、`plan mode → plan review`、`subagent → HUD/Hub`、`images → attachment chips`。
> 4. omp 的多数 UI 表面是它为 40+ provider / 多 agent / 协作场景付的成本，对单机最小 agent 不构成需求。

---

## 0. 结论速览

| 类别 | 总量 | ✅ 已有 | 🟡 部分 | ❌ 未做 | 结论 |
|---|---|---|---|---|---|
| 87 个 slash 命令 | 87 | 20（含 ✅* 3） | 5 | 62 | 真正值得补的只有 **3–4 个** |
| 45 个应用键位 | 43 行 | 12（含 ✅* 1） | 6 | 25 | 补 **3 个**（历史搜索/取回排队/双击 Esc）即可 |
| 浮层 + 全屏界面 | 35 + 21 | 2 + 2 | 5 + 4 | 28 + 15 | 只补 **审批（已做）**、少量 P2 |
| 状态段 | 27 + 2 动态 | 7 | 3 | 19 | 加 **2–3 段**（cache_hit / time_spent / token_rate） |
| composer 形状 | 8 | 1（band） | 0 | 7 | **不做**，band 足够 |
| 转录行类型 | 24 | 7 | 4 | 13 | 补 **压缩分隔线 + 错误样式**，其余按需 |
| 工具渲染器 | 60（11 家族） | 1（兜底） | 6 | 5 | 补 **bash/read/edit/grep 的标题+元信息** |
| dock 17 层 | 17 | 3 | 1 | 13 | 只补 **错误横幅 / 排队 HUD**（P2） |
| Esc 语义链 | 13 级 | 3 | 0 | 10 | 补 **双击 Esc 回退 + 草稿保护（已有）** |

**一句话结论**：omp 的「观感骨架」（布局、状态带、转录、工具卡、浮层、键位四件套、单实例菜单）已经在 P0–P4 复刻完成；剩下 60+ 个未做项里，**只有 8–10 个对 ZenAgent 有真实价值**，其余应当明确记为「不做」，避免后续反复讨论。

### 0.1 值得做（P1 主项 + 条件项，建议 v2 前后落地）

| # | 项 | 来源 | 成本 | 价值 |
|---|---|---|---|---|
| 1 | 工具卡按 omp 语法补标题/元信息（bash/read/edit/grep） | 04 §5.2 | 1d | 转录可读性最大的一处 |
| 2 | compaction 分隔线 + 压缩前/后 token 提示 | 04 #14 | 0.5d | 自动压缩从「无声」变「可解释」 |
| 3 | Esc/队列交互子集：空草稿双击 Esc 回退、`Alt+Up` 取回排队、`Ctrl+R` 历史搜索 | 01 §4、03 §5.3 | 1–2d | 日常高频 |
| 4 | 错误横幅（或 sticky 错误行） | 05 #34 | 0.5d | provider/工具错误不再被刷走 |
| 5 | 小命令补齐：`/tools`、REPL `/retry`（`/pin` 可选） | 08 | 0.5d | 低成本透明性/一致性 |
| 6 | 状态段 2–3 个：`cache_hit`、`time_spent`、`token_rate` | 03 §5.1 | 0.5d | 成本/性能可见（数据已有） |
| 7 | TUI 图片附件 + attachment chip 带 | 04 #18、02 #14 | 1–2d | 仅当 vision 是常用能力 |
| 8 | todo 工具 + 简化 HUD | 04 #21、02 #3 | 2–3d | 长任务/自主性的地基；不走向主性则降 P2 |
| 9 | plan review（最小版：计划文本 + 显式批准） | 05 §5.2 | 1–2d | 仅当 plan 模式要成为正式工作流 |
| 10 | TUI 历史搜索/外部编辑器（二选一） | 03 §5.3 | 1d | 长 prompt 体验 |

> 第 8–9 项是条件项（做到对应系统才成立，否则降 P2）；第 1–7、10 项为确定 P1。

### 0.2 明确不做（N，别再讨论）

插件/技能市场、`/security`、语音/录屏/collab/分享、OAuth 登录、Git UI、ps/top、worktree/ssh、advisor、memory、goal/vibe/loop/ratchet/prewalk、`/cleanse` `/omfg` `/tan`、`/computer` `/fast` `/slow` `/extended-context` `/skillful`、`/pause` `/force`、`/dump` `/open` `/browser`、`/stats` `/changelog`、`!` bash 模式、`$` python 模式、`#` 快速动作菜单、queue 简写、拼写/yank、composer 另外 7 种形状、27 段全量、24 行类型全量、60 个工具渲染器全量、25 个全屏 surface 全量、17 层 dock 全量、Agent Hub、debug viewers、codex fireworks、`status`/`collab`/`stream`/`vim`/`usage` 状态段、`/login` `/logout`。

---

## 1. 87 个 slash 命令逐项对标

### 1.1 modes（21）

| 命令 | omp 作用 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| `/security` | 安全扫描域 | ❌ | N：不在定位内 |
| `/settings` | 全屏设置中心 | 🟡 TUI 设置 palette（模型/密钥/搜索/思考/侧栏/清空/导出），无全屏中心 | P2：config.toml + palette 够用 |
| `/setup` | provider 配置向导 | ❌（密钥在 settings/配置） | N：向导是给 OAuth 用的 |
| `/plan` | 只读规划模式 | ✅*：`/mode plan` + plan prompt note + gate 拒写 | 已补齐，无遗留 |
| `/plan-review` | 计划复核界面 | ❌（计划只是模型输出文本） | P2：除非 plan 模式要做成工作流 |
| `/vibe` | 只读 worker 会话 | ❌ | N |
| `/goal` `/guided-goal` | 自主目标模式 | ❌ | N：与最小 agent 定位冲突 |
| `/loop` | 循环模式 | ❌ | N |
| `/queue` | 排队模式选择 | 🟡：follow-up 队列已有（Ctrl+Q），无 queue-mode 选择器 | P2：当前队列语义够用 |
| `/model` | 切模型 | ✅ | — |
| `/switch` | 模型切换（browser） | ✅ 等价 `/model` + Alt+P | — |
| `/fast` `/slow` | provider 服务档位 | ❌ | N：单家单档 |
| `/skillful` | 技能列表开关 | ❌ | N |
| `/extended-context` | 扩上下文开关 | ❌ | N |
| `/computer` | computer-use prelude | ❌ | N |
| `/ratchet` `/prewalk` | eval 爬山 / 模型交接 | ❌ | N |
| `/modelpreset` | 角色模型预设 | ❌ | N：单模型定位；要做也先做 todo/subagent |
| `/effort` | 思考档 | ✅ 等价 `/thinking`（off/low/medium/high/max） | — |

**modes 小结**：21 项中 ✅ 4（1 项为工作树 `/plan`）、🟡 2、❌ 15；只有 `/plan` 是必须（已完成），`/settings` `/queue` 保持现状。

### 1.2 session（20）

| 命令 | omp 作用 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| `/todo` | 任务清单（HUD + 编辑） | ❌ 无 todo 工具/系统 | **P1**：长任务价值最高的单项 |
| `/session` | 会话管理 | ✅ 文本信息（模型/上下文/花费） | — |
| `/jobs` | 后台作业列表 | ❌ 无后台作业系统 | N：bash 有超时，不需要 jobs |
| `/usage` | provider 用量 | ✅ 文本统计（无 dashboard） | — |
| `/stats` | 本地统计面板 | ❌ | N |
| `/changelog` | 更新日志 | ❌ | N |
| `/hotkeys` | 快捷键参考 | ✅ 静态文本 | — |
| `/tools` | 当前可见工具清单 | ❌ | **P1**：一行命令的成本，透明度高 |
| `/context` | 上下文占用浏览 | ✅ 文本（百分比），无全屏明细 | P2：文本够用，viewer 可选 |
| `/extensions` | 扩展控制中心 | ❌ 无 UI（扩展本身可用） | P2：文件式扩展够了 |
| `/agents` | agent 定义管理 | ❌ | N |
| `/git` | 全屏 Git UI | ❌ | N：交给 shell |
| `/local` | 会话内 local:// 文件 | ❌ | N |
| `/hub` | Agent Hub（subagent） | ❌ 无 subagent | N（除非做 subagent，也只做简化 HUD） |
| `/branch` `/tree` | 分支 / 会话树 | ❌（有 fork/undo/import，无树） | P2：成本高，append-only 文件模型要先升级 |
| `/fork` | 分叉 | ✅ | — |
| `/login` `/logout` | OAuth | ❌ | N：env key + settings 够用 |
| `/mcp` | MCP 管理 | ✅ 列表/重连（无 add 向导） | — |

**session 小结**：✅ 6、❌ 14；P1 两个（todo/tools），其余 P2 或 N。

### 1.3 lifecycle（25）

| 命令 | omp 作用 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| `/ssh` | SSH 主机管理 | ❌ | N |
| `/new` | 新会话 | ✅（TUI /new；REPL 无） | — |
| `/fresh` | 重置流状态 | ❌ | N |
| `/clear` | 原地清上下文 | ✅ | — |
| `/delete` | 删当前会话 | ✅*（TUI picker `d` + Confirm + `.trash/`） | — |
| `/compact` | 手动压缩 | ✅ | — |
| `/shake` | 分级瘦身（工具结果/图片/thinking） | ❌ | P2：比 /compact 更细，可后置 |
| `/handoff` | 总结成交接文档 | ❌ | P2：长会话外交接有用 |
| `/resume` | 选会话恢复 | ✅ `/sessions` picker + `--resume` | — |
| `/pin` | 置顶会话 | ❌ | P2：picker 排序的一部分，低成本 |
| `/btw` | 旁路问答 | ❌ | P2：多一条流，先不做 |
| `/tan` | 后台切题 agent | ❌ | N |
| `/omfg` | 从抱怨生成规则 | ❌ | N |
| `/cleanse` | 并行修诊断 | ❌ | N |
| `/retry` | 重试上一轮 | 🟡 TUI `/retry` 有；REPL 无命令 | **P1**：REPL 对齐，半天 |
| `/debug` | 调试工具选择 | ❌ | N：有 doctor/crash/trace |
| `/memory` | 记忆维护 | ❌ | N |
| `/rename` | 会话改名 | ✅*（`/rename` + sidecar 标题） | — |
| `/move` | 挪会话目录 | ❌ | N |
| `/wt` | 迁到 worktree | ❌ | N |
| `/add-dir` `/remove-dir` `/dirs` | 多根工作区 | ❌ | P2：monorepo 有用，但 cwd 模型要改 |
| `/exit` | 退出 | ✅ | — |
| `/restart` | 带原参数重启 | ❌ | N |

**lifecycle 小结**：✅ 7（含 ✅* 2）、🟡 1（retry）、❌ 17；P1 仅 retry 对齐，`/shake` `/handoff` `/pin` 列 P2。

### 1.4 control（6）

| 命令 | omp 作用 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| `/force` | 强制下一轮用某工具 | ❌ | N |
| `/live` | 实时语音 | ❌ | N |
| `/record` | 录屏回放 | ❌ | N |
| `/pause` | 冻结所有 agent | ❌ | N |
| `/forget-approvals` | 清掉记住的批准 | 🟡 不适用：ZenAgent 的 "always" 只活在本会话内存 | N |
| `/quit` | 退出 | ✅ | — |

### 1.5 collaboration（11）

| 命令 | omp 作用 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| `/advisor` | 第二模型评审 | ❌ | N |
| `/export` | 导出会话 | ✅（md/html） | — |
| `/trace` | 打开 trace 面板 | 🟡 `--trace` CLI 落盘；无 TUI 命令 | P2：加个 `/trace` 打开文件即可 |
| `/dump` | 复制 transcript / 落 LLM 请求 | ❌ | N |
| `/share` `/collab` `/join` `/leave` | 分享/协作 | ❌ | N |
| `/browser` | 浏览器模式 | ❌ | N |
| `/copy` | 挑段复制 | ❌ | P2：终端选中已够用 |
| `/open` | 打开链接 | ❌ | N |

### 1.6 marketplace（3）+ skills（1）

| 命令 | omp 作用 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| `/marketplace` `/plugins` `/reload-plugins` | 插件市场 | ❌（有文件式扩展，无市场） | N：与"最小、无插件注册机制"反目标冲突 |
| `/skills` | 技能搜索/安装/更新 | ✅ 本地发现 + `read_skill`（无 registry 安装） | 保持现状；registry 不必要 |

### 1.7 命令总账

- **已具备 20 / 87**（其中 3 项为工作树新增：`/plan`、`/delete`、`/rename`；`/mode` 是 ZenAgent 新增、不在 omp 87 表内）。
- **部分 5**：`/settings`、`/queue`、`/retry`、`/trace`、`/forget-approvals`（不适用）。
- **未做 62**，但按必要性只有：**P1 = 3 项**（`/todo`、`/tools`、`/retry` REPL 对齐；`/plan-review` 为 P2 条件项）、**P2 ≈ 12 项**、**N ≈ 46 项**。

---

## 2. 应用键位（45 → 43 行）

omp 的 `app.*` 分 7 组；Linux/mac 平台相关 2 个（suspend/stt 变体）归并。当前 ZenAgent 键位 = `app.py::ZenAgentApp.BINDINGS` + `prompt_input.py` 的 key overrides。

### 2.1 会话（11）

| omp 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| new | — | 🟡* picker `n` / `/new`（TUI） | — |
| resume | 选择器 | ✅ `/sessions` / Ctrl+K | — |
| fork | — | 🟡* picker `f` / `/fork` | — |
| delete | — | 🟡* picker `d` + Confirm | — |
| rename | — | 🟡* `/rename` + picker `r` | — |
| pin / unpin | — | ❌ | P2：合并成一个 pin 动作 |
| tree | 双击 Esc | ❌ | P2：依赖会话树模型 |
| observe | — | ❌ | N |
| togglePath / toggleSort | — | ❌ | N：picker 还没有排序/路径列 |

### 2.2 模型（4）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| cycleForward | Ctrl+P | ✅ Ctrl+P | — |
| cycleBackward | Shift+Ctrl+P | ❌ | P2：一行键位 |
| select | Alt+M | ✅ Alt+P | — |
| selectTemporary | Alt+P | ✅ Alt+P（同一语义） | — |

### 2.3 对话（6）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| interrupt | Esc | ✅ Esc | — |
| clear | Ctrl+C | ✅ Ctrl+C（清草稿） | — |
| exit | Ctrl+D | ✅ Ctrl+D | — |
| retry | F5 / Alt+R | 🟡 `/retry` 命令（TUI），无键 | P2：加 F5 即可 |
| followUp | Ctrl+Enter / Ctrl+Q | ✅ Ctrl+Q | — |
| dequeue | Alt+Up / Shift+Up | ❌（Ctrl+X 是清空队列） | **P1**：取回比清空更有用 |

### 2.4 显示（5）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| thinking.toggle | Ctrl+T | ✅ Ctrl+T | — |
| thinking.cycle | Shift+Tab | ✅ Shift+Tab | — |
| tools.expand | Ctrl+O | ✅ Ctrl+O（批量展开） | — |
| tools.toggleVisibility | Ctrl+Shift+O | ❌ | P2 |
| display.reset | Alt+L | ❌ | P2：Textual 重绘偶发时有用 |

### 2.5 输入（7）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| editor.external | Ctrl+G | ❌ | P2：长 prompt 场景 |
| local.open | Alt+G | ❌ | N |
| clipboard.copyLine / copyPrompt | Alt+Shift+L / C | ❌ | P2 / N |
| clipboard.pasteTextRaw | Ctrl+Shift+V | ❌（终端原生粘贴可用） | N |
| clipboard.pasteImage | Ctrl+V | ❌（TUI 无图片附件） | P2：随图片能力走 |
| history.search | Ctrl+R | ❌ | **P1**：日常高频 |

### 2.6 模式（4）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| plan.toggle | Alt+Shift+P | 🟡* 只有 `/mode plan` | P2：加键位 |
| live.toggle | Ctrl+L | ❌ | N |
| stt.pushToTalk / stt.toggle | Space / — | ❌ | N |

### 2.7 界面（6）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| agents.hub | Alt+A | ❌ | N（做 subagent 也只做 HUD） |
| links.hint | Alt+O | ❌ | N |
| ask.hide | Alt+H | ❌（无 ask 工具） | N |
| approval.approve | Ctrl+Enter / Ctrl+Q | ✅* 弹窗内 `y`/Enter（allow）、`a`（always）、`n`/Esc（deny） | — |
| session.observe | — | ❌ | N |
| suspend | Ctrl+Z | ❌ | P2/N |

### 2.8 输入区/编辑器键位（03 §4.3，补充 10 行）

| 动作 | omp 键 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| submit / newline | Enter / Shift+Enter | ✅ / ✅ | — |
| newline 兜底 | Ctrl+J | ❌ | P2：终端差异大，值得加 |
| tab autocomplete | Tab | 🟡 TUI 有 `/` 命令提示；无参数级补全 | P2 |
| 复制选区 | Ctrl+C | ❌（Ctrl+C 清草稿；Textual 鼠标被关） | P2 |
| 编辑器 undo | Ctrl+- / Ctrl+_ | ❌（TextArea 原生 Ctrl+Z） | N |
| kill-ring yank | Ctrl+Y / Alt+Y | ❌ | N |
| 拼写候选 | — | ❌ | N |
| bash / python 模式 | `!` / `$` | ❌（写/执行走工具） | N |
| queue 简写 | `->` / `=>` | ❌ | N |
| `#` 快速动作 / `@` mention | `#` / `@` | ❌ / 🟡 REPL 有 `@` 补全，TUI 无 | N / P2 |

**键位小结**：核心四件套（Esc/Ctrl+C/Ctrl+D/Ctrl+Q）与 thinking/tools 开关**已对齐**；P1 三个 = 历史搜索、取回排队、双击 Esc 回退；其余 P2 或不做。

---

## 3. 浮层体系（69 → 35 行为项）与 25 个全屏 surface

omp 的三层体系（`01`/`05`）：**A1 内联替换编辑器槽**（审批、ask、选择器）、**B 贴底/居中锚定浮层**、**C 全屏 alt-screen**；核心规则是**单实例 + 无 raise**（重复请求 = 聚焦已开的，不叠第二个）、Esc 分层退、关闭后焦点还给打开者。

**ZenAgent 现状**：palette 骨架 + `_menus` 单实例 dict ✅；ModelPicker / SessionPicker / SettingsPicker / Confirm / RenamePrompt / ToolApproval ✅*；CommandPalette ✅；无全屏 surface；无 A1 内联槽（审批走 ModalScreen，与 omp"叠在 composer 上方、打字仍进草稿"不同，但可接受）。

### 3.1 浮层清单（35）

| # | omp 浮层 | 作用/层级 | ZenAgent 现状 | 必要性 |
|---|---|---|---|---|
| 1 | hook-selector | 扩展通用选择器 A1 | 🟡 扩展能注册命令，无对话框 API | P2：等扩展需求 |
| 2 | hook-input | 单行输入 A1 | ❌ | P2 |
| 3 | hook-editor | 多行编辑器 A1 | ❌ | N |
| 4 | approval-dialog | 工具审批 A1 | ✅* ToolApproval（ModalScreen） | 已补齐 |
| 5 | ask-dialog | ask 工具问答 A1 | ❌（无 ask 工具） | N |
| 6 | thinking-selector | 思考档选择 A1 | 🟡 `/thinking` 参数式 | P2：palette 复用成本极低 |
| 7 | theme-selector | 主题选择 A1 | ❌（REPL `--theme`） | P2 |
| 8 | tree-selector | 会话树 A1 | ❌ | P2 |
| 9 | history-search | 历史搜索 A1 | ❌ | **P1** |
| 10 | queue-mode-selector | 排队模式 A1 | ❌ | N |
| 11 | show-images-selector | 图片策略 A1 | ❌ | P2（随图片） |
| 12 | plugin-selector / 13 plugin-settings | 插件 A1 | ❌ | N |
| 14–18 | login / oauth / logout / session-account / reset-usage | 账号 A1 | ❌ | N |
| 19 | plan-save-overlay | 保存计划 A1 | ❌ | P2 |
| 20 | debug-selector | 调试 A1 | ❌ | N |
| 21 | live-visualizer | 语音 A1 | ❌ | N |
| 22 | btw-panel / 23 omfg / 24 cleanse | 侧问答/规则/看板 A2 | ❌ | N |
| 25 | report-panel | 停靠报告 A2/C | ❌（命令结果进 transcript） | P2 |
| 26 | session-info-overlay | 会话信息卡 B | 🟡 `/session` 文本 | P2 |
| 27 | jobs-panel | jobs 主从表 B | ❌ | N |
| 28 | model-picker | 临时模型 B | ✅ Alt+P | — |
| 29 | move-overlay | 移动会话 B | ❌ | N |
| 30 | mcp-add-wizard | 添加 MCP B | ❌（编辑 mcp.json） | P2 |
| 31 | btw-history-panel | 侧问答历史 B | ❌ | N |
| 32 | hotkeys sheet | 快捷键表 B | 🟡 `/hotkeys` 文本 | P2 |
| 33 | codex-reset-fireworks | 彩蛋 B | ❌ | N |
| 34 | error-banner | 常驻错误横幅 dock | ❌ | **P1** |
| 35 | subagent badge / 跳转 | subagent HUD dock | ❌ | N（除非做 subagent；届时 P2 简化） |

### 3.2 全屏 surface（21）

| 界面 | ZenAgent 现状 | 必要性 |
|---|---|---|
| `/settings` 设置中心 | 🟡 palette 版 | P2 |
| `/models` Model Hub | 🟡 临时 picker（无 provider 侧栏/角色模型） | N |
| `/agents` agent 定义管理 | ❌ | N |
| Agent Hub（subagent 名册） | ❌ | N（做 subagent 也只做 HUD） |
| agent transcript viewer | ❌ | N |
| `/usage` 仪表盘 | 🟡 `/usage` 文本 | P2 |
| `/context` 上下文查看器 | 🟡 `/context` 文本 | P2 |
| `/extensions` 控制中心 | ❌ | N |
| `/resume` 会话选择 | ✅（palette 版，工作树加操作） | — |
| `/copy` 复制选择器 | ❌ | N |
| esc-esc rewind 回退选择器 | ❌（有 `/undo`） | P2：双击 Esc + 复用 undo |
| plan review | ❌ | P2：看 plan 模式是否正式化 |
| `/pause` 暂停屏 | ❌ | N |
| advisor 配置 | ❌ | N |
| `/annotate` 代码审查 | ❌ | N |
| git UI | ❌ | N |
| ps / top | ❌ | N |
| standalone 会话 picker | ✅（`--resume` + picker） | — |
| setup speech | ❌ | N |
| debug viewers | ❌ | N |
| bench/cleanse 看板 | ❌ | N |

**浮层/全屏小结**：ZenAgent 用「palette + modal」覆盖了 omp 三层体系里**日常 90% 的入口**；缺的全屏界面基本都是 omp 为多 agent/多 provider/协作场景准备的，**不建议复刻**。真正值得补的只有错误横幅（P1）和历史搜索（P1），其余 P2。

---

## 4. 状态栏 27 + 2 段

ZenAgent 已实现 11 个 **segment 化** 状态段（`status/segments.py`）+ 3 个 preset + 宽度降级 ✅（omp 的 segment 契约已复刻）。

| # | omp 段 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| 1 | pi（品牌/运行指示） | ✅（`zen` / `◐`） | — |
| 2 | status（hook 文本） | ❌ | N |
| 3 | model | ✅（无 advisor/fast 后缀） | — |
| 4 | mode | ✅*（plan/ask/full） | — |
| 5 | path | ✅（max 40） | — |
| 6 | git（branch + 计数） | 🟡 只有 branch，无 dirty/staged/untracked | P2：数据可现算 |
| 7 | pr | ❌ | N |
| 8 | subagents | ❌ | N（随 subagent） |
| 9 | token_in / 10 token_out | 🟡 合并为 `tokens`（"N in / M out"） | — |
| 11 | token_total | ❌ | N |
| 12 | token_rate | ❌ | P2：需要流式计时 |
| 13 | cost | ✅ | — |
| 14 | context_pct | ✅（无 total/auto 图标） | — |
| 15 | context_total | ❌ | P2：可并入 context_pct |
| 16 | time_spent | ❌ | P2：需在 ChatController 记录 turn 时间，小改动 |
| 17 | time（时钟） | ❌ | N |
| 18 | session（id） | ❌ | N |
| 19 | hostname | ❌ | N |
| 20 | cache_read / 21 cache_write | ❌ | P2 |
| 22 | cache_hit（命中率） | ❌ | **P1**：`RunStatus.cache_read` 已有，补 `cache_write` 字段即可 |
| 23 | session_name | ✅（工作树后接 brief 名） | — |
| 24 | usage（provider 配额） | ❌ | N：catalog 无配额 API |
| 25 | collab / 26 stream / 27 vim | ❌ | N |
| +1 | subagent-badge（动态） | ❌ | N |
| +2 | jobs（动态） | ❌ | N |

**状态段小结**：7 ✅ / 3 🟡 / 19 ❌；P1 只建议加 `cache_hit`（+ 可选 `time_spent`、`token_rate`），**不要把 27 段做全**。

---

## 5. composer 8 种形状

| 形状 | ZenAgent 现状 | 必要性 |
|---|---|---|
| band（默认） | ✅（状态带 + `╰─` gutter + 无框输入） | — |
| box / claude / pi / borderless / rule / field / rail | ❌ | N：v1 已决定只做 band（`07` §6） |

保持现状；若未来加形状，先保留 `shape → 状态栏挂点` 抽象即可。

---

## 6. 转录 24 行类型

已对齐：user / assistant / thinking / tool call+result / error / usage / diff / 通用 notice（+ welcome 是 ZenAgent 额外项）。

| # | omp 行类型 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| 1 | user | ✅ 整行填色块 | — |
| 2 | developer/synthetic | ❌ | N |
| 3 | assistant | ✅ markdown | — |
| 4 | thinking（可见） | ✅ ThinkingRow（可折叠） | — |
| 5 | thinking（隐藏脉冲） | ❌（有 work indicator） | N |
| 6 | tool call + result | ✅ ToolCard | — |
| 7 | 工具组（连续 read） | ❌ | P2：读多个文件时降噪 |
| 8 | `!` bash 执行 | ❌ | N |
| 9 | `$` python 执行 | ❌ | N |
| 10 | 内联错误 | ✅ NoticeRow(error) + 工具卡失败态 | — |
| 11 | warning notice | 🟡 无独立 warning 样式 | P2：一个 color 参数 |
| 12 | status notice | 🟡 通用 notice 有，无 toast 生命周期 | P2 |
| 13 | 状态 pill | ❌ | N |
| 14 | compaction 分隔线 | ❌（REPL 压缩成功有文本提示；TUI 无分隔线/提示） | **P1**：自动压缩的可解释性 |
| 15 | handoff/branch 摘要 | ❌ | N（无 handoff） |
| 16 | custom/hook 消息 | ❌ | N |
| 17 | skill 提示 | 🟡 有 skills 注入，无 callout 行 | P2 |
| 18 | 图片 | ❌（TUI 无附件） | P2（随图片能力） |
| 19 | diff | ✅ 工具卡内渲染 | — |
| 20 | usage 行 | ✅ UsageRow | — |
| 21 | todo 卡 | ❌ | P1/P2（随 todo 系统） |
| 22 | task/subagent 卡 | ❌ | N（随 subagent） |
| 23 | notice 卡（严重级别） | 🟡 NoticeRow 简化版 | P2 |
| 24 | 轻量标记行 | ❌ | N |

**行类型小结**：8 ✅ / 3 🟡 / 13 ❌；P1 只有 **compaction 分隔线**，其余按依赖（images/todo/subagent）走。

---

## 7. 工具渲染器（60 → 11 家族）

ZenAgent 有 9 个内置工具 + MCP 工具，TUI 统一走 `ToolCard`（单行 + 状态点/底色 + Enter 展开 + diff 渲染）。

| omp 家族 | ZenAgent 现状 | 必要性 |
|---|---|---|
| Bash | 🟡 通用卡；命令在 args、无 exit/timeout/duration 元信息 | **P1**：标题行拆出命令/退出码 |
| Read（单文件） | 🟡 通用卡；无行号范围/截断提示 | P1/P2 |
| Read（多文件组） | ❌ | P2：连续读降噪 |
| Write | 🟡 通用卡；无内容预览 | P2 |
| Edit/Create/Delete | 🟡 有 diff 正文；无 `⟨+8/-1⟩` 统计/动词标题 | **P1**：diff 统计很便宜 |
| Grep | 🟡 通用卡；无命中数/文件数 | P1/P2 |
| Glob/Find | 🟡 通用卡 | P2 |
| Task（subagent） | ❌ | N（无 subagent） |
| Todo | ❌ | P1/P2（随 todo） |
| Ask | ❌ | N |
| 兜底 | ✅ 通用卡（已对齐 v1 目标） | — |
| wait/job/async | ❌ | N |

**结论**：不做 60 个专用渲染器；只把 **bash / edit / read / grep** 四张卡的标题与元信息按 omp 语法补上，收益最大。

---

## 8. dock 17 层

| # | omp dock 层 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| 1 | chatContainer | ✅ 转录区 | — |
| 2 | pendingMessagesContainer | 🟡 状态带显示 `N queued`，无消息列表 HUD | P2 |
| 3 | todoContainer | ❌ | P1/P2（随 todo） |
| 4 | subagentContainer | ❌ | N（随 subagent） |
| 5 | btwContainer | ❌ | N |
| 6 | reportContainer | ❌ | N |
| 7 | omfgContainer / 8 cleanseContainer | ❌ | N |
| 9 | errorBannerContainer | ❌ | **P1**：sticky 错误 |
| 10 | modelCycleContainer | ❌ | N（切换已有状态带反馈） |
| 11 | deferredCommandContainer | ❌ | N |
| 12 | progressHudContainer | ❌ | N |
| 13 | statusContainer（工作行） | ✅ work_indicator（⎋ Working…） | — |
| 14 | attachmentChipsContainer | ❌（REPL 有图片，TUI 无） | P2（随图片） |
| 15/17 | hookWidget above/below | ❌ | N |
| 16 | editorContainer | ✅ prompt input | — |
| — | #statusHost（状态带宿主） | ✅ 状态带在输入区上方（band 形状） | — |

**结论**：dock 的「主轴 + 工作行 + 状态带」已复刻；只建议补 **错误横幅**（+ 可选排队 HUD），其余 13 层不做。

---

## 9. Esc 语义链（13 级）与菜单栈

| 级 | omp 语义 | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| 1 | 取消提交 | ❌ | N |
| 2 | 子 agent 视图退出 | ❌ | N |
| 3 | collab guest | ❌ | N |
| 4 | 取消待提交 | ❌ | N |
| 5 | 排队消息退回编辑器 | ❌ | **P1**（同 Alt+Up） |
| 6 | abort bash | ✅ 工具级 timeout/abort | — |
| 7 | bash 模式退出 | ❌ | N |
| 8 | abort eval | ❌（无 `$` eval 模式） | N |
| 9 | python 模式退出 | ❌ | N |
| 10 | 结束异步等待 | ❌ | N |
| 11 | abort 本回合 | ✅ Esc → chat.abort() | — |
| 12 | **有草稿时什么都不做** | ✅ action_interrupt 不动草稿 | — |
| 13 | 空草稿双击 Esc → rewind | ❌ | **P1**：复用 `/undo` 即可 |

**菜单栈**：单实例 ✅（`_menus` dict，重复 open 不叠）；omp 的「no raise / 重新挂顶 / 关闭后焦点还原」以及弹层内 Esc 三层（清搜索→退子视图→关面板）为 🟡，列 P2。

---

## 10. 横切系统（比 UI 更重要）

| 系统 | omp | ZenAgent 现状 | 必要性 |
|---|---|---|---|
| **权限模式** | approval-mode 文档（本调研快照未展开细节；审批对话框见 05） | ✅* `plan/ask/full`（gate + Runtime + 三前端 + TUI 弹窗） | 已补齐 |
| **会话管理** | 列表/恢复/fork/删除/改名/pin/树 | ✅* 列表/恢复/fork/删除/改名/brief 命名；❌ pin/树 | 树 P2，pin P2 |
| **plan 模式** | plan + plan review | ✅* plan（只读 + prompt note）；❌ review | review P2 |
| **todo** | todo 工具 + HUD + 提醒 | ❌ | **P1**：长任务价值最高 |
| **subagent** | task 工具 + HUD + Agent Hub + 成本归属 | ❌ | P1（v2 方向 B，属于项目级） |
| **后台 jobs** | /jobs + jobs pill | ❌ | N |
| **context 治理** | /compact /shake /handoff + viewer | ✅ compact/自动压缩；❌ shake/handoff/viewer | P2 |
| **usage/成本** | dashboard + 配额 | 🟡 文本统计 + catalog 单价；无配额 API | P2 |
| **设置/主题/键位** | 全屏中心 + 可重映射 | 🟡 config.toml + settings palette + REPL theme/keybindings；TUI 键位固定 | P2 |
| **图片** | 附件 + 内联显示 | 🟡 REPL `--image` / `/image`；TUI 无 | P2 |
| **MCP** | 管理 + add 向导 | ✅ 发现/重连/热替换；❌ add 向导 | P2 |
| **扩展** | 插件市场/UI | ✅ 文件式 register(api)；❌ 市场/UI | N |
| **skills/templates** | registry + 注入 | ✅ 本地 SKILL.md + templates；❌ registry | N（registry 不做） |
| **导出/RPC/SDK** | export/copy/share | ✅ md/html 导出、JSONL RPC、SDK 文档 | — |
| **evals** | bench/ratchet/cleanse | ✅ 18 条行为评测 + runner；❌ 榜单/爬山 | N |

---

## 11. 必要性总表（按优先级）

### P0（无）
omp 清单里**没有**必须立刻补的能力。当前该做的是把工作树里的 modes/sessions/审批收尾、跑全量闸门并提交。

### P1（值得做，8–10 项）
1. 工具卡标题/元信息（bash/edit/read/grep）。
2. compaction 分隔线。
3. Esc/队列交互子集（双击 Esc 回退、Alt+Up 取回、Ctrl+R 历史搜索）。
4. 错误横幅。
5. `/tools`、REPL `/retry`、`/pin`。
6. 状态段 `cache_hit`（+ `time_spent`/`token_rate`）。
7. TUI 图片附件 + attachment chip（条件：vision 常用）。
8. todo 工具 + 简化 HUD（条件：走向长任务/自主性）。
9. plan review（条件：plan 模式正式化）。
10. 历史搜索或外部编辑器二选一（与 3 合并可减项）。

### P2（可选，约 15 项）
`/shake`、`/handoff`、`/pin`、`/btw`、`/move`、`/shake`、`/trace` TUI、`/copy`、context viewer、usage dashboard、settings 全屏、thinking/theme selector、session info overlay、hotkeys sheet、MCP add 向导、session tree/branch、model preset、多根工作区、external editor、clipboard、display.reset、tools toggleVisibility、cycle model backward、image 策略、todo HUD 增强、plan review 全屏、菜单 raise/focus、Esc 弹层分层。

### N（明确不做，约 45+ 项）
见 §0.2。

---

## 12. 与 v2 的关系

如果 v2 按上一轮讨论的三个方向推进，omp 对标的结论是：

- **方向 A「可控自主性」**（权限模式 + 沙箱 + 审批）：omp 的 approval/plan/审批弹窗**已经在这轮收尾中补齐**；本调研快照没有覆盖 omp 的沙箱方案（87 命令与 UI 清单里也没有对应入口），所以这块**没有可抄的现成功能**，需要自己设计。
- **方向 B「多代理」**：omp 的 task/subagent/HUD/Agent Hub 是唯一值得系统参考的部分；但只建议抄 **task 工具 + 单层 spawn + HUD 行 + 成本归属**，不抄完整 Hub。
- **方向 C「平台化」**：omp 的 marketplace/installer/debug 面板等都不值得抄；Terminal-Bench 适配属于外部生态，见 `docs/eval-landscape.md`。

---

## 附录 A：旧 `08` 状态列的修订

08 的第三列写于 TUI P0–P4 之前，以下条目已变化（以本文档为准）：

| 命令 | 08 旧状态 | 现在 |
|---|---|---|
| `/plan` | ⏳ 未做 | ✅*（plan mode；review 仍 ❌） |
| `/delete` | ⏳ 未做 | ✅*（TUI picker d + Confirm） |
| `/rename` | ⏳ 未做 | ✅*（sidecar 标题） |
| `/retry` | ⏳ 未做 | 🟡 TUI 已有命令；REPL 未对齐 |
| `/context` `/usage` `/session` `/hotkeys` `/export` `/mcp` `/skills` | ✅ | 仍 ✅（形态为文本/palette） |
| `/settings` | ✅ | 🟡（palette 版，非全屏中心） |
| `/fork` `/new` `/clear` `/compact` `/resume` | ✅/⏳ | ✅（TUI + REPL 按入口不同） |
| `/mode`（不在 omp 表） | — | ✅* ZenAgent 新增 |

另：08 的「⏳ 里最值得做的四个：/undo /retry /todo /tree」中，`/undo` 早已实现（只是 omp 没有此命令），`/retry` TUI 已有，`/todo` 与 `/tree` 按本文档分别 P1 / P2。

## 附录 B：证据与局限

- **证据**：代码为 `HEAD 9606d33` + 工作树（`app/modes.py`、`app/permissions.py`、`app/runtime.py`、`cli/tui/screens/modals.py`、`cli/tui/app.py`、`app/commands.py`、`status/segments.py` 等）；omp 侧为 `01–08` 的调研快照（源码级）。
- **局限**：omp 快照对应其 main 分支某一时点，后续版本可能变化；本表不追踪更新。ZenAgent 工作树未提交部分以 2026-10-30 20:50 为准，提交后请把 ✅* 改为 ✅。
- **未纳入**：omp 的 60 个渲染器逐个细节、69 个浮层的文件级实现差异、8 种 composer 的像素级渲染——这些只影响"复刻程度"，不影响"是否要做"的判断。
