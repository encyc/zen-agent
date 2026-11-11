# 模式感知侧边栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 侧边栏内容随 workspaceMode 切换：coding 原样、zen = 项目下拉 + fleet 名册、swarm = 项目下拉 + Roster + TaskStrip（从 RoomPage aside 搬移），主区变纯 feed。

**Architecture:** Sidebar.tsx 拆成薄壳 + SidebarCoding（原样迁出）；新共享 ProjectSwitcher 复用 `openProject`；swarmStore 增加 `load(project)`/`loadedProject`/`selectedTask` 接缝（latest-request guard），SwarmSidebar 的 effect 在项目变更时清房间级 UI 状态并重载——后端按项目化落地后无需再改 UI。appStore 一行不加。

**Tech Stack:** TypeScript + React 18 + zustand，CSS Modules（ds tokens），node --test（纯逻辑单测，无 DOM），Playwright e2e（dev bridge mock）。

**Spec:** `docs/superpowers/specs/2026-11-11-mode-sidebars-design.md`（已批准；含 fix 分支兼容契约）。

## Global Constraints

- 全部工作在 worktree `.worktrees/feat-mode-sidebars`（分支 `feat/mode-sidebars`，已含 main `16edfbd` + fix T1–T11 merge `6e2f1ce`）。**永不改 main 检出；永不 push。**
- 门禁：`bash scripts/check.sh`（ruff + 架构预算 + pytest）；desktop：`npm test`（`node --test tests/*.test.ts`）、`npm run build`（`tsc --noEmit && vite build`）、`npm run lint:tokens`、`npm run e2e`（playwright）。单测单文件：`node --test tests/<name>.test.ts`；单 e2e：`npx playwright test e2e/<name>.spec.ts`（均在 `desktop/` 下）。
- 行数预算（`scripts/check_architecture.py`，allowlist `docs/architecture/allowlist.json`）：新文件 ≤800；`Sidebar.tsx` 854（现 853）；**`appStore.ts` 1175/1175 零余量，一行不能加**；`messages.ts` 1123（现 678）；`bridge.ts` 998（现 921，本计划不改 bridge）；`harness.ts` 886（现 27）。
- `swarmStore`/`swarmWiring` **永不 import appStore**（node --test 加载约束）；项目变更 effect 放 UI 组件。
- `data-swarm-*`/`data-zen-*`/`data-sidebar-*`/`data-workspace-*` 选择器原样保留；DOM 位置变化在同提交内更新断言。
- i18n zh/en 键成对（`locale.test.ts` 自动校验）；新键进 `desktop/src/i18n/messages.ts`。
- CSS 只搬移/复用现有类与 ds tokens，不写裸色值（`check-style-tokens.mjs`）。
- 不改后端、不新增 RPC kind、`PROTOCOL_VERSION=3` 不动；`swarm list` 请求**附加**可选 `project` 字段（现 host 忽略，用户按项目化草案 §3.2 的 `request.project` 契约预留）。
- fix 分支契约不动：ZenRoster 的 paused/resume（`data-zen-paused`/`data-zen-resume`）、SidebarCoding 名册换肤条件 `zenEnabled || workspaceMode === 'zen'`、`zen.spec.ts` 现有三测试。

## Review Focus

1. **A→B 切项目时 load(A) 仍在途** —— A 的迟到应答不得标成 B 的房间（否则 B 永不补载）。→ T1 `a stale load answer is dropped`。
2. **开着任务详情切项目** —— 旧房间的 selection 与 live runRows 必须清掉，feed 回来，不能把 A 的详情盖在 B 上。→ T1 `clearRoomUi` 测试 + T4 e2e `switching project … closes the old task detail`。
3. **直接 boot 进 swarm 模式（首连接前 project=null）** —— 房间仍要加载一次，project 落地后自动 resync，null 不得崩。→ T1 `swarmLoadPlan`/`load(null)` 测试。
4. **保存角色后的刷新** —— 不得清掉开着的任务详情（刷新 ≠ 换房间）。→ T1 `load keeps an open task selection` + T4 e2e `saving a role keeps the open task detail`。
5. **ModeSwitcher 徽标不得预载房间** —— host 对 swarm manager 懒创建，不进 swarm 的用户不付常驻 runtime 成本；未 loaded 时计数恒 0。→ T1 `swarmActiveCount` 测试 + T5 e2e `no list call before entering swarm`。

---

### Task 1: swarmStore 项目接缝

