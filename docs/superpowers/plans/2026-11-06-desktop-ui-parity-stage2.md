# Desktop UI Full Parity with pi-desktop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the zen desktop shell so it structurally and visually matches pi-desktop across the sidebar, topbar, home, transcript, composer, work panel, and footer surfaces, using only the existing zen RPC.

**Architecture:** Keep the React/zustand/RPC architecture; replace the scope-based sidebar with pi's `置顶 / 会话 / 项目` sections, move the composer into the main pane as a measured bottom dock, group transcript nodes into folds, and add local settings/tools/notification surfaces behind pi's footer. pi-desktop is a read-only visual reference (LGPL-3.0): measurements and behavior, never copied source.

**Tech Stack:** Tauri v2, React 18, zustand 5, strict TypeScript, CSS Modules, Node native TS tests (`node --test tests/*.test.ts`), Playwright over the scripted `__TAURI_INTERNALS__` bridge.

**Spec:** `docs/superpowers/specs/2026-11-06-desktop-ui-parity-stage2-design.md`

## Global Constraints

- Work only in `desktop/` plus docs; no server/RPC changes. Available requests: `prompt, cancel, status, shutdown, steer, tools, models, commands, transcript, model, mode, usage, approval, session, command`.
- Cross-project sessions are visible but **never resumable**: keep the fail-closed guard (`isCrossProject` + palette default-deny).
- No dead affordances. If the RPC cannot back it, do not render it (see spec exclusions).
- Preserve Stage 1 guarantees: capture-phase Escape in menus, keyboard-reachable row menus, silent rename/pin success, focus restoration.
- CSS Modules + `--ds-*` / `--dsw-*` tokens only; no Tailwind, no second styling system.
- Do not push. Stage only files named in each task. Leave the two pre-existing untracked `2026-11-06-session-format-gui-contract` docs untouched.
- Reply to the user in Chinese. Tests first: every task watches its new test fail before implementation, and the full suite runs green before the task's ledger line.

## Review Focus

The five inputs most likely to break a person using the app, each pinned by a test in its owning task:

1. **A cross-project row with no current session** — opening it must remain impossible; the guard must fail closed (`sessionSelectors.test.ts` + e2e, Task 2).
2. **A short window (720px tall) with the composer dock** — the last message must scroll above the dock, never under it (e2e bounding boxes, Task 7).
3. **A resumed/imported transcript with no leading user row or an interrupted tool** — turn grouping must still render every node exactly once (unit test, Task 5).
4. **Query + collapsed project + load more** — filtering must not resurrect hidden rows or lose the collapse state (unit + e2e, Task 2).
5. **Leaving Settings/Tools or switching project** — focus and the previous scroll position must not trap the user (e2e, Tasks 4 and 9).

---

### Task 1: Shell geometry and the composer dock

**Files:**
- Modify: `desktop/src/styles/tokens.css`
- Modify: `desktop/src/ui/columns.ts`
- Modify: `desktop/src/ui/AppFrame.tsx`, `desktop/src/ui/AppFrame.module.css`
- Modify: `desktop/src/App.tsx`, `desktop/src/App.module.css`
- Test: `desktop/tests/columns.test.ts`, `desktop/tests/tokens.test.ts`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: existing `AppFrame` props.
- Produces: `AppFrame` without the `bottom` prop; `CENTER_MIN = 450`, `RIGHTBAR_MIN = 244`, `RIGHTBAR_DEFAULT = 360`.

- [ ] **Step 1: Update the failing token/column tests**

In `tokens.test.ts`, change the topbar assertion to `--ds-topbar-height:\s*46px;` and add:
```ts
assert.match(css, /--ds-toolbar-height:\s*46px;/)
assert.match(css, /--ds-chat-composer-width:\s*768px;/)
assert.match(css, /--ds-sidebar-footer-height:\s*40px;/)
assert.match(css, /--ds-shadow-composer:/)
```
In `columns.test.ts`:
```ts
assert.equal(CENTER_MIN, 450)
assert.equal(RIGHTBAR_MIN, 244)
assert.equal(RIGHTBAR_DEFAULT, 360)
assert.equal(computeColumns(1280, 275, 360).rightbar, 360)
assert.equal(computeColumns(900, 275, 360).rightbar, 0) // center would drop below 450
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd desktop && pnpm test`
Expected: FAIL on the new token/column assertions.

