# omp 功能清单（Feature Inventory）

> 目的：给"我们要复刻哪些、放弃哪些"提供完整底账。
> 数据来源：`packages/coding-agent/src/slash-commands/builtin-*.ts`（87 个顶层命令）、`packages/tui/src/app-keybindings.ts`（45 个应用动作）、`packages/tui/src/overlays/`（69 个浮层）、`packages/tui/src/apps/`（25 个独立界面）、`packages/tui/src/tools/`（60 个工具渲染器）。

## 1. 视觉/交互资产总账

| 资产 | 数量 | 说明 |
|------|------|------|
| 主屏表面 | 1 | dock + 转录 + composer（`02-layout.md`） |
| composer 外形（shape） | 8 | band / box / claude / pi / borderless / rule / field / rail |
| 状态栏 segment（可配置） | 27 + 2 动态 | 6 个 preset（`03-status-line-and-composer.md`） |
| 浮层（overlay） | 69 个 .ts | 底部浮层 / 全屏浮层 / 输入区替换（`05-overlays-and-surfaces.md`） |
| 独立全屏界面（apps） | 25 | session picker、git UI、ps-top、live visualizer、log viewer… |
| 转录行类型 | ~20 | user / assistant / thinking / tool / error / notice / compaction / image / diff / usage… |
| 工具专用渲染器 | 60 | read/write/edit/bash/grep/glob/ast/todo/task/ask/web… |
| 顶层 slash 命令 | 87 | 7 个域 |
| 应用级动作（键位） | 45 | `app.*` |
| 设置项域 | ~20 | Interaction / Display / Tools / Providers / Model / Session… |

## 2. 顶层 slash 命令（87 个，按域）

### 2.1 modes（21）——改变 agent 行为

| 命令 | 作用 | 复刻优先级 |
|------|------|-----------|
| `/model` | 切本会话模型 | ★★★ |
| `/modelpreset` `/switch` | 模型预设（角色模型 + thinking 等级）存取 | ★★ |
| `/effort` | thinking/effort 等级 | ★★★ |
| `/plan` `/plan-review` | 只读规划模式 + 计划复核界面 | ★★ |
| `/settings` | 全屏设置中心 | ★★★ |
| `/setup` `/providers` | provider 登录配置向导 | ★★（只读展示即可） |
| `/goal` `/guided-goal` | 自主目标模式（持续目标 + 预算） | ★ |
| `/vibe` | 快速只读 worker 会话 | ★ |
| `/fast` `/slow` `/extended-context` | provider 服务档位/上下文窗口 | ★ |
| `/computer` `/skillful` | 会话级开关 | ★ |
| `/security` | 安全扫描域（plan/scan/show/import/validate/compare/disposition） | ✗ |
| `/ratchet` `/prewalk` `/loop` `/queue` | 评测爬山 / 模型交接 / 循环 / 队列 | ✗ |

### 2.2 session（20）——会话与周边

| 命令 | 作用 | 优先级 |
|------|------|--------|
| `/session` | 会话管理（含 info/rename/delete/fork…） | ★★★ |
| `/resume` | **选会话恢复**（全屏 picker，带 pin / 排序 / 搜索） | ★★★ |
| `/new` `/fork` `/branch` `/tree` | 新会话 / 分叉 / 回退到某条消息 / 会话树 | ★★（branch+tree 是 omp 亮点） |
| `/context` | 上下文占用浏览（内容明细） | ★★★ |
| `/usage` | provider 用量与限额 | ★★ |
| `/jobs` | 后台作业列表（可杀） | ★★ |
| `/todo` | 任务清单（HUD + 编辑） | ★★★ |
| `/hotkeys` | 快捷键参考页 | ★★ |
| `/tools` | 当前可见工具清单 | ★ |
| `/agents` `/hub` | Agent Hub（子代理名册 + 焦点切换） | ★★ |
| `/git` | 全屏 Git UI（diff / 暂存 / commit 编辑） | ★ |
| `/extensions` | Extension Control Center（已装能力总览） | ★ |
| `/local` | 打开会话内 `local://` 文件到外部编辑器 | ✗ |
| `/stats` `/changelog` `/login` `/logout` `/mcp` | 统计/更新日志/登录/MCP | ★ |

### 2.3 lifecycle（25）——生命周期与上下文治理

`/clear`（原地清上下文，保留会话）、`/compact`（压缩，多种模式）、`/shake`（丢重内容：工具结果/大块/图片/thinking 四种粒度）、`/handoff`（总结成交接文档后原地压缩）、`/retry`、`/rename`、`/move`、`/wt`（迁到 worktree）、`/add-dir` `/remove-dir` `/dirs`（多根工作区）、`/pin`、`/btw`（侧问答）、`/debug`、`/memory`、`/cleanse`、`/tan`、`/omfg`、`/fresh`、`/restart`、`/exit`、`/ssh`。

> 值得抄的三个：**`/shake` 的分级瘦身**（elide / images / thinking 各一条命令）、**`/btw` 侧问答**（不污染主上下文）、**`/handoff`**。

### 2.4 control（6）——运行时控制