**Files:**
- Modify: `desktop/src/state/swarmStore.ts`（209 行；状态字段 + load + 两个纯函数）
- Modify: `desktop/src/ui/pages/RoomPage.tsx:39-48,71`（load 调用点传 project）
- Modify: `desktop/src/ui/pages/AgentSettings.tsx:75-85,99,116`（同上，3 处）
- Test: `desktop/tests/swarmStore.test.ts`（扩展）

**Interfaces:**
- Consumes: 现有 `_useSwarmSender` 测试接缝、`SwarmListResult`、`SwarmTask`（`../rpc/types`）。
- Produces（T4/T5 依赖）:
  - `load(project: string | null): Promise<void>` —— 请求 `{type:'swarm', op:'list', ...(project!==null?{project}:{})}`；模块级 `let loadSeq = 0`，调用时 `const token = ++loadSeq`，应答后 `token !== loadSeq` 则丢弃；成功 set 房间字段 + `loaded: true` + `loadedProject: project`（**请求发起时**的项目）。**永不**触碰 `selectedTask`/`runRows`。
  - 状态字段 `selectedTask: string | null`（初始 null）、`loadedProject: string | null`（初始 null）。
  - `selectTask(id: string): void` —— toggle：`selectedTask === id ? null : id`。
  - `clearRoomUi(): void` —— `set({ selectedTask: null, runRows: {} })`。
  - `export function swarmLoadPlan(loaded: boolean, loadedProject: string | null, project: string | null): 'none' | 'load' | 'switch'` —— `!loaded → 'load'`；`loadedProject === project → 'none'`；否则 `'switch'`。
  - `export function swarmActiveCount(loaded: boolean, tasks: SwarmTask[]): number` —— `!loaded → 0`；否则 `tasks.filter(t => t.state === 'running' || t.state === 'queued').length`。

- [ ] **Step 1: 写失败测试**（追加到 `swarmStore.test.ts`；`reset()` 补齐 `selectedTask: null, loadedProject: null, runRows: {}`；现有 2 处 `load()` 调用改 `load(null)`）

```typescript
test('selectTask toggles the selection', () => {
  reset()
  useSwarmStore.getState().selectTask('t_1')
  assert.equal(useSwarmStore.getState().selectedTask, 't_1')
  useSwarmStore.getState().selectTask('t_1')
  assert.equal(useSwarmStore.getState().selectedTask, null)
  useSwarmStore.getState().selectTask('t_1')
  useSwarmStore.getState().selectTask('t_2')
  assert.equal(useSwarmStore.getState().selectedTask, 't_2')
})

test('load records the requesting project and sends it on the wire', async () => {
  reset()
  const sent = fakeClient(() => listAnswer())
  await useSwarmStore.getState().load('/work/a')
  assert.equal(useSwarmStore.getState().loadedProject, '/work/a')
  assert.deepEqual(sent[0], { type: 'swarm', op: 'list', project: '/work/a' })
  await useSwarmStore.getState().load(null)
  assert.equal(useSwarmStore.getState().loadedProject, null)
  assert.deepEqual(sent[1], { type: 'swarm', op: 'list' })
})

test('a stale load answer is dropped', async () => {
  reset()
  let resolveA!: (value: unknown) => void
  fakeClient((request) =>
    request.project === '/a'
      ? new Promise((resolve) => { resolveA = resolve })
      : listAnswer('b'),
  )
  const a = useSwarmStore.getState().load('/a')
  const b = useSwarmStore.getState().load('/b')
  await b
  resolveA(listAnswer('a'))
  await a
  const state = useSwarmStore.getState()
  assert.equal(state.loadedProject, '/b')
  assert.equal(state.messages[0].id, 'm_b')
})

test('load keeps an open task selection (save refresh)', async () => {
  reset()
  fakeClient(() => listAnswer())
  useSwarmStore.getState().selectTask('t_1')
  await useSwarmStore.getState().load('/work/a')
  assert.equal(useSwarmStore.getState().selectedTask, 't_1')
})

test('clearRoomUi drops selection and live rows but keeps room data', async () => {
  reset()
  fakeClient(() => listAnswer())
  await useSwarmStore.getState().load('/work/a')
  useSwarmStore.getState().selectTask('t_1')
  useSwarmStore.getState().applyFrame({
    type: 'swarm_event', swarm: true, agent: 'research', task: 't_1',
    event: { type: 'tool_execution_start', tool_call_id: 'c1', tool_name: 'bash' },
  } as never)
  useSwarmStore.getState().clearRoomUi()
  const state = useSwarmStore.getState()
  assert.equal(state.selectedTask, null)
  assert.deepEqual(state.runRows, {})
  assert.equal(state.loaded, true)
  assert.equal(state.messages.length, 1)
})

test('swarmLoadPlan: boot loads, same project stays, different switches', () => {
  assert.equal(swarmLoadPlan(false, null, null), 'load')
  assert.equal(swarmLoadPlan(false, null, '/a'), 'load')
  assert.equal(swarmLoadPlan(true, '/a', '/a'), 'none')
  assert.equal(swarmLoadPlan(true, null, '/a'), 'switch')
  assert.equal(swarmLoadPlan(true, '/a', '/b'), 'switch')
})

test('swarmActiveCount only counts live tasks once loaded', () => {
  const tasks = [taskFrame('t_1', 'running'), taskFrame('t_2', 'queued'), taskFrame('t_3', 'done')]
  assert.equal(swarmActiveCount(false, tasks), 0)
  assert.equal(swarmActiveCount(true, tasks), 2)
  assert.equal(swarmActiveCount(true, []), 0)
})
```