- [ ] **Step 3: Add tokens and update the column solve**

Add the four tokens above to `tokens.css` (`--ds-topbar-height` becomes `var(--ds-toolbar-height)` so both names resolve to 46px). In `columns.ts` set `CENTER_MIN = 450`, `RIGHTBAR_MIN = 244`, `RIGHTBAR_DEFAULT = 360`; keep the drag clamp and ratio solve.

- [ ] **Step 4: Move the composer into the main pane**

Remove `bottom` from `AppFrameProps` and the `.bottomRow` render. `App.tsx` renders inside the frame:
```tsx
<ConversationTopbar ... />
<NoticeBanner />
<ChatView />
<div className={css.composerDock}><InputBar /></div>
```
`App.module.css` `.composerDock`: `position:absolute; inset-inline:0; bottom:0; padding:0 24px 16px; display:flex; justify-content:center; pointer-events:none;` with `.composerDock > * { pointer-events:auto; }`. The sidebar and right rail now run full height.

- [ ] **Step 5: Add the fixed work-panel toggle**

In `App.tsx`, before `</AppFrame>`, render a fixed 28px button at `top:9px; right:12px; z-index:30`, `aria-expanded={rightbarOpen}`, toggling the inspector. Remove the inspector button from `ConversationTopbar`.

- [ ] **Step 6: Add the shell e2e assertion**

In `app.spec.ts`, after `boot`:
```ts
const sidebar = await page.locator('aside').boundingBox()
const viewport = page.viewportSize()
assert.equal(Math.round(sidebar!.height), viewport!.height)
const composer = await page.locator('[data-shell-composer]').boundingBox()
const center = await page.locator('[data-shell-center]').boundingBox()
assert.ok(composer!.x >= center!.x && composer!.x + composer!.width <= center!.x + center!.width)
```
Add `data-shell-center` to the center column and `data-shell-composer` to the dock.

- [ ] **Step 7: Run all gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`
Expected: all green. Commit `feat(desktop): pi shell geometry and composer dock`.

---

### Task 2: Sidebar `置顶 / 会话 / 项目`

**Files:**
- Modify: `desktop/src/state/sessionGroups.ts`
- Modify: `desktop/src/state/stores.ts`
- Modify: `desktop/src/ui/Sidebar.tsx`, `desktop/src/ui/Sidebar.module.css`
- Modify: `desktop/src/i18n/locale.ts`
- Test: `desktop/tests/sessionGroups.test.ts`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `SessionSummary` (`project` may be `''`), `isCrossProject`, `canForkSession`.
- Produces:
```ts
export const PROJECT_PAGE_SIZE = 10
export interface SessionGroup { project: string; sessions: SessionSummary[] }
export interface SidebarSections {
  pinned: SessionSummary[]
  standalone: SessionSummary[]   // sessions with no project
  projects: SessionGroup[]       // non-empty project, newest group first
}
export function sessionSections(sessions: SessionSummary[], query: string): SidebarSections
```
- Produces store fields: `collapsedProjects: string[]`, `toggleProject(project: string): void`, persisted under `zen.sidebar.projects`.

- [ ] **Step 1: Write the failing section tests**

Replace the scope cases in `sessionGroups.test.ts` with:
```ts
test('sections split pinned, projectless and project groups', () => {
  const sections = sessionSections([
    make('/s/a.jsonl', 9, { project: '' }),
    make('/s/b.jsonl', 8, { project: 'alpha' }),
    make('/s/c.jsonl', 7, { project: 'beta' }),
    make('/s/d.jsonl', 6, { project: 'beta', pinned: true }),
  ], '')
  assert.deepEqual(sections.pinned.map((s) => s.path), ['/s/d.jsonl'])
  assert.deepEqual(sections.standalone.map((s) => s.path), ['/s/a.jsonl'])
  assert.deepEqual(sections.projects.map((g) => g.project), ['beta', 'alpha'])
})

