# TUI × GUI 功能对齐计划

日期：2026-11-17 · 状态：待实施 · 基线：[四端能力矩阵](../../frontend-matrix.md)

## 范围裁决（2026-11-17）

**swarm 与 zen 不进 TUI**（暂缓，非永久）。房间/fleet 的多代理编排是桌面形态的能力：
GUI 用侧栏 + 房间页承载它，TUI 的单列会话流没有对应的呈现位；为它造一套 TUI 房间 UI
的投入与使用频率不成比例。裁决只关 UI，不影响协议：swarm/zen 的 wire op 与 app 层保持
GUI 专属现状，矩阵中相应格标注 TUI「裁决排除」。

对齐范围收敛为四族——**管理面板族、观察视图族、goal 面板、输入通道补齐**：

| 族 | TUI 现状 | GUI 蓝本 |
|---|---|---|
| 管理面板 | API key 录入框散件、无 plugins/limits UI | Secrets（ModelSettings/Settings 族）、PluginSettings、LimitSettings |
| 观察视图 | 无 worktree 总览、无 runs 视图 | WorktreePanel、runs op（GUI 也未接，四端共同缺口） |
| goal | 文本命令 + HUD + pause 开关 | GoalChip + GoalPanel（目标/check/预算/verdict） |
| 输入通道 | follow_up 仅 TUI 有——**反向项**：REPL 缺 | follow_up/steer 双通道 GUI 已齐 |

原计划 Phase 0（`SwarmHost` 进程内提取）随裁决取消，不再列入。

## 原则

1. **GUI 是蓝本**：能力集合与语义对齐（GUI 能查什么改什么，TUI 就能查什么改什么）；
   呈现遵循 TUI 惯例（screen/picker/HUD/sidebar section），不复刻 GUI 布局。
2. **进程内直连**：TUI 不经 RPC，直接调 app 层（`app/probes.py`、plugins、`app/runs.py`、
   `app/workspace.py::worktree_overview`）。零新增 wire op。
3. **registry 驱动**：limits 屏与 GUI 的 LimitSettings 同源（`zenagent/limits.py::ALL_KNOBS`），
   旋钮全集含 swarm/goal 项——那是配置面，不违反范围裁决（裁决排除的是房间/fleet UI）。
4. 每阶段独立可交付，一个阶段一个 PR 粒度。

## 明确不做

- **swarm / zen / gate**：范围裁决（上文）。
- **terminal**：TUI 本身就是终端，by design。
- **多项目驻留**：TUI 单进程绑 cwd 是架构事实；需要多项目时开多个 TUI 实例。不假装支持。
- **fs 文件浏览**：TUI 用户手边就是 shell，`@` 补全已走 fs_index；列为观察项，有反馈再做。
- **headless 交互化**：by design，出口是 `zen rpc`。

---

## Phase A · `/secrets` 管理屏

对齐 GUI 的 secrets 面（model/search providers 的 list/set/clear/test，`ops_secrets.py` 同语义）。

**工作项**

1. 新 `tui/screens/secrets_manager.py`（或扩展现有 `screens/secrets.py`）：按 provider 分组的
   key 列表，逐项 set（SecretInput 掩码录入，复用现有屏）/ clear / test（`app/probes.py`
   进程内直调，显示连通结果）。
2. 把现有零散的 API key 录入（model picker 缺 key 提醒、search provider 设置）收编为该屏的
   入口，消除两处录入路径。
3. `/secrets` 命令注册进 `app/commands.py`（共享注册表，REPL 免费获得文本降级渲染）。

**验收**：TUI 内完成「查缺 key → 录入 → test 通过」闭环；模型 picker 的缺 key 提醒直达该屏。

**测试**：`tests/test_tui_secrets.py`（列表装配、set 写盘 0600、test 调用 probes）。

## Phase B · `/plugins` 管理屏

对齐 GUI PluginSettings（list/install/uninstall/enable/disable；configure/凭据抽屉视 app 层
支持度跟进）。app 层已就绪（plugins ops 的服务端实现即走它），TUI 只缺 UI。

**工作项**

1. `tui/screens/plugins.py`：已装插件列表（enable/disable 开关）、官方目录浏览 + 安装。
2. `/plugins` 命令。TUI 目前只能运行插件注册的 slash 命令，屏内提示这一关系。

**验收**：TUI 内装/卸/开关一个插件并看到 slash 命令出现/消失。

**测试**：`tests/test_tui_plugins.py`。

## Phase C · `/limits` 预算屏

对齐 GUI LimitSettings：registry 驱动（`ALL_KNOBS`），get/set/reset 语义一致（0=关闭上限），
进程内走 `app/limits_apply.py`。

**工作项**

1. `tui/screens/limits.py`：分组旋钮编辑（task/goal/swarm/… 按注册表分组），即时生效 +
   reset 单旋钮。
2. `/limits` 命令；`/settings` 的 goal_pause 开关保留不动。

**验收**：TUI 内改任一旋钮即刻生效，行为与 GUI 同一旋钮一致。

**测试**：`tests/test_tui_limits.py`（旋钮读写、0=关闭语义）。

## Phase D · 观察视图：`/runs` + `/worktrees`

1. **`/runs`**：runs 账本列表 + 详情（`app/runs.py`、`app/subagent_runs.py`）。这是**四端第一个**
   runs 视图——信息架构在此验证后，GUI 随后接同一个 `runs` op（服务端伴随项）。
2. **`/worktrees`**：worktree 总览只读屏（`app/workspace.py::worktree_overview`）。切换 =
   提示重启（TUI 单进程绑 cwd），UI 明示。

**验收**：TUI 能查最近 runs（kind/agent/status 过滤）与 worktree 全景。

**测试**：`tests/test_tui_runs.py`、`tests/test_tui_worktrees.py`。

## Phase E · goal 详情面板

补齐 GUI GoalPanel 的结构化呈现：目标、check 进度、budget 消耗、verdict。

**工作项**

1. `tui/screens/goal.py`（或增强 sidebar goal section）：从 `app/goal.py` 的状态直读渲染，
   pause/resume/clear 走现有命令路径。
2. GoalHud 点击进入面板（沿用 HUD → screen 的既有惯例，如 context breakdown）。

**验收**：GUI GoalPanel 展示的字段 TUI 全部可达。

**测试**：`tests/test_tui_goal_panel.py`。

## Phase F · 顺手项（不阻塞）

- **REPL follow_up + 队列管理**：`ChatController.follow_up` 已有，只差命令/键位入口；
  steer 已有。
- 矩阵维护：每阶段合入更新 [frontend-matrix.md](../../frontend-matrix.md) 格与日期。

## 依赖与顺序

```
A（secrets）─► B（plugins）─► C（limits）─► D（runs/worktrees）─► E（goal）
F 随时可做
```

A→B→C 面板族共用屏框架惯例，先后只为评审节奏；D/E 无依赖，可提前。

## 风险

1. **REPL 降级渲染**：新屏注册进共享注册表后，REPL 侧需要文本渲染分支
   （`cli/render.py::render_command_result` 惯例），否则 REPL 拿到无输出的静默命令。
   每个屏的 PR 必须带 REPL 分支。
2. **旋钮全集的语义漂移**：`/limits` 屏必须吃注册表的元数据（范围/默认/描述），
   不许硬编码旋钮名——GUI LimitSettings 已是 registry 驱动，TUI 照抄数据源。
3. **矩阵同步**：缺口条目消化后移入矩阵，不积灰（维护规则第 4 条）。
