# Zen × Swarm 合并实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `feat/zen-mode` 与 `feat/swarm-runtime` 合并到集成分支 `merge/zen-swarm`，两功能共存互不破坏，模式状态统一为三态 `workspaceMode`；fleet 离开行为可配置（默认后台继续，Settings→Zen 可切"离开即停"）+ 显式停用入口；Settings 页新增 Zen/Swarm 两个区域。

**Architecture:** 先合 zen（近似快进），再合 swarm（4 个文本冲突按"并集原则"解 + 行数预算抽离），随后模式统一接线（删 zenMode/ZenToggle，ModeSwitcher 驱动 fleet enable，离开行为由 `zenBackgroundRun` 设置门控），zen surface 落位（App 三态里 zen=coding 同款 center，删占位页），Settings 新增两区域。后端零行为改动。

**Tech Stack:** Python 3 (asyncio RPC host/slot) + React 18 / Zustand 5 / Tauri 2 + node:test + Playwright。

**Spec:** `docs/superpowers/specs/2026-11-11-zen-swarm-merge-design.md`（本计划从其论证，执行者须同时读两者）

## Global Constraints

- 工作区：worktree `/Users/mac/zen-agent/.worktrees/merge-zen-swarm`，分支 `merge/zen-swarm`；**merge commit 不 squash 不 rebase** 已评审历史。
- swarm 合并输入 = Task 1 快照的 SHA（写进 merge commit message），中途分支再动**不追**。
- Python 门禁：`scripts/check.sh`（uv 若不在 PATH 用 `/Users/mac/.local/bin/uv`；含 ruff + 架构预算 + pytest 全量）。
- Desktop 门禁：`pnpm test && pnpm lint:tokens && pnpm build`（在 desktop/ 下）；e2e `pnpm e2e`（playwright，Task 7 全量跑）。
- 架构预算（`docs/architecture/allowlist.json`，**只许降**）：`appStore.ts ≤1175`（合并后需抽离）、`runtime.py ≤1061`（合并恰等，**零新增行纪律**）、`Sidebar.tsx ≤854`、`types.ts ≤813`。
- `zenagent/app/` 禁止 import `zenagent.cli`（tests/test_layering.py 钉死）；`app/zen` 与 `app/swarm` 零交叉 import（为下阶段抽象留边界）。
- RPC：PROTOCOL_VERSION=3；REQUEST_KINDS 必须同时含 `'zen_fleet'` 与 `'swarm'`（tests/test_rpc_contract.py 双向刮源码）。
- 后端（fleet/manager/房间/审批/生命周期）**零行为改动**；本计划不改任何 `zenagent/app/zen/`、`zenagent/app/swarm/` 文件。
- i18n zh/en 键齐平；CSS 只用 `--dsw-alias-*` 词表真实 token。

## Review Focus

1. **老用户升级迁移**：localStorage 只有旧 key `zen.ui.mode='zen'` → 迁移后 `workspaceMode='zen'` 且 boot 自动 enable（Task 3 迁移测试钉死）。
2. **busy slot 切 zen 失败**：enable 被拒 → UI 回滚 coding、无假 zen 态（Task 3 revert 测试）。
3. **fleet 离开行为按设置门控**：`zenBackgroundRun=true`（默认）切走零请求、`zenStore.enabled` 恒 true；`=false` 切走自动 disable 并跟随恢复 session；运行中改开关不立即启停（Task 3 `onZenModeLeft` 两向测试钉死）。
4. **swarm 帧分流顺序**：`applySwarmFrame` 必须在 project/session 过滤之前消费无标签 swarm 帧，否则 RoomPage 死寂（Task 2 单测 + Task 7 e2e 案①）。
5. **运行中显式停用**：stopFleet 成功后 roster 清空、session 跟随恢复到 pre-zen（Task 3 stopFleet 测试）。

---

### Task 1: 快照 swarm SHA + 合并 feat/zen-mode

**Files:**
- 无代码改动（git merge 产生 merge commit）。

**Interfaces:**
- Produces: 集成分支 HEAD = main + 设计/计划文档 + 全部 zen 提交；`SWARM_SHA`（Task 2 的合并输入）。

- [ ] **Step 1: 记录 swarm 输入快照**

```bash
cd /Users/mac/zen-agent/.worktrees/merge-zen-swarm
git rev-parse feat/swarm-runtime   # 记下此 SHA，Task 2 使用且不再变更
```

- [ ] **Step 2: 合并 zen**

```bash
git merge feat/zen-mode --no-edit
```
Expected: 无冲突（zen 只与 main 分叉；集成分支仅多两个 docs 提交）。