test('the query filters every section and keeps collapse data intact', () => {
  const sections = sessionSections([
    make('/s/a.jsonl', 9, { project: '', title: 'Fix login' }),
    make('/s/b.jsonl', 8, { project: 'alpha', title: 'other' }),
  ], 'login')
  assert.deepEqual(sections.standalone.map((s) => s.path), ['/s/a.jsonl'])
  assert.deepEqual(sections.projects, [])
})
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm test tests/sessionGroups.test.ts`
Expected: FAIL — `sessionSections` is not exported.

- [ ] **Step 3: Implement `sessionSections` and remove the scope**

Implement the split above (pinned sorted newest first; standalone and groups sorted newest first; groups by first-session `updated_at`). Delete `sessionGroups` and `SessionScope`; remove `scope/setScope` and `zen.sidebar.scope` from `stores.ts`.

- [ ] **Step 4: Rewrite the sidebar render**

Structure: header (brand + collapse; on darwin the brand is hidden to clear the traffic lights), pinned section (`data-sidebar-section="pinned"`, max-height `min(233px, 30vh)`), sessions section with `会话` label + import/export toolbar, project groups with collapsible headers (`aria-expanded`, chevron, folder, name, active dot), 10 rows then `加载更多`, and a footer slot. Every mount calls `refreshSessions('all')`. Project clicks toggle collapse only; cross-project rows keep `aria-disabled` and the existing fail-closed guards.

- [ ] **Step 5: Add collapse persistence**

Add `collapsedProjects`/`toggleProject` to the session store (localStorage `zen.sidebar.projects`). Collapsed state must survive query changes and re-list; collapsing a project hides its rows via `inert` and `aria-hidden`.

- [ ] **Step 6: Add e2e coverage**

```ts
test('the sidebar renders pinned, sessions and collapsible project groups', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-sidebar-section="sessions"]')).toBeVisible()
  await expect(page.locator('[data-project-group="p"]')).toBeVisible()
  await page.getByRole('button', { name: '项目 p' }).click()
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toBeHidden()
})
test('a cross-project row is disabled even when no session is current', async ({ page }) => {
  await boot(page, initialState({ noCurrent: true }))
  await expect(page.locator('[data-session-path="/sessions/c.jsonl"]')).toHaveAttribute('aria-disabled', 'true')
})
```

- [ ] **Step 7: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi sidebar sections and collapsible projects`.

---

### Task 3: Topbar parity

**Files:**
- Modify: `desktop/src/ui/ConversationTopbar.tsx`, `desktop/src/ui/ConversationTopbar.module.css`
- Modify: `desktop/src/ui/Sidebar.tsx` (move undo/compact into the row menu)
- Modify: `desktop/src/i18n/locale.ts`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `useAppStore.currentSession`-derived title/project, `renameSession`, `runCommand`, `usePaletteStore`.
- Produces: 46px topbar with `[data-topbar-project]` (folder + name), a `/` separator, `[data-topbar-title]`, and right actions `new-session` + `search`; no inspector button, no undo/compact menu.

- [ ] **Step 1: Update the failing e2e**

Change the menu test to assert undo/compact now live in the **current row menu**:
```ts
await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
await page.getByRole('menuitem', { name: '撤销上一轮' }).click()
```
Add: `await expect(page.locator('[data-topbar-project] svg')).toBeVisible()` and `await expect(page.locator('[data-topbar-sep]')).toHaveText('/')`.

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm e2e --grep "topbar|row menu"`
Expected: FAIL — the topbar still owns the menu and has no separator.

- [ ] **Step 3: Rebuild the topbar**

46px row; left: collapsed-sidebar lead slot (28px, darwin inset 88px), folder icon + project, `/`, inline-rename title; right: new-session, search. Keep rename Enter/Escape behavior and silent success.

- [ ] **Step 4: Move undo/compact into the row menu**

Add `撤销上一轮` and `压缩上下文` menu items for the current session only (same success/failure handling as Stage 1). Remove `[data-topbar-menu]`.

- [ ] **Step 5: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi conversation topbar`.