`listAnswer(tag = 'a')` 是本文件新增的 fixture helper：返回 `{ type:'result', id:'1', ok:true, roles:[], agents:[], tasks:[], messages:[messageFrame('m_'+tag, tag)], warnings:[] }`。import 行补 `swarmLoadPlan, swarmActiveCount`。

- [ ] **Step 2: 跑测验证 RED**

Run: `cd desktop && node --test tests/swarmStore.test.ts`
Expected: FAIL —— `load` 不接受参数 / `selectedTask` 等未定义（TS stripping 下表现为断言失败或 TypeError）。

- [ ] **Step 3: 实现 store 变更**

按 Interfaces 块改 `swarmStore.ts`：`SwarmState` 接口加字段与方法签名；create 初始值加 `selectedTask: null, loadedProject: null`；`load` 用 token guard 重写；`selectTask`/`clearRoomUi` 如上；两个纯函数 export 在 store 定义之外（文件顶部 helper 区）。文件头注释补一句：list 请求携带发起时项目，为按项目房间预留。

- [ ] **Step 4: 更新 5 个调用点（保持 tsc 绿）**

`RoomPage.tsx`：加 `import { useAppStore } from '../../state/appStore'`；组件内 `const project = useAppStore((state) => state.project)`；挂载 effect 改 `if (!loaded) void load(project)`，deps `[loaded, load, project]`；`save()` 内 `await load(project)`。
`AgentSettings.tsx`：同样加 import 与 project selector；`:85` `void load(project)`（deps 补 project）、`:99` 与 `:116` `await load(project)`。

- [ ] **Step 5: 跑测验证 GREEN**

Run: `cd desktop && npm test && npm run build`
Expected: PASS（全部单测 + tsc；其余测试不受影响）。

- [ ] **Step 6: Commit**

```bash
git add desktop/src/state/swarmStore.ts desktop/src/ui/pages/RoomPage.tsx desktop/src/ui/pages/AgentSettings.tsx desktop/tests/swarmStore.test.ts
git commit -m "feat(desktop): swarm store loads per requesting project with a latest-request guard"
```

---

### Task 2: Sidebar 壳拆分（行为不变）

**Files:**
- Create: `desktop/src/ui/SidebarCoding.tsx`（~700 行，新文件 cap 800）
- Modify: `desktop/src/ui/Sidebar.tsx`（853 → ~120 行）

**Interfaces:**
- Consumes: 现有 `Sidebar.module.css`（两文件共用）、stores、`ZenRoster`、`ModeSwitcher`。
- Produces: `export function SidebarCoding(): JSX.Element`（无 props，自读 stores）；`Sidebar(props: { onCollapse(): void })` 签名不变（App.tsx 不改）。

- [ ] **Step 1: 基线回归**

Run: `cd desktop && npx playwright test e2e/app.spec.ts e2e/zen.spec.ts e2e/swarm.spec.ts`
Expected: PASS（拆分前绿基线）。

- [ ] **Step 2: 迁出 coding 主体**

创建 `SidebarCoding.tsx`，从 `Sidebar.tsx` **原样**迁入：
- `RowMenu` 接口、`SECTION_KEY`、`readClosedSections`（现 :56-73）；
- `Sidebar` 函数中除 header/`<ModeSwitcher />`/`<Footer />` 外的全部 hooks、state、helpers 与 JSX（现 :76-332 的逻辑 + :357-451 的 sections + :455-555 的 menu portal），改名为 `export function SidebarCoding()`，返回 `<>…</>` fragment（保持 .root flex 布局的直接子级关系）；
- `SessionTools`、`SessionRow`、`RenameInput`（现 :560-801）。
- import 按需搬移；css 仍 `import css from './Sidebar.module.css'`。
- 名册换肤条件 `zenEnabled || workspaceMode === 'zen'`（现 :398）**原样保留**。

