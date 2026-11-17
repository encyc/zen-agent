# TUI × GUI 功能对齐计划

日期：2026-11-17 · 状态：待实施 · 基线：[四端能力矩阵](../../frontend-matrix.md)

## 背景

GUI 以 RPC 为背骨长出了 swarm/zen/管理面板全家桶，TUI 停留在单会话编排。矩阵盘点出
TUI 缺席的能力域：swarm 全套、zen fleet、limits 编辑、fs/worktree/runs 视图、secrets/plugins
管理面。本计划以 GUI 为蓝本把 TUI 对齐，顺带补两个四端共同的服务端缺口（gate 状态、runs 视图）。

关键架构事实（决定计划的形状）：

- **TUI 是进程内直连**（`app/bootstrap.py::prepare` → `Runtime`/`ChatController`），不经过 RPC。
  对齐的方式不是给 TUI 加协议，而是把「只在 RpcHost 里装配的能力」提升到前端中立处。
- **`SwarmManager` 构造已前端无关**（`app/swarm/manager.py::SwarmManager.__init__` 的
  `write_frame`/`event_sink`/`approval_router` 都是注入回调），但装配入口只在
  `cli/rpc/host.py::RpcHost._swarm`。zen fleet 同理（见
  [orchestration-parity](2026-11-13-orchestration-parity-design.md) 分叉表 #1）。
- **TUI 的 mode 与 GUI 的 mode 是两个轴**：TUI `/mode` 是权限轴（plan/ask/auto，
  `app/modes.py`），GUI 的 ModeSwitcher 是工作区轴（coding/zen/swarm，`app/workspace_state.py`
  持久化）。TUI 需要新增的是工作区轴，不动权限轴。

## 原则

1. **GUI 是蓝本**：功能集合与交互语义对齐 GUI（切模式 → 发帖 → 看任务 → 审批 → works 闭环）；
   视觉呈现遵循 TUI 既有惯例（screen/picker/HUD/sidebar），不复刻 GUI 布局。
2. **in-process 优先**：TUI 直连 app 层对象，零新增 wire op；需要的公共提升在 app 层做，
   RpcHost 改为委托方。
3. **复用优先于新建**：审批复用 TUI 模态（`request_tool_approval`），transcript 渲染复用
   subagent 换装视图机制（`tui/app_parts/views.py`），编辑屏复用 `screens/editors.py` 惯例。
4. **阶段即依赖序**，每阶段独立可交付、独立 PR 粒度；Phase 4 与 1–3 并行无依赖。

## 范围外

- terminal 面板——TUI 本身就是终端。
- headless 交互化——by design，出口是 `zen rpc`。
- REPL 深度对齐——列在 Phase 5 顺手项，不阻塞主线。
- GUI 侧新功能——只在「服务端伴随项」交叉引用，不在本计划内实施。

---

## Phase 0 · 地基：swarm/zen 进程内可托管

**问题**：`SwarmManager`/zen fleet 只在 `RpcHost` 装配，`bootstrap.prepare()` 完全不沾编排器。
这是 TUI 对齐的最大单项前置。

**工作项**

1. **提取 `SwarmHost`**（新 `app/swarm/host.py`）：把「按 `project_key` 建/取/销 SwarmManager」
   的逻辑从 `RpcHost._swarm` 挪进 app 层；`RpcHost` 改为委托。manager 生命周期
   （project close 时的显式 teardown、host close 时的批量 close）随之迁移。
2. **zen fleet 装配同样处理**。与 orchestration-parity 的分叉表 #1（构造入口）合并做：
   那边已判定「要修」，本阶段提供落点。
3. **事件面中立化**：`write_frame` 回调接受任意 sink。TUI 提供自己的 frame sink
   （转发进 Textual message queue）；实时事件复用 `app/swarm/events_tap.py`。
