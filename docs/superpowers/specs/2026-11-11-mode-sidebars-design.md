# 模式感知侧边栏设计（ZEN / SWARM sidebar）

日期：2026-11-11
状态：已批准（用户确认与 fix 计划无冲突后授权并行实施）
分支：`feat/mode-sidebars`（worktree `.worktrees/feat-mode-sidebars`，从 main `16edfbd` 出发）
前置：zen×swarm 合并已落 main（见 `2026-11-11-zen-swarm-merge-design.md`），三态切换器
与 fleet 后台语义均已拍板，本设计不推翻。本分支已并入 `fix/zen-swarm-review` 的已提交
tip（merge `6e2f1ce`）：zen e2e 依赖其 zenMock，paused/resume 语义以其 ZenRoster 为准；
fix 余下任务均为后端文件，与本设计零交集，继续并行。

## 意图与动因

合并后的侧边栏仍是 coding 的信息架构：ZEN 模式只把 sessions 区换成名册，置顶、项目
分组、搜索/导入导出全部保留；SWARM 模式的侧边栏与房间完全无关，角色 Roster 和任务
TaskStrip 挤在 RoomPage 自带的右侧 aside 里——swarm 实际有两根侧柱，其中一根没用。

本次让侧边栏内容随模式切换（用户已选 A 方案）：每个模式一根侧柱、同一数据不重复
展示、复用现成组件而非简化重写。

**用户拍板的关键语义**：swarm 的目标语义是**按项目的房间**；当前后端写成全局是待修
项，用户将自行修改后端。UI 按"有项目"前瞻设计：zen 与 swarm 侧边栏都带紧凑的项目
切换下拉，swarm 在项目切换时重载房间数据，为后端落地留好接缝。

## 目标

- coding 侧边栏原样保留（含 fleet 后台启用时名册换肤的既有行为——由 `zenStore.enabled`
  驱动，合并时已拍板）。
- ZEN 侧边栏 = 项目下拉 + fleet 名册；停用态复用 fix 分支的 paused/resume。
- SWARM 侧边栏 = 项目下拉 + Roster + TaskStrip（从 RoomPage aside 原样搬移，全部
  操作随迁）；主区变纯 feed + composer + 任务详情。
- 两个模式的项目切换语义与今天点侧边栏项目完全一致（复用 `openProject`）。
- 跨模式活动指示：ModeSwitcher zen 段 fleet 活动点、swarm 段运行中任务数。
- 全门禁绿：`scripts/check.sh`（ruff + 架构预算 + pytest）、desktop `pnpm test &&
  pnpm build`、style tokens lint、e2e。

## 非目标（anti-goals）

- **不改后端**：swarm 按项目化是用户自己的后续任务；zen slot/fleet 语义不动。
- **不做客户端假分区**：不在前端把全局房间数据按项目切分展示——那是错误语义，
  后端落地后还得拆。过渡期所有项目看到同一份全局房间数据是已知且可接受的。
- 不做 zen/swarm roster 的公共抽象（合并设计明确的下一阶段工作）。
- 不在 boot 时预加载 swarm（host 对 swarm manager 是懒创建，不进入 swarm 的用户
  不应付常驻角色 runtime 的成本）。
- 不新增 RPC kind；不动顶栏静态面包屑；不改三态切换器的位置与切换语义。
- 不改 SwarmSettings 的"房间为全局"说明文案（后端当前仍是全局；后端落地时随改，
  见"后续项"）。

## 约束（实测）

- **行数预算**：`Sidebar.tsx` 853/854，仅 1 行余量 → 必须拆分；`appStore.ts`
  1175/1175 **零余量，一行不能加**；未列名文件 DEFAULT_CAP = 800
  （`scripts/check_architecture.py`）；`bridge.ts` 919/998、`e2e/harness.ts`
  27/886 均有余量，新 e2e mock 可直接加。
- **模块边界**：`swarmStore`/`swarmWiring` 永不 import `appStore`（node --test
  加载约束）→ 项目变更重载不能挂 wiring，放 UI 组件 effect（组件可读 appStore）。
- **挂载行为**：AppFrame 折叠时侧边栏宽度归 0 但组件保持挂载 → SwarmSidebar 的
  effect 在折叠态依然生效；AgentModal 遮罩 `position: fixed; inset: 0; z-index: 60`
  → 可挂在侧边栏组件树内。
- **fleet 生命周期**：有活跃 worker 的 fleet pin 住 slot（`slot.py:92`），切项目
  不停 fleet；4 slot 全 pin 时开新项目被拒 `project_busy`，走现有拒绝横幅。
- **e2e 选择器**：`data-swarm-*`、`data-zen-*`、`data-sidebar-*` 等 data 属性
  原样保留，DOM 位置变化在同提交内更新断言。