`Sidebar.tsx` 只留：shell imports、`Sidebar` 函数（header JSX 原样 + `<ModeSwitcher />` + 分发 + `<Footer />`）、`Footer` 函数。分发暂为（T3/T4 逐个替换）：

```tsx
{workspaceMode === 'zen' ? <SidebarCoding /> : workspaceMode === 'swarm' ? <SidebarCoding /> : <SidebarCoding />}
```

（即三模式都渲染 SidebarCoding——与今天行为完全一致；写成三元是为了 T3/T4 只改一个分支。shell 只保留 `workspaceMode` 一个 store 订阅。）

- [ ] **Step 3: 验证预算与回归**

Run: `wc -l desktop/src/ui/Sidebar.tsx desktop/src/ui/SidebarCoding.tsx && cd desktop && npm run build && npx playwright test e2e/app.spec.ts e2e/zen.spec.ts e2e/swarm.spec.ts`
Expected: Sidebar.tsx ≤854、SidebarCoding.tsx ≤800；build 与三套 e2e 全 PASS。

- [ ] **Step 4: Commit**

```bash
git add desktop/src/ui/Sidebar.tsx desktop/src/ui/SidebarCoding.tsx
git commit -m "refactor(desktop): split the sidebar shell from the coding body"
```

---

### Task 3: ProjectSwitcher + ZenSidebar

**Files:**
- Create: `desktop/src/ui/ProjectSwitcher.tsx`、`desktop/src/ui/ProjectSwitcher.module.css`、`desktop/src/ui/ZenSidebar.tsx`
- Modify: `desktop/src/ui/Sidebar.tsx`（zen 分支 → `<ZenSidebar />`；import）
- Modify: `desktop/src/ui/Sidebar.module.css`（追加 `.zenBody`）
- Modify: `desktop/src/i18n/messages.ts`（zh/en 各 3 键）
- Test: `desktop/e2e/zen.spec.ts`（+2 测试）

**Interfaces:**
- Consumes: `useAppStore`（`project: string | null`、`openProject(path: string, target?: string | null): Promise<void>`）、`useProjectStore`（`projects: ProjectEntry[] {path,name,at}`、`addProject(path)`）、`projectName(path)`（`../state/sessionGroups`）、`pickProjectFolder(): Promise<string | null>`（`./sessions/fileDialog`）、`FolderIcon/ChevronDownIcon/CheckIcon`（`./icons`）。
- Produces: `export function ProjectSwitcher(): JSX.Element`（T4 SwarmSidebar 复用）；`export function ZenSidebar(): JSX.Element`。
- data 属性（e2e 契约）: 触发钮 `data-project-switcher`；菜单 `data-project-switcher-menu`；项目项 `data-project-option={path}`（当前项另带 `data-current`）；打开文件夹 `data-project-open-folder`。

- [ ] **Step 1: 写失败 e2e**（追加到 `zen.spec.ts`；import assert）

```typescript
test('zen sidebar switches project through the compact switcher', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.projects', JSON.stringify([{ path: '/work/q', name: 'q', at: 1 }]))
  })
  await boot(page, initialState())
  await page.locator('[data-workspace-mode="zen"]').click()

  const trigger = page.locator('[data-project-switcher]')
  await expect(trigger).toContainText('p')
  await trigger.click()
  const menu = page.locator('[data-project-switcher-menu]')
  await expect(menu).toBeVisible()
  await expect(menu.locator('[data-project-option]')).toHaveCount(2)
  await expect(menu.locator('[data-project-option="/work/p"]')).toHaveAttribute('data-current', 'true')

  // 键盘：ArrowDown 到 q，Enter 选中；Escape 关闭并恢复焦点。
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(menu).toHaveCount(0)
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.ok(calls.some((call) => call.type === 'project' && call.op === 'switch' && call.root === '/work/q'))
  await expect(trigger).toContainText('q')
  await expect(page.locator('[data-zen-roster]')).toBeVisible()

  await trigger.click()
  await expect(menu).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('the switcher opens a new folder through the dialog', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: '/work/r' }))
  await page.locator('[data-workspace-mode="zen"]').click()
  await page.locator('[data-project-switcher]').click()
  await page.locator('[data-project-open-folder]').click()
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.ok(calls.some((call) => call.type === 'project' && call.op === 'switch' && call.root === '/work/r'))
  await expect(page.locator('[data-project-switcher]')).toContainText('r')
})
```