4. **审批面接线**：房间的 approval/ask 路由到 TUI 现有模态——`approval_router` 注入
   TUI 的 `request_tool_approval` / ask 协议。注意 GUI 已处理过的并发归属问题
   （`desktop/src/state/pendingProject.ts` 按项目+会话归位），TUI 需要类似的队列化：
   多 role 并发审批 vs TUI 单模态。

**验收**：TUI 进程内能启用一个房间并跑通一个 role 任务：审批弹 TUI 模态、事件进 TUI、
RpcHost 行为不变（既有 `tests/test_rpc_*` 全绿）。

**测试**：`tests/test_swarm_host.py`（装配/按项目隔离/teardown）；既有 swarm/rpc 测试回归。

## Phase 1 · swarm 核心 UI

对齐 GUI 的 SwarmSidebar / RoomPage / SwarmRoster / SwarmTasks / SwarmWorks / RoomComposer。

**工作项**

1. **工作区模式轴**：引入 coding/swarm(/zen) 切换，持久化走 `app/workspace_state.py`
   （与 GUI 同一份状态）。入口：`/workspace` 命令或模式切换 picker；权限轴 `/mode` 不动。
2. **房间屏（RoomScreen）**：全局消息流 + 任务索引。swarm 工作区模式下主区即房间屏。
   **长房间流控**：GUI 有 transcript 挂载窗口（`desktop/src/state/transcriptWindow.ts`），
   TUI 需要等价的尾部窗口 + 向上翻页，否则长房间会拖垮 Textual。
3. **roster / roles**：角色列表进侧栏 section；角色编辑器复用 `screens/editors.py` 惯例，
   覆盖 create/update/delete/copy（进程内直调 SwarmHost，对应 GUI 的 `swarm.roles.*`）。
4. **works 面板**：work items 列表 + 详情（list/score/close/reopen），侧栏 section + 详情屏。
5. **房间发帖 lane**：swarm 模式下 submit 走 supervisor dispatch（与 GUI 同路径，
   `app/dispatch.py`），`@角色` 引用提示接进现有 hint 条（`tui/app_parts/hints.py`）。
6. **任务详情**：role run 的 transcript 用 subagent 换装视图机制渲染（顶部 banner + Esc 返回）。

**验收**：TUI 完成与 GUI RoomPage 等价的闭环——切工作区 → 发帖 → 看任务实时跑 →
审批 → works 评分/关闭；`/help` 与侧栏可见全部新入口。

**测试**：`tests/test_tui_swarm.py`（模式切换与持久化、发帖走 dispatch 路径、roster/works
操作）；流控行为（窗口大小上限）单测。

## Phase 2 · 预算与 gate 可视

对齐 GUI 的 LimitSettings，并在 gate 实时状态上**反超** GUI（GUI 目前只有旋钮文案）。

**工作项**

1. **`/limits` 屏**：registry 驱动的编辑屏（`zenagent/limits.py::ALL_KNOBS`，含 swarm 十旋钮
   与 goal 预算，0=关闭上限的语义与 GUI 一致），进程内走 `app/limits_apply.py`。
2. **gate 实时状态**：TUI 从 SwarmManager 直接读 GateDecision/ChainFacts
   （storm 冻结、chain 余量、fanout 余量），显示在房间屏状态带；预算拒绝的 system note
   已在消息流里（`app/swarm/manager.py` 房间通知）。
3. **反哺服务端**：gate 结构化状态先在 TUI 验证呈现，再落 `swarm.gate` wire op
   （见服务端伴随项）——TUI 当试验田，GUI 随后接同一个 op。

**验收**：TUI 能查看/修改全部 limits 旋钮并即时生效；房间里风暴冻结「可见可解释」
（谁被冻结、为什么、何时解冻）。

**测试**：`tests/test_tui_limits.py`；gate 读取的语义复用 `tests/test_swarm_gate.py` 的断言。

## Phase 3 · zen fleet