- [ ] **Step 3: 验证树等价**

```bash
git diff feat/zen-mode HEAD --stat
```
Expected: 仅 `docs/superpowers/plans/2026-11-11-zen-swarm-merge.md` 与 `docs/superpowers/specs/2026-11-11-zen-swarm-merge-design.md`（代码零差异，无需重跑门禁）。

### Task 2: 合并 swarm —— 并集解冲突 + 预算抽离（TDD 新缝）

**Files:**
- Modify（解冲突）: `desktop/src/rpc/types.ts`、`desktop/src/state/stores.ts`、`desktop/src/state/appStore.ts`、`CHANGELOG.md`
- Create: `desktop/src/state/swarmWiring.ts`
- Modify: `desktop/src/state/zenActions.ts`（加 `applyFleetFrame`）
- Test: `desktop/tests/swarmWiring.test.ts`、`desktop/tests/zenActions.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `SWARM_SHA`。
- Produces: `applySwarmFrame(frame: Record<string, unknown>): boolean`（swarmWiring.ts，notice→Notification、`swarm:true`→swarmStore，消费返回 true）；`applyFleetFrame(frame: Record<string, unknown>): boolean`（zenActions.ts，`type==='zen_fleet'`→zenStore.applyRoster，消费返回 true）。Task 3/4 依赖二者与 union 后的 `ServerFrame`。

- [ ] **Step 1: 先跑 trial merge 重核冲突清单**（swarm tip 可能新增冲突文件）

```bash
git merge-tree --write-tree --name-only HEAD <SWARM_SHA>
```
与设计 §1 的 4 文件清单比对；新出现的冲突按同一"并集原则"处理（见 Step 3）。

- [ ] **Step 2: 写失败测试（新缝）**

`desktop/tests/swarmWiring.test.ts`：
- `applySwarmFrame` 对 `swarm_message` 帧 → swarmStore 消息入列且返回 true；
- 对 `swarm_notice` 帧 → notificationStore 收到 push 且返回 true；
- 对普通 `event` 帧 → 返回 false 且两 store 不变。

`desktop/tests/zenActions.test.ts` 追加：`applyFleetFrame` 对 `zen_fleet` 帧 → `useZenStore.enabled/roster` 被应用且返回 true；对 `event` 帧 → false。

Run: `cd desktop && pnpm test`
Expected: FAIL（两函数未定义）。

- [ ] **Step 3: 执行合并并解冲突**

```bash
git merge <SWARM_SHA>   # message 注明 SHA：Merge feat/swarm-runtime (<SHA>)
```

解冲突原则（**纯并集，不删任何符号** —— zenMode/ZenToggle/ZenPlaceholder 的删除属于 Task 3/4）：
- `types.ts`：PROTOCOL_VERSION=3；REQUEST_KINDS 同时保留 `'swarm'`（字母序位置）与 `'zen_fleet'`；`ServerFrame = ResultFrame | EventFrame | ZenFleetFrame | SwarmMessageFrame | SwarmTaskFrame | SwarmAgentFrame | SwarmNoticeFrame`；zen 的内联类型与 swarm 的 `types-swarm.ts` re-export 都保留。
- `stores.ts`：SettingsState 同时保留 zen 的 `zenMode/setZenMode`（`zen.ui.mode`）与 swarm 的 `workspaceMode/setWorkspaceMode`（`zen.workspaceMode`）。
- `appStore.ts`：import 并集；`_useSwarmSender({...})` 注册保留在 store 创建后；handleFrame 头部 swarm 分流（在 project/session 解析**之前**）+ 尾部 zen_fleet 分支（在 `frameBelongsTo` 之后）都保留；bootChild 尾部 `syncZenState` 保留。
- `CHANGELOG.md`：单一 `## Unreleased`，下设 Swarm runtime 与 Zen 模式两小节（内容全保留），末尾 Merge note 两行："集成两分支，模式状态统一为 workspaceMode 三态；zen 离开行为改为默认后台继续，Settings→Zen 可切回'离开即停'（老用户可见的行为变化）"。

- [ ] **Step 4: 实现两个 apply 函数（预算抽离）**

`swarmWiring.ts`：把 appStore.handleFrame 头部的 swarm notice/`swarm:true` 分支体移入 `applySwarmFrame`（import `useNotificationStore`/`useSwarmStore`/`swarmNoticeText`；**不 import appStore**，避免环）。`zenActions.ts`：同法加 `applyFleetFrame`。appStore 两处分支体各替换为一行 `if (applySwarmFrame(frame)) return` / `if (applyFleetFrame(frame)) return`。

- [ ] **Step 5: 绿灯 + 门禁**