（boot 把活跃项目 `/work/p` 注册进 rail（appStore:216），preset 只补 q；bridge 默认不 relocate，`/work/p` 即字面值。dialog mock：`initialState({ dialogSelection })` 应答 `plugin:dialog|open`。）

- [ ] **Step 2: 跑测验证 RED**

Run: `cd desktop && npx playwright test e2e/zen.spec.ts -g "compact switcher|new folder"`
Expected: FAIL —— `[data-project-switcher]` 不存在。

- [ ] **Step 3: i18n 键**（`messages.ts` zh/en 对应位置成对添加）

- `sidebar.projectSwitcher`: zh `切换项目` / en `Switch project`（触发钮 aria-label）
- `sidebar.openFolder`: zh `打开文件夹…` / en `Open folder…`
- `sidebar.noProject`: zh `未打开项目` / en `No project open`（project=null 时的触发钮文案）

- [ ] **Step 4: 实现 ProjectSwitcher**

结构：触发钮（FolderIcon + `project !== null ? projectName(project) : t('sidebar.noProject')` + ChevronDownIcon，`aria-haspopup="menu"` `aria-expanded`）；打开时 `createPortal` 菜单到 body，`position: fixed`，`left = Math.max(8, rect.left)`、`top = rect.bottom + 4`、宽度 `max(rect.width, 220)`px（inline style）。菜单内容：`projects.map` 项（name + path caption，`project === entry.path` 时 CheckIcon + `data-current`）+ 分隔线 + 打开文件夹项。

交互**仿 SidebarCoding 行菜单**（现 Sidebar.tsx 迁出的 menu effect）：打开后 focus 首个 menuitem；document `pointerdown`  outside 关闭（触发钮自身点击除外）；`keydown` capture 阶段 Escape 关闭 + 恢复焦点 + `stopImmediatePropagation`；菜单内 ArrowDown/Up 循环 focus、Tab 关闭。项目项点击：先关菜单再 `void openProject(entry.path)`；打开文件夹：`const path = await pickProjectFolder(); if (path !== null) { addProject(path); await openProject(path) }`（关菜单在 await 前）。拒绝横幅由 openProject 现有路径承载，组件不另做错误态。

CSS（`ProjectSwitcher.module.css`，全 ds tokens）：`.trigger`（flex 行、gap 8、padding 6px 8px、radius-6、bg-layer-2、label-primary、font-12、hover interactive-bg-hover、文本 ellipsis）；`.menu`（仿 Sidebar.module.css `.menu`：fixed、z-index 40、padding 4、border-l1、radius-8、bg-layer-2、elevation shadow 用 `var(--dsw-elevation-soft)`）；`.item`（仿 `.menuItem`）；`.itemPath`（font-10-5、label-caption、ellipsis）；`.separator`（1px border-l1、margin 4px 0）；`.check`（flex none、label-secondary）。

- [ ] **Step 5: 实现 ZenSidebar + 分发**

```tsx
export function ZenSidebar() {
  return (
    <div className={css.zenBody} data-zen-sidebar>
      <ProjectSwitcher />
      <ZenRoster />
    </div>
  )
}
```

css 用 `Sidebar.module.css`；追加 `.zenBody { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding: 4px 10px 12px; }`。ZenRoster 原样复用（paused 横幅在其内部，fix 契约）。壳分发 zen 分支改 `<ZenSidebar />`。

- [ ] **Step 6: 跑测验证 GREEN**

Run: `cd desktop && npm test && npm run build && npx playwright test e2e/zen.spec.ts e2e/app.spec.ts`
Expected: PASS（locale parity 含新键；zen 五测试 + coding 回归）。

- [ ] **Step 7: Commit**

```bash
git add desktop/src/ui/ProjectSwitcher.tsx desktop/src/ui/ProjectSwitcher.module.css desktop/src/ui/ZenSidebar.tsx desktop/src/ui/Sidebar.tsx desktop/src/ui/Sidebar.module.css desktop/src/i18n/messages.ts desktop/e2e/zen.spec.ts
git commit -m "feat(desktop): zen sidebar with a compact project switcher"
```

---

### Task 4: SwarmSidebar + 面板搬移 + RoomPage 单列