---

### Task 4: Home and auto-connect

**Files:**
- Create: `desktop/src/ui/HomeHero.tsx`, `desktop/src/ui/HomeHero.module.css`
- Modify: `desktop/src/ui/WelcomeScreen.tsx` (become the idle host of `HomeHero`)
- Modify: `desktop/src/ui/chat/ChatView.tsx` (empty state uses `HomeHero`)
- Modify: `desktop/src/App.tsx`, `desktop/src/state/appStore.ts`
- Modify: `desktop/src/i18n/locale.ts`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `connect(cwd)`, `disconnect()`, recent projects (`zen.recent.projects`), current session project.
- Produces:
```ts
interface HomeHeroProps {
  connected: boolean
  projectName: string | null
  recents: { path: string; at: number }[]
  onPickFolder(): void
  onUseCurrent(): void
  onOpenRecent(path: string): void
}
```
- Produces: `zen.last.cwd` written on every successful `rpc_start`; auto-connect on mount when present.

- [ ] **Step 1: Write the failing e2e**

```ts
test('a returning user lands on the home hero and auto-connects', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work'))
  await page.addInitScript(installBridge, initialState())
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('ZenAgent')
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
})
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm e2e --grep "returning user"`
Expected: FAIL — the app still gates on the welcome screen.

- [ ] **Step 3: Build `HomeHero`**

100px mark, 28px normal-weight headline with an inline project button, 3 onboarding tips (⌘K 搜索 / `/` 命令 / ⌘B 侧栏) dismissible via `zen.home.tips`, and the composer slot rendered by the host. Width `min(100%, 760px)`, padding `28px 12px 24px`, gap 16px.

- [ ] **Step 4: Wire the idle and empty states**

`App.tsx`: when `connection === 'idle'`, if `zen.last.cwd` exists call `connect(undefined, cwd)` and show `BootScreen`; otherwise show `WelcomeScreen` (HomeHero idle). On `connection === 'ready'`, persist `zen.last.cwd = cwd ?? ''`. `ChatView` uses `HomeHero connected` when the transcript is empty. The project button opens a menu: current project, recents, and `打开其他项目…`; choosing another path asks for confirmation, then `disconnect()` + `connect(undefined, path)`.

- [ ] **Step 5: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi home and project switching`.

---

### Task 5: Turn grouping and the folded process

**Files:**
- Create: `desktop/src/state/turns.ts`, `desktop/src/ui/chat/TurnProcess.tsx`, `desktop/src/ui/chat/TurnProcess.module.css`
- Modify: `desktop/src/ui/chat/ChatView.tsx`
- Test: `desktop/tests/turns.test.ts`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `ChatNode`, `busy`.
- Produces:
```ts
export interface ChatTurn {
  id: string
  user: UserNode | null
  nodes: Exclude<ChatNode, UserNode>[]
  tools: ToolNode[]
  steps: number
  durationMs: number | null
  running: boolean
}
export function groupTurns(nodes: ChatNode[], busy: boolean): ChatTurn[]
```
- Produces `TurnProcess` props `{ tools: ToolNode[]; durationMs: number | null; running: boolean }`.

- [ ] **Step 1: Write the failing grouping tests**

```ts
test('user rows start turns and every node renders exactly once', () => {
  const turns = groupTurns([user('u1'), assistant('a1'), tool('t1'), tool('t2'), user('u2'), assistant('a2')], false)
  assert.equal(turns.length, 2)
  assert.equal(turns[0].steps, 2)
  assert.deepEqual(turns[0].nodes.map((n) => n.id), ['a1', 't1', 't2'])
  assert.equal(turns[1].steps, 0)
})