`/pause`（冻结所有 agent，含子代理与 advisor）、`/force`（强制下一轮用某工具）、`/live`（实时语音）、`/record`（录屏并可 `omp play` 回放）、`/forget-approvals`（清掉"记住的批准"）、`/quit`。

### 2.5 collaboration（11）

`/export`（HTML 导出）、`/share`（加密链接）、`/collab` `/join` `/leave`（实时共享会话）、`/dump` `/trace`（把 transcript/LLM 请求打包或看 trace）、`/copy`（挑一段复制）、`/open`（打开对话里的链接）、`/browser`、`/advisor`。

### 2.6 marketplace（3）+ skills（1）

`/marketplace`、`/plugins`、`/reload-plugins`、`/skills`——插件/技能市场。**复刻可完全跳过。**

## 3. 应用级键位（45 个 `app.*`，按用途分组）

- **会话**：new / resume / fork / delete / rename / pin / unpin / tree / observe / togglePath / toggleSort
- **模型**：cycleForward / cycleBackward / select / selectTemporary
- **对话**：interrupt / clear / exit / retry / message.followUp / message.dequeue
- **显示**：thinking.toggle / thinking.cycle / tools.expand / tools.toggleVisibility / display.reset
- **输入**：editor.external / local.open / clipboard.{copyLine,copyPrompt,pasteTextRaw,pasteImage} / history.search
- **模式**：plan.toggle / live.toggle / stt.{pushToTalk,toggle}
- **界面**：agents.hub / links.hint / ask.hide / approval.approve / session.observe / suspend

## 4. 全屏界面清单（25 个 app/ 文件 + 浮层里的全屏菜单）

| 界面 | 进 | 布局 | 优先级 |
|------|----|------|--------|
| Settings | `/settings` | 左分组列表 + 右详情 | ★★★ |
| Model Hub | `/model`（非临时） | 左侧 provider 侧栏 + 右侧模型表 | ★★★ |
| Model Picker（临时） | `Alt+P` | 底部浮层，不是全屏 | ★★★ |
| Session Selector | `/resume` | 列表 + 搜索 + pin + 排序 | ★★★ |
| Agent Hub | `Alt+A` | 名册 + 每个 agent 的状态/模型 | ★★ |
| Extension Control Center | `/extensions` | 顶部 provider tab + 左列表 + 右详情 | ★★ |
| Usage Dashboard | `/usage` | 多块指标 + 图表 | ★★ |
| Context Viewer | `/context` | 占用条 + 明细列表 | ★★★ |
| Todo HUD / 编辑 | `/todo` | 常驻 HUD（几行）+ 展开 | ★★★ |
| Plan Review | plan 模式 | 全屏计划复核 | ★★ |
| Tree Selector | 双击 Esc | 会话树 | ★ |
| History Search | `Ctrl+R` | 编辑器槽内联列表（**不在浮层栈**，见 05） | ★★ |
| Git UI | `/git` | 左暂存侧栏 + 右 diff + 底部 commit 编辑 | ★ |
| ps / top | `/jobs` 相关 | 进程/作业表 | ★ |
| Live Visualizer | — | 实时面板 | ✗ |
| Debug（log viewer / raw SSE / protocol probe / terminal info） | `/debug` | 各自独立 | ✗ |
| Setup wizard | `/setup` | 多步向导 | ★ |
| Login / OAuth selector | `/login` | 浮层 | ★ |

## 5. 转录行类型（~20，详见 04）

user · assistant（prose / narration）· thinking · tool call + result · bash execution · eval execution · error · notice / divider · compaction summary · image · diff · table chart · SVG/figure · usage row · todo reminder · skill message · advisor message · hook message · TTSR notification · recap notice · late diagnostics · stripped-tool placeholder

## 6. 值得复刻 vs 放弃（给本项目的取舍）

**必做（构成"omp 感"的最小集）**
1. band 外形（状态带在输入区上方，无边框）+ `╰─ ` 前缀
2. 状态栏 segment 化（27 段里选 8-10 段）+ 宽度降级
3. 转录行类型里 8 种：user / assistant / thinking / tool call+result / error / notice / usage / compaction
4. 工具单行卡（`▸ name args · status · ms` + 展开）
5. 底部浮层选择器（模型/会话/历史）——一套骨架复用三次
6. 全屏 Settings + Context Viewer + Todo HUD
7. 键位四件套与 Esc 语义链
8. 菜单栈单实例规则

**可简化**
- composer 只做 band（+ borderless 降级），放弃另外 6 种
- 全屏界面只做 3 个（Settings / Context / Session picker）
- 工具渲染器只做通用兜底卡 + bash / read / edit / grep 4 个专用

**不做**
- 插件市场 / 技能市场 / 安全扫描 / 语音 / 录屏 / collab / worktree / git UI / ps-top / debug 面板 / Agent Hub / goal·vibe·ratchet 等模式

## 源码对照

- `packages/coding-agent/src/slash-commands/builtin-*.ts` — 87 个顶层命令的定义（按域分文件）
- `packages/tui/src/app-keybindings.ts` — `KEYBINDINGS`（45 个 `app.*`）
- `packages/tui/src/overlays/` / `packages/tui/src/apps/` — 浮层与全屏界面
- `packages/tui/src/tools/` — 60 个工具渲染器
- `packages/tui/src/chat/` — 转录行类型