**Files:**
- Create: `desktop/src/ui/SwarmRoster.tsx`、`desktop/src/ui/SwarmTasks.tsx`、`desktop/src/ui/SwarmSidebar.tsx`、`desktop/src/ui/SwarmPanels.module.css`
- Modify: `desktop/src/ui/pages/RoomPage.tsx`（662 → ~470 行：删 Roster/TaskStrip/editing/save/aside）
- Modify: `desktop/src/ui/pages/RoomPage.module.css`（删搬移类；.root 单列）
- Modify: `desktop/src/ui/Sidebar.tsx`（swarm 分支 → `<SwarmSidebar />`）
- Test: `desktop/e2e/swarm.spec.ts`（现有 9 测试回归 + 2 新测试）

**Interfaces:**
- Consumes: T1 的 `selectedTask/selectTask/loadedProject/clearRoomUi/swarmLoadPlan/load(project)`；T3 的 `ProjectSwitcher`；`AgentModal`（`./pages/agentEditor`，props `{row: SwarmRole | null, busy, onClose, onSave}`）。
- Produces:
  - `export function SwarmRoster(props: { busy: boolean; onConfigure(role: SwarmRole): void; onCreate(): void })` —— 自读 store 的 agents/roles/tasks；numbers 自派生 `useMemo(() => new Map(tasks.map((task, index): [string, number] => [task.id, index + 1])), [tasks])`。
  - `export function SwarmTasks()` —— 自读 tasks/selectedTask/selectTask；numbers 同上自派生。
  - `export function SwarmSidebar()` —— 组合 + AgentModal + 项目变更 effect。
- 搬移规则：Roster（RoomPage 现 :310-404）与 TaskStrip（:406-484）的 JSX **原样**（data 属性、按钮、空态、`t()` 键全保留）；仅 props 取数改为 store 订阅、`onSelect` 改 `selectTask`。样式类从 RoomPage.module.css 迁入 SwarmPanels.module.css：`side block blockHead rosterActions rowActions title blockEmpty rows agentRow taskItem taskRow taskToggle rowMain rowTitle rowCaption stateDot taskState badge ghost`（含全部 data-state/selected/hover/disabled 变体）。

- [ ] **Step 1: 写失败 e2e**（追加到 `swarm.spec.ts`）

```typescript
test('switching project reloads the room and closes the old task detail', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.projects', JSON.stringify([{ path: '/work/q', name: 'q', at: 1 }]))
  })
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-task-toggle]').first().click()
  await expect(page.locator('[data-swarm-main] [data-swarm-task-detail]')).toBeVisible()
  const before = (await page.evaluate(() => window.__ZEN_E2E__.calls))
    .filter((call) => call.type === 'swarm' && call.op === 'list').length

  await page.locator('[data-project-switcher]').click()
  await page.locator('[data-project-option="/work/q"]').click()

  await expect(page.locator('[data-swarm-task-detail]')).toHaveCount(0)
  await expect(page.locator('[data-swarm-feed]')).toBeVisible()
  const lists = (await page.evaluate(() => window.__ZEN_E2E__.calls))
    .filter((call) => call.type === 'swarm' && call.op === 'list')
  assert.ok(lists.length > before, 'project switch must re-list the room')
  assert.equal(lists.at(-1)?.project, '/work/q')
})

test('saving a role keeps the open task detail', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()
  await page.locator('[data-swarm-task-toggle]').first().click()
  const detail = page.locator('[data-swarm-main] [data-swarm-task-detail]')
  await expect(detail).toBeVisible()

  await page.locator('[data-swarm-configure="research"]').click()
  const modal = page.locator('[data-swarm-role-modal]')
  await modal.getByRole('button', { name: '保存' }).click()
  await expect(modal).toHaveCount(0)
  await expect(detail).toBeVisible()
})
```

- [ ] **Step 2: 跑测验证 RED**

Run: `cd desktop && npx playwright test e2e/swarm.spec.ts -g "switching project|saving a role keeps"`
Expected: FAIL —— 侧边栏无 `[data-project-switcher]`（swarm 模式仍是 coding 侧边栏）。

- [ ] **Step 3: 抽出面板与样式**

按搬移规则创建 `SwarmRoster.tsx`/`SwarmTasks.tsx`/`SwarmPanels.module.css`。`.side` 迁入时 padding 改 `4px 10px 12px` 并加 `flex: 1`（侧边栏沟槽；原 `16px 16px 16px 0` 是右栏语境）。`SwarmTasks` 的 `selectedId`/`onSelect` 改读 `selectedTask`/`selectTask`。

- [ ] **Step 4: 实现 SwarmSidebar**