- style tokens lint：样式类原样搬移，不写新裸值。

## 方案选型（已定）

**侧边栏形态** —— A 模式感知侧边栏（用户选）。备选 B"全局会话导航 + 弱化无关
内容"弃：双柱与无关会话列表的问题仍在。

**项目入口** —— zen/swarm 都用紧凑项目下拉（用户定：swarm 也有项目概念，UI 前瞻
设计）。备选"swarm 不放项目入口"弃：它基于错误的全局语义。备选"等后端改完再加"
弃：用户明确 UI 先行。

**swarm 过渡接缝** —— load-on-switch + `loadedProject` 跟踪（实现者定）。
Builder check 实测：`reload`（op=reload）只刷 roles/warnings，且有副作用（删除
manifest 已消失的角色并取消其未完成任务），不适合自动触发；只有 `load`
（op=list）取房间全量快照——接缝必须用 load。当前全局后端下重 list 返回同一份
数据，无害；后端按项目化后天然正确，无需再改 UI。备选"客户端按项目缓存/分区"
弃：假语义，返工。

## 设计

### 1. Sidebar 壳拆分

`Sidebar.tsx` 变薄壳（目标 ~120 行）：品牌行 + 折叠钮 + `<ModeSwitcher />` +
按 `workspaceMode` 分发 + `<Footer />`（三模式共用）+ `data-shell-sidebar`。

coding 主体（置顶区、sessions 区、项目分组、行菜单、SessionTools、SessionRow）
**原样**迁入 `ui/SidebarCoding.tsx`（估 ~700 行 < 800）。名册换肤条件维持 fix 分支
契约（`zenEnabled || workspaceMode === 'zen'`），留在 SidebarCoding 内不动。

分发：`coding → SidebarCoding`；`zen → ZenSidebar`；`swarm → SwarmSidebar`。

### 2. ProjectSwitcher（新共享组件 `ui/ProjectSwitcher.tsx`）

- 一行：FolderIcon + 当前项目名（`appStore.project` → 项目名）+ chevron。
- 下拉菜单 portal 到 body（仿现有行菜单：键盘上下导航、outside-click/Escape
  关闭、焦点恢复）：注册项目列表（`useProjectStore`，名称 + 路径，当前项打勾）
  + 分隔线 + "打开文件夹…"（`pickProjectFolder → addProject → openProject`）。
- 点项目 = `openProject(path)`，与今天侧边栏点项目语义一致；拒绝走现有横幅。
- 二期可选（不在本次）：消费 project op 返回里的 `resident` 列表，给下拉中
  "有 fleet 在跑"的项目标点。

### 3. ZenSidebar（`ui/ZenSidebar.tsx`）

- ProjectSwitcher + 现有 `ZenRoster`（原样复用：router 行 + 停止钮、worker 行 →
  WorkerDrawer，抽屉全局挂载不动）。zen 模式下无条件渲染 ZenRoster——停用态由
  fix 分支的 paused 横幅 + resume 按钮承载（`data-zen-paused`/`data-zen-resume`），
  不再另做启用空态，避免重复入口。
- 不含会话历史/搜索/导入导出——Coding 段是会话表面，一次点击可达。

### 4. SwarmSidebar（`ui/SwarmSidebar.tsx`）与面板搬移

- Roster、TaskStrip 从 `RoomPage.tsx` **原样**抽出为 `ui/SwarmRoster.tsx`、
  `ui/SwarmTasks.tsx`：data 属性、操作按钮（新建/配置/重载/重置、取消/重试）、
  空态文案全保留；对应样式类从 `RoomPage.module.css` 迁入
  `SwarmPanels.module.css`。任务序号 map 在两处各自一行 `useMemo` 派生，不做
   premature 抽象。
- SwarmSidebar = ProjectSwitcher + SwarmRoster + SwarmTasks + AgentModal
  （editing 状态与 save 流程从 RoomPage 随迁；遮罩 fixed，挂侧边栏树内即可）。
- `selectedTask` 上提到 `swarmStore`（`selectedTask: string | null` +
  `selectTask(id)` toggle）：TaskStrip 写、RoomPage 的 TaskDetailPanel 读。
- RoomPage：删 aside 与两个面板函数，`.root` 变单列；保留 feed、composer、
  warnings、ApprovalPanel、TaskDetailPanel。
- **项目变更接缝**（builder check 后定稿的契约）：
  - `swarmStore.load(project: string)`：调用方传入**请求发起时**的项目；成功时
    `loadedProject` 记为该请求项目，而非应答时的当前项目。内部请求令牌保证只有
    最新请求的应答生效（A→B 切换时 load(A) 在途：A 的应答被丢弃，防止旧数据被
    标成 B 后永不补载）。
  - SwarmSidebar effect 读 `appStore.project`：`loaded && loadedProject !== project`
    → 先清 `selectedTask` 与 `runRows`（房间级 UI 状态随旧房间留在原地），再
    `load(project)`。组件常挂载（折叠也在），无挂载时序死角。
  - 其余调用点同步传当前项目：RoomPage 挂载加载、saveRole 成功后的刷新（后者
    不清选择，保住详情面板）。
  - 当前全局后端下重 list 返回同一份数据，无害；后端按项目化后天然正确。