test('leading non-user nodes form their own turn and interrupted tools still count', () => {
  const turns = groupTurns([assistant('a1'), tool('t1', 'interrupted')], false)
  assert.equal(turns[0].user, null)
  assert.equal(turns[0].steps, 1)
})

test('the live turn is marked running while busy', () => {
  assert.equal(groupTurns([user('u1'), assistant('a1')], true)[0].running, true)
})
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm test tests/turns.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `groupTurns`**

A user node closes the previous turn and opens a new one. Duration = sum of tool `durationMs` when positive, else the turn's usage `turnMs`. `running` = any tool `running`, or an assistant node still streaming, or (`busy` and this is the last turn).

- [ ] **Step 4: Render folds**

`TurnProcess` header: `已处理 {duration} · {n} 个步骤` (running: `正在处理 · {n} 个步骤`), chevron, `aria-expanded`; body renders `ToolRowView` rows; completed folds default collapsed, running folds default expanded. In `ChatView`, replace each run of consecutive tool nodes with one `TurnProcess`.

- [ ] **Step 5: Add e2e**

```ts
await expect(page.getByRole('button', { name: /已处理.*步骤/ })).toBeVisible()
await page.getByRole('button', { name: /已处理.*步骤/ }).click()
await expect(page.getByText('read_file')).toBeVisible()
```

- [ ] **Step 6: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): fold turns into pi process rows`.

---

### Task 6: Transcript anatomy, minimap and width handles

**Files:**
- Modify: `desktop/src/ui/chat/ChatView.module.css`, `MessageItem.module.css`, `AssistantNodeView.module.css`, `ToolRowView.module.css`, `AuxRows.module.css`
- Create: `desktop/src/ui/chat/ConversationMinimap.tsx`, `ConversationMinimap.module.css`
- Create: `desktop/src/ui/chat/ChatWidthHandles.tsx`, `ChatWidthHandles.module.css`
- Modify: `desktop/src/App.tsx` (apply persisted width), `desktop/src/state/stores.ts`
- Test: `desktop/tests/columns.test.ts` (width clamp helper), `desktop/e2e/app.spec.ts`

**Interfaces:**
- Produces: `clampChatWidth(px): number` in `columns.ts` (560–900, default 760); `ConversationMinimap` props `{ nodes: ChatNode[]; onJump(id: string): void; activeId: string | null }`; `ChatWidthHandles` props `{ width: number; onChange(width: number): void }`.
- Produces settings field `chatWidth`, persisted under `zen.chat.width`.

- [ ] **Step 1: Write the failing clamp test**

```ts
assert.equal(clampChatWidth(500), 560)
assert.equal(clampChatWidth(760), 760)
assert.equal(clampChatWidth(1200), 900)
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm test tests/columns.test.ts`
Expected: FAIL — `clampChatWidth` missing.

- [ ] **Step 3: Apply pi's transcript values**

Flow `max-width: var(--ds-chat-content-width)`, padding `20px 32px` with top padding 46px; rows padding `12px 0`, gap 12px; user bubble radius 18px / bottom-right 8px, padding `10px 15px`, 14px/1.55, `primary 8%` fill; assistant 14px/1.6 with the meta line after the body; thinking 12.5px; tool header 24px; compaction divider 11px with flanks; scroll mask above the composer reserve.

- [ ] **Step 4: Add minimap and width handles**

Minimap rail: `left:8px; width:26px; top:46px`, one 2px dash per user/assistant node (user 13px, assistant 8px), active dash full ink, click jumps. Width handles: two 12px hit lanes around the band; drag calls `onChange(clampChatWidth(...))`; persist on pointer-up.

- [ ] **Step 5: e2e for handles and minimap**

```ts
await page.mouse.move(bandLeft, 400); await page.mouse.down(); await page.mouse.move(bandLeft - 80, 400); await page.mouse.up()
const width = await page.locator('[data-chat-flow]').evaluate((el) => el.getBoundingClientRect().width)
assert.ok(width < 760)
await expect(page.locator('[data-minimap-dash]').first()).toBeVisible()
```

- [ ] **Step 6: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi transcript anatomy and width controls`.