```tsx
export function SwarmSidebar() {
  const project = useAppStore((state) => state.project)
  const loaded = useSwarmStore((state) => state.loaded)
  const loadedProject = useSwarmStore((state) => state.loadedProject)
  const [editing, setEditing] = useState<SwarmRole | 'new' | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const plan = swarmLoadPlan(loaded, loadedProject, project)
    if (plan === 'none') return
    if (plan === 'switch') useSwarmStore.getState().clearRoomUi()
    void useSwarmStore.getState().load(project)
  }, [project, loaded, loadedProject])

  // save()：从 RoomPage 现 :61-79 原样迁来，刷新改 `await load(project)`（不清选择）
  return (
    <div className={css.swarmBody} data-swarm-sidebar>
      <ProjectSwitcher />
      <div className={panels.side}>
        <SwarmRoster busy={busy} onConfigure={setEditing} onCreate={() => setEditing('new')} />
        <SwarmTasks />
      </div>
      {editing !== null && (
        <AgentModal row={editing === 'new' ? null : editing} busy={busy}
          onClose={() => setEditing(null)} onSave={save} />
      )}
    </div>
  )
}
```

css 用 `Sidebar.module.css`（追加 `.swarmBody { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 8px; padding: 4px 0 0; }`，面板滚动交给 `.side`）；panels 用 `SwarmPanels.module.css`。AgentModal 遮罩 fixed inset 0 z-60，挂侧边栏树内即可（折叠态也可用）。

- [ ] **Step 5: RoomPage 瘦身**

删除：`Roster`/`TaskStrip` 函数、`editing`/`busy` state、`save()`、`<aside>`、AgentModal import 与渲染、`setSelectedTask`。`selectedTask` 改 `useSwarmStore((state) => state.selectedTask)`；`onClose` 改 `() => selectTask(selectedTask)`——直接用 `useSwarmStore((state) => state.selectTask)` 的 toggle 语义：`onClose={() => selectTask(detail.id)}`（detail 非 null 时 toggle 必关）。保留 feed/composer/warnings/ApprovalPanel/TaskDetailPanel/numbers。`.root` 的 `grid-template-columns` 改 `minmax(0, 1fr)`；删 `.side` 及已迁出类；TaskDetailPanel 仍用的共享类（`ghost rowCaption taskState blockEmpty`）改 import `panels from '../SwarmPanels.module.css'` 引用。文件头注释更新（侧栏已搬出）。

- [ ] **Step 6: 壳分发 + 回归**

Sidebar.tsx swarm 分支改 `<SwarmSidebar />`。
Run: `cd desktop && npm test && npm run build && npm run lint:tokens && npx playwright test e2e/swarm.spec.ts`
Expected: PASS —— 现有 9 测试选择器不变应全绿（detail 断言 `[data-swarm-main] [data-swarm-task-detail]` 天然成立）+ 2 新测试。若有断言因 DOM 位置失败，修断言不修选择器。

- [ ] **Step 7: Commit**

```bash
git add desktop/src/ui/SwarmRoster.tsx desktop/src/ui/SwarmTasks.tsx desktop/src/ui/SwarmSidebar.tsx desktop/src/ui/SwarmPanels.module.css desktop/src/ui/Sidebar.tsx desktop/src/ui/Sidebar.module.css desktop/src/ui/pages/RoomPage.tsx desktop/src/ui/pages/RoomPage.module.css desktop/e2e/swarm.spec.ts
git commit -m "feat(desktop): swarm sidebar owns the roster and tasks; room page goes single column"
```

---

### Task 5: ModeSwitcher 活动指示

**Files:**
- Modify: `desktop/src/ui/ModeSwitcher.tsx`（48 行）
- Modify: `desktop/src/ui/ModeSwitcher.module.css`（追加 `.zenDot`/`.swarmBadge`）
- Modify: `desktop/src/i18n/messages.ts`（zh/en 各 2 键）
- Test: `desktop/e2e/zen.spec.ts`（+1）、`desktop/e2e/swarm.spec.ts`（+1）

**Interfaces:**
- Consumes: T1 `swarmActiveCount`；`useZenStore((s) => s.enabled)`；`useSwarmStore((s) => swarmActiveCount(s.loaded, s.tasks))`（selector 返回 number，原始值订阅）。
- Produces: zen 段 `<span className={css.zenDot} data-zen-active aria-hidden />`（enabled 时）；swarm 段 `<span className={css.swarmBadge} data-swarm-badge>{count}</span>`（count>0 时）；活动态 tab 的 `aria-label` = `${t(labelKey(entry))} · ${状态文案}`，非活动态不设 aria-label（保留文本可访问名）。