对齐 GUI 的 ZenSidebar / ZenRoster / WorkerConversation。

**工作项**

1. `/zen` 命令组：enable/disable/state。
2. 侧栏 zen section：router + workers 状态（挂进 `tui/widgets/sidebar.py` 现有 sections）。
3. worker transcript：换装视图复用 Phase 1 机制。

**前置**：orchestration-parity 判定「要修」的 #5（zen worker 不传 model_id/max_cost，
落错模型且无上限）建议先修，否则 TUI 复刻的是缺陷。

**验收**：TUI 内起 fleet → dispatch → 看 worker 会话 → 停 fleet。

**测试**：`tests/test_tui_zen.py`。

## Phase 4 · 管理面板补齐（与 Phase 1–3 并行）

对齐 GUI Settings 族里 TUI 缺的管理面。

**工作项**

1. **`/secrets` 屏**：list/set/clear/test（`app/probes.py` 进程内直调），合并现有 API key
   录入屏（`tui/screens/secrets.py`）。
2. **`/plugins` 屏**：list/install/uninstall/enable/disable（app 层 plugins 已就绪，TUI 只缺 UI；
   对应 GUI `ui/pages/PluginSettings.tsx`）。
3. **`/worktrees`**：worktree 总览只读屏（`app/workspace.py::worktree_overview`）。
   切换 = 提示 `--cwd` 重启——TUI 单进程绑 cwd，与 GUI「切换即重启 runtime」语义对齐但方式不同，UI 明示。
4. **`/runs`**：runs 账本列表 + 详情屏（`app/runs.py`、`app/subagent_runs.py`）。
   注意这是四端第一个 runs 视图，GUI 随后接同一个 op/数据。

**验收**：GUI Settings 族功能在 TUI 全部有对应命令与屏。

**测试**：`tests/test_tui_panels.py`（各屏的命令处理与数据装配）。

## Phase 5 · 顺手项（不阻塞主线）

- REPL：follow_up（`ChatController.follow_up` 已有，只差键位/命令入口）、队列管理。
- TUI 观察项（暂不做）：minimap（TUI 有滚动 + 侧栏，收益低）、fs 文件浏览（TUI 有 shell
  与 `@` 补全，需求弱——等用户反馈）。

---

## 服务端伴随项（非 TUI 范围，与本计划交叉引用）

1. **`swarm.gate` 结构化 op**——Phase 2 产出后在 `cli/rpc/ops_swarm.py` 落 wire，GUI 接。
2. **runs 视图 GUI 未接**——`runs list/get` op 就绪，GUI 侧小活；Phase 4 的 TUI `/runs`
   可先行验证信息架构。
3. **`app/metrics.py` 零暴露**、**`slash_args` argument_values op**——独立小项，不排期进本计划。

## 依赖与顺序

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3
                │
                └──► Phase 4（并行）
orchestration-parity #1（构造入口）并入 Phase 0
orchestration-parity #5（zen worker 模型/预算）先于 Phase 3
```

## 风险

1. **与 orchestration-parity 交错**：其待实施项与本计划 Phase 0/3 同区域。处置：#1 并入
   Phase 0 一起做，#5 作为 Phase 3 前置，其余（2/6–10 判定为保留设计）不碰。
2. **TUI 单进程单项目**：GUI 可多项目驻留，TUI 一个进程一个 cwd。swarm 房间绑定 cwd 项目，
   切项目 = 重启进程。UI 需明示（与 worktree 切换同理），不假装支持驻留。
3. **房间流数据量**：Textual 长列表性能敏感，Phase 1 的挂载窗口是硬要求不是优化项。
4. **审批并发**：房间多 role 并发审批 vs TUI 单模态。Phase 0 的队列化设计先行，
   复用 GUI pendingProject 的归属思路。
5. **矩阵维护**：每阶段合入时同步更新 [frontend-matrix.md](../../frontend-matrix.md) 的格与盘点日期。