---

### Task 7: Composer plate and dock

**Files:**
- Modify: `desktop/src/ui/composer/InputBar.tsx`, `desktop/src/ui/composer/InputBar.module.css`, `desktop/src/ui/composer/Docks.module.css`
- Modify: `desktop/src/App.tsx`, `desktop/src/App.module.css`
- Modify: `desktop/src/ui/approval/ApprovalPanel.tsx`, `ApprovalPanel.module.css`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `InputBar`, `ApprovalPanel`, `useStatusStore`.
- Produces: composer dock that measures its own height into `--composer-dock-height` on the main pane; approval renders **above** the plate instead of replacing it.

- [ ] **Step 1: Write the failing dock e2e**

```ts
test('the composer never covers the last message on a short window', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState())
  const flow = await page.locator('[data-chat-flow]').boundingBox()
  const dock = await page.locator('[data-shell-composer]').boundingBox()
  assert.ok(flow!.y + flow!.height <= dock!.y + 1)
})
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm e2e --grep "never covers"`
Expected: FAIL — the transcript does not reserve the dock height.

- [ ] **Step 3: Measure the dock**

A `ResizeObserver` on the dock writes `--composer-dock-height` to the main pane; `ChatView` scroll padding bottom becomes `calc(var(--composer-dock-height, 120px) + 16px)`; remove the old fixed tail pad.

- [ ] **Step 4: Apply pi's plate values**

Plate `min(100%, 768px)`, radius 20, composer shadow, input padding `10px 12px 0`, min 3lh / max 7lh, toolbar `4px 8px 8px`, chips 20px, send 28px always mounted with 18% opacity when disabled, mode chip fixed 96px. Queued rows: radius 12, padding `7px 8px 7px 12px`, accent priority inset.

- [ ] **Step 5: Move approval above the plate**

Render `<ApprovalPanel />` before the input inside the dock (grid column, width 100%) and keep `InputBar` mounted underneath. Approval buttons stay 30px high.

- [ ] **Step 6: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi composer plate and dock`.

---

### Task 8: Work panel chrome

**Files:**
- Modify: `desktop/src/ui/rightbar/RightPanel.tsx`, `RightPanel.module.css`
- Modify: `desktop/src/App.tsx`, `desktop/src/AppFrame.tsx`, `AppFrame.module.css`
- Modify: `desktop/src/ui/columns.ts`
- Test: `desktop/e2e/app.spec.ts`

**Interfaces:**
- Consumes: `status`, `sessions`, current path.
- Produces: 46px header with tabs `检查器` / `上下文`; resize handle; panel min 244 / default 360; center floor 450 (Task 1).

- [ ] **Step 1: Write the failing e2e**

```ts
await page.getByRole('button', { name: '打开检查器' }).click()
await expect(page.getByRole('tab', { name: '上下文' })).toBeVisible()
await page.getByRole('tab', { name: '上下文' }).click()
await expect(page.getByText('上下文')).toBeVisible()
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm e2e --grep "inspector"`
Expected: FAIL — no tabs.

- [ ] **Step 3: Implement the panel chrome**

Header 46px with tabs (28px high), close/collapse control, and the existing sections split: `检查器` = todos/jobs/subagents/errors; `上下文` = usage/context/tokens. Resize handle at `left:-5px`, width 10px, pointer + keyboard arrows; double-click resets to 360.

- [ ] **Step 4: Gate and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi work panel chrome`.

---

### Task 9: Footer surfaces — Settings, Tools, Notifications, version

**Files:**
- Create: `desktop/src/ui/pages/SettingsPage.tsx`, `SettingsPage.module.css`, `ToolsPage.tsx`, `ToolsPage.module.css`
- Create: `desktop/src/ui/Notifications.tsx`, `Notifications.module.css`
- Modify: `desktop/src/state/stores.ts`, `desktop/src/state/appStore.ts`
- Modify: `desktop/src/ui/Sidebar.tsx`, `Sidebar.module.css`
- Modify: `desktop/src/App.tsx`, `desktop/src/i18n/locale.ts`, `desktop/vite.config.ts`
- Test: `desktop/tests/stores.test.ts` (new), `desktop/e2e/app.spec.ts`