- [ ] **Step 1: 写失败 e2e**

`zen.spec.ts` 追加：

```typescript
test('the zen tab shows a fleet dot while the fleet is enabled', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-zen-active]')).toHaveCount(0)
  await page.locator('[data-workspace-mode="zen"]').click()
  await expect(page.locator('[data-zen-active]')).toBeVisible()
  await page.locator('[data-zen-stop]').click()
  await expect(page.locator('[data-zen-active]')).toHaveCount(0)
})
```

`swarm.spec.ts` 追加：

```typescript
test('the swarm tab badge counts live tasks without eager-loading the room', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  // 未进 swarm：不预载，徽标不显示
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.ok(!calls.some((call) => call.type === 'swarm' && call.op === 'list'))
  await expect(page.locator('[data-swarm-badge]')).toHaveCount(0)

  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-badge]')).toHaveText('1')
  await page.locator('[data-workspace-mode="coding"]').click()
  await expect(page.locator('[data-swarm-badge]')).toHaveText('1')
})
```

- [ ] **Step 2: 跑测验证 RED**

Run: `cd desktop && npx playwright test e2e/zen.spec.ts -g "fleet dot" && npx playwright test e2e/swarm.spec.ts -g "badge"`
Expected: FAIL —— 两个 data 属性不存在。

- [ ] **Step 3: i18n + 实现**

键：`workspace.zenActive` zh `Fleet 运行中` / en `Fleet running`；`workspace.swarmActive` zh `{count} 个任务进行中` / en `{count} tasks active`。
ModeSwitcher：两个 store 订阅；map 内 zen 段 enabled 时渲染 dot、swarm 段 count>0 时渲染 badge；活动态 aria-label 如上。CSS：`.zenDot`（6px 圆点、`var(--dsw-alias-state-business-primary)`，仿 Sidebar `.projectActiveDot`）；`.swarmBadge`（font-10-5、padding 0 5px、radius-4、bg-layer-3、label-secondary、min-width 14px、text-align center）。tab 内布局：文本与指示器间 gap 4（`.tab` 追加 `display: flex; align-items: center; justify-content: center; gap: 4px;`，保留现有 ellipsis 行为——文本 span 包一层 `.tabLabel` 承载 overflow）。

- [ ] **Step 4: 跑测验证 GREEN**

Run: `cd desktop && npm test && npm run build && npx playwright test e2e/zen.spec.ts e2e/swarm.spec.ts e2e/app.spec.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add desktop/src/ui/ModeSwitcher.tsx desktop/src/ui/ModeSwitcher.module.css desktop/src/i18n/messages.ts desktop/e2e/zen.spec.ts desktop/e2e/swarm.spec.ts
git commit -m "feat(desktop): cross-mode activity cues on the workspace switcher"
```

---

### Task 6: 全门禁 + CHANGELOG + 收尾

**Files:**
- Modify: `CHANGELOG.md`（Unreleased 节增补子节）
- Verify: 全部门禁与预算

- [ ] **Step 1: 全门禁**

Run（worktree 根）: `bash scripts/check.sh`
Run: `cd desktop && npm test && npm run build && npm run lint:tokens && npm run e2e`
Expected: 全 PASS。e2e 全量含 app/zen/swarm/plugin/dev-browser 五套。

- [ ] **Step 2: 预算复核**

Run: `python3 scripts/check_architecture.py && wc -l desktop/src/ui/Sidebar.tsx desktop/src/ui/SidebarCoding.tsx desktop/src/ui/SwarmSidebar.tsx desktop/src/ui/ProjectSwitcher.tsx desktop/src/state/swarmStore.ts desktop/src/state/appStore.ts`
Expected: PASS；appStore.ts 仍 1175；新文件均 ≤800。

- [ ] **Step 3: CHANGELOG**

在 `## Unreleased — Zen 模式 × Swarm …` 节内追加 `### 模式感知侧边栏` 子节（zh/en 双语风格随现有条目）：coding/zen/swarm 三态侧边栏、共享项目下拉（复用 openProject）、swarm 面板从房间右栏搬入侧边栏、房间单列、swarmStore 按请求项目加载的接缝（为后端按项目化预留）、ModeSwitcher 活动指示。已发布条目一字不动。

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(desktop): changelog for the mode-aware sidebars"
```

- [ ] **Step 5: 汇报**

向用户报告：分支提交链、门禁结果、预算数字、与 fix 分支的合流建议（再并一次 fix tip）。**不 push、不合并**——集成决定归用户。