### 5. ModeSwitcher 活动指示

- zen 段：`zenStore.enabled` 时显示活动小点（fleet 存在即亮，含后台运行）。
- swarm 段：`loaded` 且 running+queued 任务数 > 0 时显示计数徽标；未加载不预载。
- 只读 store，不改切换语义；tab 的 aria-label 附带状态文案。样式用现有 ds tokens。

### 6. i18n

新键 zh/en 成对：`sidebar.projectSwitcher`、`sidebar.openFolder`、
ModeSwitcher 徽标 aria 文案 → `messages.ts`（余量充足）；zen 停用/恢复复用 fix
分支已加的 `zen.paused*` 键；swarm 面板复用现有 `swarm.*` 键
（messages-swarm.ts），不新增。

### 7. 测试

单元（新增/更新）：
- Sidebar 壳分发：三模式渲染对应主体，Footer 常在。
- ProjectSwitcher：开合、键盘导航、选择调 openProject、打开文件夹路径。
- ZenSidebar：zen 模式渲染 ProjectSwitcher + ZenRoster；停用态 paused 横幅
  可见（fix 契约，不重复造启用入口）。
- swarmStore：selectTask toggle、loadedProject 按请求发起项目记录、过期应答
  丢弃（latest-request guard）。
- SwarmSidebar：项目变更清空 selection/runRows 并发出 list 请求（sender seam
  断言）。
- ModeSwitcher：zen 点 / swarm 计数的显示条件。
- 既有 `zenSidebar.test.ts`（纯函数）、workspaceMode、zenActions、swarmRun、
  swarmWiring 测试不改，照跑验证不破坏。

e2e：
- `swarm.spec.ts` 更新：roster/task 位于侧边栏（选择器不变；涉及可见性的断言
  补侧边栏展开前提）；任务详情断言（`[data-swarm-main] [data-swarm-task-detail]`、
  列表内无详情）天然继续成立。
- 新增：zen 侧边栏项目下拉切换（复用 bridge 现有 project switch mock）、
  swarm 切项目发出 list 请求并清旧选择、ModeSwitcher 徽标。zen 停用/恢复路径
  由 fix 分支 `zen.spec.ts` 覆盖，不重复。
- `app.spec.ts` coding 侧边栏全量回归，验证壳拆分无行为变化。

门禁：`scripts/check.sh`、desktop `pnpm test && pnpm build`、style tokens、
`pnpm e2e`（或仓库现行 e2e 命令）。

### 8. 后续项与并行协调（不在本设计内）

- 用户：swarm 后端按项目化（`2026-11-11-project-scoped-swarm-design.md` 草案，
  fix 落地后另开分支）。其 §7.4 推荐的"单活房间 + 切项目 resync"正是本设计的
  load-on-switch 接缝；其桌面任务 4 扩展 swarmStore/swarmWiring 路由时，本设计
  的 `load(project)`/`loadedProject` 契约可直接承接。
- "从角色库复制"入口（该草案 §3.4）将随 Roster 落在 SwarmSidebar
  （`data-swarm-new-role` 旁），实现时按新位置挂载。
- 后端落地时：swarmWiring 按帧 project 标签路由；SwarmSettings"房间为全局"
  文案随改；可选消费 `resident` 给项目下拉标 fleet 点。
- fix 分支剩余任务（T12–T14，后端）与本分支并行；合流时再并一次 fix tip。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 壳拆分破坏既有选择器/行为 | data-* 原样保留；app.spec.ts 全量回归 |
| appStore 零余量 | 一行不加；新逻辑全在新组件与 swarmStore |
| 全局后端下 load 接缝重 list | 返回同一份数据，无害；loadedProject 防重复加载；latest-request guard 防旧应答标错项目 |
| 折叠时 roster/task 不可达 | 与 coding 会话语义一致；ExpandSidebar 既有恢复入口 |
| e2e 对 DOM 位置敏感 | 选择器稳定，断言随搬移同提交更新 |
| 样式搬移触发 token lint | 类原样迁移，不写新裸值 |

## 交付物

`feat/mode-sidebars` 分支（壳拆分 → 共享项目下拉 → zen/swarm 侧边栏 → 活动指示 →
测试与文档），全门禁绿；本设计 + 实施计划
（`docs/superpowers/plans/2026-11-11-mode-sidebars.md`）一并入库。