```bash
cd desktop && pnpm test && python3 ../scripts/check_architecture.py
```
Expected: 全部通过；`appStore.ts` ≤1175。再核对 `wc -l ../zenagent/app/runtime.py` = **1061**（恰等上限，冲突标记清理不得净增行）。

```bash
cd .. && scripts/check.sh && cd desktop && pnpm lint:tokens && pnpm build
```
Expected: 全绿（Python 全量 + 两分支 desktop 测试共存）。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "merge: integrate feat/swarm-runtime — union wire types, swarm frame wiring under budget"
```

### Task 3: 模式统一 —— workspaceMode 收编（TDD）

**Files:**
- Modify: `desktop/src/state/stores.ts`（删 zenMode，迁移读取）、`desktop/src/state/zenActions.ts`、`desktop/src/state/appStore.ts`、`desktop/src/ui/ModeSwitcher.tsx`、`desktop/src/ui/Sidebar.tsx`、`desktop/src/i18n/messages.ts`
- Delete: `desktop/src/ui/ZenToggle.tsx`
- Test: `desktop/tests/zenActions.test.ts`、`desktop/tests/workspaceMode.test.ts`

**Interfaces:**
- Consumes: Task 2 的 union 状态与 `applyFleetFrame`。
- Produces: `switchZenPresence(get, entering: boolean): Promise<void>`（entering=true 走 enable 流程含失败回滚 `setWorkspaceMode('coding')`；entering=false 立即返回、**不发任何 RPC**）；`stopFleet(get): Promise<void>`（disable 成功→`applyRoster(false,[])`+closeWorker+跟随返回 session+刷新）；`onZenModeLeft(get): Promise<void>`（`zenBackgroundRun===false` → `stopFleet`，否则零请求）；appStore 新 action `switchWorkspaceMode(mode: WorkspaceMode): void`（`prev=settings.workspaceMode`；持久化 + `mode==='zen'` 时 `void switchZenPresence(get,true)` + `prev==='zen' && mode!=='zen'` 时 `void onZenModeLeft(get)`）与 `stopFleet()` 委托；`syncZenState` 门改读 `workspaceMode`；SettingsState 新增 `zenBackgroundRun: boolean`（persist `zen.fleet.background`，默认 true，非法值回落 true）+ `setZenBackgroundRun`。Task 4 的 ZenRoster 停止按钮与 Task 5 的 ZenSettings 开关消费这些。

- [ ] **Step 1: 写失败测试**

`workspaceMode.test.ts` 追加迁移用例：无 `zen.workspaceMode` + 旧 `zen.ui.mode='zen'` → 初值 `'zen'` 且新 key 已写回；两 key 皆无 → `'coding'`；新 key 为垃圾值 → `'coding'`；`zenBackgroundRun` 默认 true 且 persist 往返（key `zen.fleet.background`）。

`zenActions.test.ts` 重写模式相关用例：
- `switchZenPresence(true)` 发 enable、成功跟随 session + 刷新（不再断言 settings 写入）；
- enable 被拒（`zen_busy`）→ `useSettingsStore.workspaceMode` 回滚 `'coding'`；
- `switchZenPresence(false)` → `sent` 数组为空（后台保活）；
- `onZenModeLeft`：`zenBackgroundRun=false` → 发 disable 且 enabled=false、跟随恢复 session；`true` → 零请求；
- `stopFleet` 发 disable、成功后 enabled=false、currentPath 跟随返回 session；
- `syncZenState` 改断言 `workspaceMode==='zen'` 触发 auto-enable；
- 保留既有 never-auto-disable / already-on / pre-zen-child / zenTranscript / applyFleetFrame 用例。

Run: `cd desktop && pnpm test` — Expected: FAIL（新签名/迁移未实现）。

- [ ] **Step 2: stores.ts 删旧态 + 迁移 + 新增离开行为开关**

删 `zenMode/setZenMode/zen.ui.mode` 读写与 `ZenUiMode` 引用；SettingsState 加 `zenBackgroundRun/setZenBackgroundRun`（persist `zen.fleet.background`，`load` 回落 true）。`loadWorkspaceMode()` 扩为（沿用文件内既有 `load/persist` 助手签名）：

```ts
function loadWorkspaceMode(): WorkspaceMode {
  const stored = load<string | null>('zen.workspaceMode', null)
  if (stored === null && load<string | null>('zen.ui.mode', null) === 'zen') {
    persist('zen.workspaceMode', 'zen')   // 一次性迁移；旧 key 留置不再读
    return 'zen'
  }
  return WORKSPACE_MODES.includes(stored as WorkspaceMode) ? (stored as WorkspaceMode) : 'coding'
}
```

- [ ] **Step 3: zenActions / appStore / ModeSwitcher / Sidebar 接线**

`zenActions.ts`：`setZenMode` 拆为 `switchZenPresence/stopFleet`，另加 `onZenModeLeft`（读 `useSettingsStore.getState().zenBackgroundRun` 门控 `stopFleet`；语义见 Interfaces；enable 体沿用原 `mode==='zen'` 分支，disable 体沿用原 `'coding'` 分支去掉 settings 写入）；`syncZenState` 的门换 `workspaceMode`。
`appStore.ts`：AppState 的 `setZenMode` 替换为 `switchWorkspaceMode` + `stopFleet` 委托；`switchWorkspaceMode` 实现见 Interfaces（注意先记 `prev` 再持久化）。
`ModeSwitcher.tsx`：selector 从 settings 的 `setWorkspaceMode` 换 appStore 的 `switchWorkspaceMode`。
`Sidebar.tsx`：删 `ZenToggle` import 与 `<ZenToggle />` 两行；删除 `src/ui/ZenToggle.tsx`。
`messages.ts`：grep 确认仅被 ZenToggle 引用的 `zen.*` 键删除（zh/en 同步）；新增 `zen.stopFleet`（zh"停用 Zen fleet"/en"Stop Zen fleet"）与 `zen.stopFleetTitle`（zh"停止后台 worker 并恢复原会话"/en"Stop background workers and restore the session"）。

- [ ] **Step 4: 绿灯 + 提交**

```bash
cd desktop && pnpm test && pnpm lint:tokens && pnpm build && python3 ../scripts/check_architecture.py
```
Expected: 全绿（Sidebar 应降至 ~852）。`git add -A && git commit -m "feat(desktop): unify workspace mode — three-state switcher adopts zen, fleet keeps running in background with explicit stop"`

### Task 4: zen surface 落位 —— 删占位页 + 停止按钮

**Files:**
- Modify: `desktop/src/App.tsx`、`desktop/src/ui/ZenRoster.tsx`、`desktop/src/ui/ZenRoster.module.css`（或 ZenRoster 所在 css）、`desktop/src/i18n/messages.ts`
- Delete: `desktop/src/ui/pages/ZenPlaceholder.tsx`、`desktop/src/ui/pages/ZenPlaceholder.module.css`
- Test: 既有 suites + e2e 回归（无新单测，UI 布线沿用 zen 分支无组件测试的先例）

**Interfaces:**
- Consumes: Task 3 的 `stopFleet`、`zen.stopFleet/zen.stopFleetTitle` 键。

- [ ] **Step 1: App.tsx 三态收敛**

把 coding 分支的 center（Topbar+ChatView+InputBar+workPanelToggle）提为局部 const `codingCenter`；ternary 改为 `workspaceMode === 'swarm' ? <RoomPage /> : codingCenter`（zen 与 coding 共用——zen 的 surface 就是 router 会话本身）。确认 `<WorkerDrawer />` 仍挂在与 CommandPalette 同级的全局位置（不受模式 ternary 影响）。删 ZenPlaceholder import。

- [ ] **Step 2: ZenRoster router 行停止按钮**

router 行加小按钮：`aria-label={t('zen.stopFleet')}`、`title={t('zen.stopFleetTitle')}`、点击 `void stopFleet()`（selector 自 appStore）。样式复用 `.zenRouterRow` 内既有 token（如 `--dsw-alias-interactive-bg-hover` hover），不引新 token。

- [ ] **Step 3: 清理与验证**

删 `ZenPlaceholder.tsx/.module.css` 与 `zen.placeholder` 键（zh/en）。`cd desktop && pnpm test && pnpm lint:tokens && pnpm build`；`pnpm e2e -- e2e/swarm.spec.ts e2e/app.spec.ts` 确认 ModeSwitcher/coding/swarm 路径未回归。
`git add -A && git commit -m "feat(desktop): real zen surface replaces the placeholder; roster gains explicit fleet stop"`

### Task 5: Settings 页新增 Zen / Swarm 区域

**Files:**
- Create: `desktop/src/ui/pages/ZenSettings.tsx`、`desktop/src/ui/pages/SwarmSettings.tsx`（样式沿用 AgentSettings 的 module.css 模式，小体量各自成文件或共用一个，执行者按现存 settings 行样式取舍，只用真实 token）
- Modify: `desktop/src/ui/pages/settingsNav.tsx`（Tab 联合 + NAV_ITEMS，排在 agents 项附近）、`desktop/src/ui/pages/SettingsPage.tsx`（lazy 两面板）、`desktop/src/i18n/messages.ts`
- Test: `desktop/tests/settingsNav.test.ts`（若现存该模式则扩展；否则在 `workspaceMode.test.ts` 补 `zenBackgroundRun` 的 persist 用例已覆盖 Task 3，本任务不强制新单测——面板为纯布线，行为测试在 Task 3 的 store 层）

**Interfaces:**
- Consumes: Task 3 的 `SettingsState.zenBackgroundRun/setZenBackgroundRun`。
- Produces: Settings tab `'zen'` 与 `'swarm'`；`SwarmSettings` 的 prop `onNavigate: (tab: Tab) => void`（SettingsPage 注入，用于跳转 agents）。

- [ ] **Step 1: 导航与面板**

`settingsNav.tsx`：`Tab` 联合加 `'zen' | 'swarm'`；NAV_ITEMS 在 agents 附近加两项（labelKey `settings.zen` / `settings.swarm`，keywords 复用 zen/swarm 既有键）。`SettingsPage.tsx`：lazy 引入两面板，`tab === 'zen'` → `<ZenSettings />`，`tab === 'swarm'` → `<SwarmSettings onNavigate={setTab} />`。

- [ ] **Step 2: ZenSettings 开关**

复用现存 settings 行 + 开关控件样式（grep 其他面板的 toggle 用法照抄结构）。开关绑定 `useSettingsStore` 的 `zenBackgroundRun/setZenBackgroundRun`；标题 `zen.settings.backgroundRun`，说明 `zen.settings.backgroundRunHint`（zh 概述"开=离开 Zen 模式时 worker 后台继续；关=离开即停止并恢复原会话"），附注 `zen.settings.stopNote`（"名册中的停止按钮始终可用"）。

- [ ] **Step 3: SwarmSettings 卡片**

说明段 `swarm.settings.desc`（房间全局、跨项目常驻，数据在 `~/.zenagent/swarm/`）+ 按钮 `swarm.settings.manageAgents` → `onNavigate('agents')`。不新增后端设置项。

- [ ] **Step 4: i18n + 验证 + 提交**

`messages.ts` zh/en 齐平补键：`settings.zen/settings.zenHint/settings.swarm/settings.swarmHint`（nav）、`zen.settings.backgroundRun/backgroundRunHint/stopNote`、`swarm.settings.desc/manageAgents`。`cd desktop && pnpm test && pnpm lint:tokens && pnpm build`（SettingsPage/settingsNav 行数若近预算上限，抽样式或精简，不升 allowlist）。
`git add -A && git commit -m "feat(desktop): settings sections for zen and swarm — fleet leave-behavior toggle"`

### Task 6: 文档合流

**Files:**
- Modify: `docs/architecture.md`、`docs/architecture-full.html`、`docs/README.md`、`docs/guide/zen-mode.md`、`docs/guide/swarm.md`

- [ ] **Step 1: 补齐文档**

`architecture.md` 模块表加 `app/swarm/`（roles/room/router/runner/manager/protocol/roster 一行或两行）与 `cli/rpc/ops_swarm`，风格照 zen 行；`architecture-full.html` 加 swarm 包节点 + host 边（wire kind `swarm`），从简（zen 已建立画法）。`docs/README.md` 核对 zen 与 swarm 指南都在列。两份 guide 各加一行互指（"另一种多 agent 形态见 …"）。zen-mode.md 补：模式切换已并入三态切换器；离开行为默认后台继续、可在 Settings→Zen 切为"离开即停"；显式停止按钮位置。
`git add -A && git commit -m "docs: merged-tree architecture and guide cross-links for zen + swarm"`

### Task 7: 全门禁 + 独立终审

**Files:** 无新改动（除非终审修复）。

- [ ] **Step 1: 全量门禁**

```bash
scripts/check.sh && cd desktop && pnpm test && pnpm lint:tokens && pnpm build && pnpm e2e
```
Expected: Python 全绿（zen 2620 + swarm 全部新增）、desktop 全绿、e2e 全绿。

- [ ] **Step 2: 整分支 fresh 终审**

用 superpowers:requesting-code-review 派全新子代理审整个 `merge/zen-swarm`（main..HEAD），重点：设计 §风险表五项、Global Constraints 逐条、Review Focus 五项。Critical/Important 即修即绿，minors 记入 deferred 清单汇报。

- [ ] **Step 3: 汇报**

向用户交付：merge 结构（commit 图）、门禁数字、迁移/保活/停止语义说明、`git checkout main && git merge --ff-only merge/zen-swarm` 的推进命令（由用户决定何时执行）。