**Interfaces:**
- Produces settings: `fontScale` (0.9–1.2), `chatWidth`, `sessionTime`, with setters persisted (`zen.fontScale`, `zen.chat.width`, `zen.sidebar.sessionTime`).
- Produces notifications: `items: { id; tone: 'error' | 'info'; text; at }[]`, `unread`, `markRead()`, `clear()`; `useNoticeStore.show` appends an item.
- Produces view state `'chat' | 'settings' | 'tools'` in the settings store; Settings/Tools replace the shell like pi.
- Produces `__APP_VERSION__` defined from `package.json` in `vite.config.ts`.

- [ ] **Step 1: Write the failing store tests**

```ts
test('settings clamp the font scale and chat width', () => {
  assert.equal(clampFontScale(0.5), 0.9)
  assert.equal(clampFontScale(1.05), 1.05)
  assert.equal(clampFontScale(2), 1.2)
})
test('showing a notice records a notification', () => {
  useNoticeStore.setState({ notice: null })
  useNoticeStore.getState().show({ kind: 'error', text: 'boom' })
  assert.equal(useNotificationStore.getState().items[0].text, 'boom')
  assert.equal(useNotificationStore.getState().unread, 1)
})
```

- [ ] **Step 2: Run to watch it fail**

Run: `cd desktop && pnpm test tests/stores.test.ts`
Expected: FAIL — module/store missing.

- [ ] **Step 3: Implement stores and apply settings**

Extend `useSettingsStore`; in `App.tsx` apply `--ds-font-scale` and `--ds-chat-content-width` to `document.documentElement`. Push every `useNoticeStore.show` into notifications.

- [ ] **Step 4: Build Settings and Tools**

Settings page: theme (auto/light/dark), language (zh/en), session time toggle, font-scale slider, content-width slider, version. Tools page: fetch `{type:'tools'}` on open, render name, description, approval badge; error state with retry. Both restore the previous chat scroll/focus on return.

- [ ] **Step 5: Rebuild the sidebar footer**

Icon row: Settings, Tools, Notifications (bell + unread dot), then the version chip (`v{__APP_VERSION__}`). Remove the theme/import/export buttons; move import/export into the `会话` section toolbar. Move undo/compact already in the row menu (Task 3).

- [ ] **Step 6: e2e for footer surfaces**

```ts
await page.getByRole('button', { name: '设置' }).click()
await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()
await page.getByRole('button', { name: '返回' }).click()
await expect(page.locator('[data-shell-composer]')).toBeVisible()
await page.getByRole('button', { name: '工具' }).click()
await expect(page.getByText('read_file')).toBeVisible()
```

- [ ] **Step 7: Gates and commit**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`. Commit `feat(desktop): pi footer surfaces`.

---

### Task 10: Whole-surface verification

**Files:**
- Test: `desktop/e2e/app.spec.ts`
- No production files unless a gate fails.

- [ ] **Step 1: Run the full suite**

Run: `cd desktop && pnpm test && pnpm build && pnpm e2e`
Expected: all green; capture the counts.

- [ ] **Step 2: Capture screenshots at 1600×1000 (light and dark) and 1280×720**

Use the mocked bridge with a multi-turn transcript; verify against the pi reference for: full-height sidebar, 46px chrome, 760 band, composer inside the main pane, folded process rows, footer icon row, work-panel tabs.

- [ ] **Step 3: Fix visual regressions found in the screenshots**

Each fix gets a failing assertion (e2e or unit) before the change, then a green suite.

- [ ] **Step 4: Final review and commit**

Run the executing-plans whole-branch review; fix Critical/Important in one TDD pass; list deferred minors in the final message. Commit `fix(desktop): stage 2 parity review pass`.
