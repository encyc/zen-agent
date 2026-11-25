# 桌面端动效与滚动三条线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用零新增依赖把 shadcn/ui 里值得抄的三个机制落到 `desktop/`：状态驱动的进入/退出动画、统一的动效 token 阶梯、对话滚动的贴底跟随状态机。

**Architecture:** 三条线互不共享文件，只有 C 线的 token 消费与 B 线相邻（C 的新样式全部用 B 产出的 token，所以 B 先落地）。逻辑放在 `src/state/` 与 `src/ui/presence.ts`（纯函数，`node --test` 可直测），组件只做接线；视觉全部由 `data-state` 属性驱动 CSS transition，不引入动画库。

**Tech Stack:** React 18 + TypeScript 5.9 + Vite 6 + CSS Modules + zustand；测试 `node --test`（Node 25 直跑 `.ts`）+ Playwright。

**Spec:** [docs/superpowers/specs/2026-11-25-desktop-motion-design.md](../specs/2026-11-25-desktop-motion-design.md)

## Global Constraints

- **不新增任何 `dependencies` / `devDependencies`**（`desktop/package.json` 的这两个块在整条分支里不得出现 diff）。
- 组件样式表（`desktop/src/**/*.module.css`）**不得出现颜色字面量**（`scripts/check-style-tokens.mjs`，BASELINE 必须保持空数组）。
- 组件样式表的 `transition` / `animation` 声明**不得出现裸时长或裸缓动**（本计划新增的 `scripts/check-style-motion.mjs`）。
- 单文件 ≤ 800 行（`scripts/check_architecture.py`），`desktop/src` 在扫描范围；`ChatView.tsx` 起始 497 行，本计划结束时必须 **≤ 497**。
- 新增文案必须**同时**进 `src/i18n/messages.ts` 的 `zh` 与 `en`（`tests/locale.test.ts` 钉 key 集合、占位符集合与非空）。
- 新增 `t('key')` 字面量必须存在于目录（`tests/i18nKeys.test.ts` 钉）。
- 不写按文件的 `prefers-reduced-motion` 覆盖：`src/styles/base.css:89` 已有全局兜底。**唯一例外**是循环动画已有的 `animation: none` 段（既有代码，不动）。
- 每个任务结束的绿灯命令（cwd `desktop/`）：`pnpm lint:tokens && pnpm test && pnpm build`。

## Review Focus

1. **退场期间的可交互性**：审批卡、命令面板、通知 popover 在退出动画期间仍挂载——点一下会不会发出两次 RPC、或让遮罩吃掉本该落到页面的点击。期望：退场期间这些容器不响应指针，键盘路径由已有 `answered` ref 幂等守卫。
2. **`prefers-reduced-motion: reduce` 下的行为**：全局兜底把时长压到 `0.01ms`，此时退出必须立即卸载（不能挂着 120ms 的不可见节点），且动画类样式不能留下 `opacity: 0` 的僵尸节点。
3. **程序化滚动与用户滚动的区分**：点击浮标、点 minimap、窗口增长锚定都会改 `scrollTop` 并触发 `scroll` 事件——这些不得把 `mode` 从 `following` 打成 `free`，否则浮标闪一下。
4. **`--ds-motion*` 的作用域**：这些 token 定义在 `body`，旧名 `--ds-transition-duration*` 定义在 `:root`；别名放错层会让 `var()` 解析为空、整条 `transition` 声明失效（静默，无报错）。
5. **窗口化 + 未见计数的边界**：`unseen` 只能数**用户消息**，且窗口化会在用户回到底部时裁掉旧行——计数必须基于节点 id 而非索引，否则裁窗口后计数错乱。

---

### Task 1: presence 状态机

**Files:**
- Create: `desktop/src/ui/presence.ts`
- Test: `desktop/tests/presence.test.ts`

**Interfaces:**
- Produces:
  - `type PresencePhase = 'closed' | 'entering' | 'open' | 'exiting'`
  - `function nextPhase(phase: PresencePhase, wantOpen: boolean): PresencePhase`
  - `function isMounted(phase: PresencePhase): boolean`
  - `function presenceState(phase: PresencePhase): 'open' | 'closed'`
  - `function usePresence(open: boolean, leavingMs: number): { mounted: boolean; state: 'open' | 'closed' }`
  - `const PRESENCE_LEAVE_MS = 120`（浮层默认退场时长）

- [ ] **Step 1: 写失败测试**

`desktop/tests/presence.test.ts`（断言逐条照抄；导入 `../src/ui/presence.ts` 的纯函数，**不导入 hook**）：

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isMounted, nextPhase, presenceState } from '../src/ui/presence.ts'

test('opening mounts on the first commit and settles a phase later', () => {
  assert.equal(nextPhase('closed', true), 'entering')
  assert.equal(nextPhase('entering', true), 'open')
})

test('closing keeps the node mounted through one exit phase', () => {
  assert.equal(nextPhase('open', false), 'exiting')
  assert.equal(presenceState('exiting'), 'closed')  // CSS 目标态
  assert.equal(isMounted('exiting'), true)          // 但仍在 DOM
})

test('only the timer closes, never a boolean flip', () => {
  // closed 是 hook 超时后直接 set 的结果；nextPhase 的任何输入都不得到达 closed
  for (const phase of ['closed', 'entering', 'open', 'exiting'] as const) {
    for (const wantOpen of [true, false]) {
      assert.notEqual(nextPhase(phase, wantOpen), 'closed')
    }
  }
})

test('an exit interrupted by a reopen enters again instead of jumping open', () => {
  assert.equal(nextPhase('exiting', true), 'entering')
  assert.equal(nextPhase('entering', false), 'exiting')  // 进入中被打断也能反向
})

test('mounted and the data-state attribute disagree exactly in the exiting phase', () => {
  for (const phase of ['entering', 'open'] as const) {
    assert.equal(isMounted(phase), true)
    assert.equal(presenceState(phase), 'open')
  }
  assert.equal(isMounted('closed'), false)
  assert.equal(presenceState('closed'), 'closed')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/presence.test.ts`
Expected: FAIL —— `Cannot find module '../src/ui/presence.ts'`

- [ ] **Step 3: 实现 `desktop/src/ui/presence.ts`**

文件头注释写清「为什么是定时器而不是 `animationend`」（见 spec 决定 1）。`nextPhase` 是一张纯映射：

| phase | wantOpen | → |
|---|---|---|
| `closed` | true | `entering` |
| `closed` | false | `closed` |
| `entering` | true | `open` |
| `entering` | false | `exiting` |
| `open` | true | `open` |
| `open` | false | `exiting` |
| `exiting` | true | `entering` |
| `exiting` | false | `exiting` |

`usePresence(open, leavingMs)`：`useState<PresencePhase>('closed')`；`useEffect` 在每次 `[open, leavingMs]` 变化时 `setPhase((p) => nextPhase(p, open))`，并在 `phase === 'exiting'` 时 `window.setTimeout(() => setPhase('closed'), leavingMs)`、清理时 `clearTimeout`；`phase === 'entering'` 时用 `requestAnimationFrame` 落到 `'open'`。返回 `{ mounted: isMounted(phase), state: presenceState(phase) }`。

- [ ] **Step 4: 跑测试确认通过**

Run: `node --test tests/presence.test.ts`
Expected: PASS，5 个 test。

- [ ] **Step 5: 提交**

```bash
git add desktop/src/ui/presence.ts desktop/tests/presence.test.ts
git commit -m "feat(desktop): a presence state machine for enter and exit phases"
```

---

### Task 2: 浮层接入（命令面板、通知 popover）

**Files:**
- Modify: `desktop/src/ui/palette/CommandPalette.tsx`
- Modify: `desktop/src/ui/palette/CommandPalette.module.css`
- Modify: `desktop/src/ui/Notifications.tsx`
- Modify: `desktop/src/ui/Notifications.module.css`
- Test: `desktop/e2e/motion.spec.ts`（新建）

**Interfaces:**
- Consumes: `usePresence`、`PRESENCE_LEAVE_MS`（Task 1）
- Produces: e2e 契约 —— `[data-palette]` 与 `[data-notifications-popover]` 两个测试锚点

- [ ] **Step 1: 写失败测试**

`desktop/e2e/motion.spec.ts`：

```ts
/** Exit phases are observable as DOM lifetime: the node survives its close
 * click, then leaves after the leaving window. No computed-style assertions —
 * a transition's getComputedStyle changes every frame. */

import { expect, test } from '@playwright/test'
import { boot, emitEvent, initialState, paletteKey } from './harness.ts'
// `emitEvent` 在 Task 3 追加的用例里用到；Task 2 阶段先按需删掉未使用的导入，否则 tsc 的 noUnusedLocals 会响

test('the command palette survives its close and stops taking clicks while leaving', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const palette = page.locator('[data-palette]')
  await expect(palette).toHaveAttribute('data-state', 'open')

  await page.keyboard.press('Escape')
  await expect(palette).toHaveAttribute('data-state', 'closed')
  await expect(palette).toHaveCount(1)          // 退场中仍在 DOM

  // 退场期间遮罩不吃点击：底下的 textarea 仍应拿到焦点
  await page.locator('[data-composer-plate] textarea').click()
  await expect(page.locator('[data-composer-plate] textarea')).toBeFocused()

  await expect(palette).toHaveCount(0, { timeout: 1000 })  // 一次 leaving 窗口后卸载
})

test('the notifications popover keeps its node through the exit phase', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: 'Notifications' }).click()
  const popover = page.locator('[data-notifications-popover]')
  await expect(popover).toHaveAttribute('data-state', 'open')

  await page.getByRole('button', { name: 'Notifications' }).click()
  await expect(popover).toHaveAttribute('data-state', 'closed')
  await expect(popover).toHaveCount(1)
  await expect(popover).toHaveCount(0, { timeout: 1000 })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm exec playwright test e2e/motion.spec.ts`
Expected: FAIL —— `data-state` 属性不存在（`toHaveAttribute` 收到 `null`）

- [ ] **Step 3: 接线 CommandPalette**

`CommandPalette.tsx`：`if (!open) return null` 改成分层返回 —— 先 `const presence = usePresence(open, PRESENCE_LEAVE_MS)`，`if (!presence.mounted) return null`。遮罩 `<div className={css.backdrop} data-palette data-state={presence.state} onPointerDown={...}>`；`onPointerDown` 关闭分支加上 `if (presence.state !== 'open') return`。`close()` 调用的 store 动作不变（`open:false` 驱动 hook）。

`CommandPalette.module.css`：`.backdrop` 增加 `opacity` + `transition: opacity var(--ds-motion-fast) var(--ds-ease-out)`，`.backdrop[data-state='closed']` 设 `opacity: 0; pointer-events: none`；`.panel` 增加 `transform` + 同样 duration 的 `transform` transition，`[data-state='closed'] .panel` 设 `opacity: 0; transform: translateY(-4px)`。**面板自身的 `opacity` 也要写过渡**，否则只有位移没有淡出。

- [ ] **Step 4: 接线 Notifications popover**

`Notifications.tsx`：`const presence = usePresence(open, PRESENCE_LEAVE_MS)`；`{open && (<div className={css.popover} role="dialog" …>)}` 改为 `{presence.mounted && (<div className={css.popover} data-notifications-popover data-state={presence.state} role="dialog" aria-hidden={presence.state === 'closed' || undefined} …>)}`。已有的「外部 pointerdown 关闭」监听维持按 `open` 安装——退场期间不需要再监听。

`Notifications.module.css`：`.popover` 加 `opacity` + `transform: translateY(0)` 与 `transition: opacity … , transform …`；`.popover[data-state='closed']` 设 `opacity: 0; transform: translateY(4px); pointer-events: none`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pnpm exec playwright test e2e/motion.spec.ts`
Expected: PASS，2 个 test。再跑 `pnpm exec playwright test e2e/settings.spec.ts e2e/home.spec.ts`，确认未回归（这两处按 `getByRole('dialog')` / `[data-update-notice]` 定位，退场中仍在 DOM 的节点不应被它们命中——若 `home.spec.ts:149` 的 `toHaveCount(0)` 变成超时，说明退场窗口太长，把该处的定位改为等价断言而非缩短动画）。

- [ ] **Step 6: 提交**

```bash
git add desktop/src/ui/palette desktop/src/ui/Notifications.tsx desktop/src/ui/Notifications.module.css desktop/e2e/motion.spec.ts
git commit -m "feat(desktop): exit phases for the command palette and the notification popover"
```

---

### Task 3: 内联接入（通知横幅、审批卡、上下文卡、重试详情）

**Files:**
- Modify: `desktop/src/ui/NoticeBanner.tsx`、`desktop/src/ui/NoticeBanner.module.css`
- Modify: `desktop/src/ui/approval/ApprovalPanel.tsx`、`desktop/src/ui/approval/ApprovalPanel.module.css`
- Modify: `desktop/src/ui/composer/Docks.tsx`、`desktop/src/ui/composer/Docks.module.css`
- Modify: `desktop/src/ui/chat/RetryRow.tsx`、`desktop/src/ui/chat/RetryRow.module.css`
- Test: `desktop/e2e/motion.spec.ts`（追加）

**Interfaces:**
- Consumes: Task 1 的 `usePresence` / `PRESENCE_LEAVE_MS`
- Produces: 审批卡的 e2e 幂等契约（退场期间点击不产生第二次 approval 调用）

- [ ] **Step 1: 写失败测试**

`desktop/e2e/motion.spec.ts` 追加。审批卡用例直接扩写 `e2e/composer.spec.ts:114` 那个场景的前半段（在 `motion.spec.ts` 里重述最小 fixture，不跨 spec 导入）：

```ts
test('the approval card leaves without accepting a second answer', async ({ page }) => {
  await boot(page, initialState())
  // 审批不是 BridgeState 字段，它是一帧 approval_request（照抄 e2e/composer.spec.ts:96-110）。
  await emitEvent(page, {
    type: 'approval_request',
    request_id: 'req-1',
    tool_name: 'bash',
    args: '{"command":"git push --force"}',
    risk: 'high',
    target: 'bash',
    reason: '这条命令会改写远端历史',
  })
  const panel = page.locator('[data-approval]')
  await expect(panel).toHaveAttribute('data-state', 'open')

  await page.getByRole('button', { name: '拒绝' }).click()
  await expect(panel).toHaveAttribute('data-state', 'closed')
  await expect(panel).toHaveCount(1)

  // 退场期间按钮必须已 disabled：<button disabled> 不派发 click，这个保证在浏览器层。
  const allow = panel.getByRole('button', { name: '允许一次' })
  await expect(allow).toBeDisabled()
  await allow.click({ force: true })
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          window.__ZEN_E2E__.calls.filter(
            (call) => call.type === 'approval' && call.request_id === 'req-1',
          ).length,
      ),
    )
    .toBe(1)

  await expect(panel).toHaveCount(0, { timeout: 1000 })
})

test('the notice banner animates out instead of vanishing', async ({ page }) => {
  await boot(page, initialState({ failExport: true }))
  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 Markdown' }).click()
  const banner = page.locator('[data-shell-composer] [role="alert"]')
  await expect(banner).toHaveAttribute('data-state', 'open')
  await banner.getByRole('button').click()
  await expect(banner).toHaveAttribute('data-state', 'closed')
  await expect(banner).toHaveCount(1)
  await expect(banner).toHaveCount(0, { timeout: 1000 })
})

test('the retry details expand and collapse through a state attribute', async ({ page }) => {
  await boot(page, initialState({
    transcript: [
      { kind: 'user', id: 'u1', text: '再试一次', timestamp: 1 },
      { kind: 'assistant', id: 'v1', text: '好。', timestamp: 2 },
    ],
  }))
  // …emit a retry_attempt like e2e/retry.spec.ts does…
  const details = page.locator('[data-retry-details]')
  await expect(details).toHaveAttribute('data-state', 'closed')
  await page.locator('[data-retry-toggle]').click()
  await expect(details).toHaveAttribute('data-state', 'open')
  await page.locator('[data-retry-toggle]').click()
  await expect(details).toHaveAttribute('data-state', 'closed')
  await expect(details).toHaveCount(1)   // 折叠后仍挂载（尺寸过渡）
})
```
（`retry_attempt` 的发射方式照抄 `e2e/retry.spec.ts:26-35`；`approval_request` 照抄 `e2e/composer.spec.ts:96-110`，两者都用 `emitEvent`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm exec playwright test e2e/motion.spec.ts`
Expected: FAIL —— `data-state` 不存在、`[data-retry-details]` 不存在

- [ ] **Step 3: 接线 NoticeBanner**

`NoticeBanner.tsx`：`const notice = useNoticeStore(...)`；`if (notice === null) return null` 之前先 `const presence = usePresence(notice !== null, PRESENCE_LEAVE_MS)`，改为 `if (!presence.mounted) return null`。**退场时 `notice` 已是 `null`**，所以要把最后一次非空 notice 记进 ref（`lastNotice`），渲染用 `lastNotice.current`，文字不会在退场瞬间被清空。根节点加 `data-state={presence.state}`；`role="alert"` 保留。样式：`opacity`/`transform: translateY(-2px)` + transition，`[data-state='closed']` → `opacity: 0; transform: translateY(-2px); pointer-events: none`。

- [ ] **Step 4: 接线 ApprovalPanel**

`ApprovalPanel.tsx`：同样的 `lastApproval` ref 手法（队列移除后仍要渲染退场帧）。`if (approval === undefined) return null` → `if (!presence.mounted) return null`，渲染取 `lastApproval.current`。根节点 `data-approval data-state={presence.state}`，`[data-state='closed']` 时容器 `pointer-events: none`。`answer()` 的 `answered.current` 守卫保持原样——它是幂等的真源，退场样式只是第二道。三个按钮的 `disabled={busy}` 也不动：`answer()` 已经把 `busy` 置真，退场期间按钮天然是 disabled 的，e2e 的 `toBeDisabled()` 与 `click({ force: true })` 不产生第二次调用正是靠这条（在浏览器层被拦，不是靠 React 拦）。

- [ ] **Step 5: 接线 ContextMeter 卡片与 RetryRow 详情**

`Docks.tsx`：`{open && report !== null && (<div className={css.contextCard} …>)}` 改为 `const card = usePresence(open && report !== null, PRESENCE_LEAVE_MS)`；`card.mounted` 时渲染，根节点加 `data-state={card.state}`。`Docks.module.css` 的 `contextCardIn` keyframes **删除**，换成 `.contextCard` 上的 `opacity`/`transform` transition 与 `[data-state='closed']` 目标态；`@media (prefers-reduced-motion)` 里对 `.contextCard` 的 `animation: none` 一并删除（不再有 animation）。

`RetryRow.tsx`：`{open && (<div className={css.details}>…)}` 改为 `usePresence(open, PRESENCE_LEAVE_MS)` + `data-retry-details data-state={presence.state}`。`RetryRow.module.css`：`.details` 用 `display: grid; grid-template-rows: 0fr;`、`.details > *` 用 `overflow: hidden`、`[data-state='open']` 设 `grid-template-rows: 1fr`，配 `transition: grid-template-rows var(--ds-motion) var(--ds-ease-out)`。

- [ ] **Step 6: 跑测试确认通过**

Run: `pnpm exec playwright test e2e/motion.spec.ts e2e/retry.spec.ts e2e/composer.spec.ts e2e/layout.spec.ts`
Expected: 全 PASS。`e2e/layout.spec.ts:305` 的横幅位置断言（banner 底边 ≤ plate 顶边）必须仍然成立——退场不改布局，若它失败说明用了 `height` 过渡而非 `transform`。

- [ ] **Step 7: 提交**

```bash
git add desktop/src/ui/NoticeBanner.* desktop/src/ui/approval desktop/src/ui/composer desktop/src/ui/chat/RetryRow.* desktop/e2e/motion.spec.ts
git commit -m "feat(desktop): exit phases for the banner, approval card, context card and retry details"
```

---

### Task 4: 动效 token 阶梯

**Files:**
- Modify: `desktop/src/styles/tokens.css:48-51`（`:root`）与 `:462-465` 所在 `body` 块
- Modify: 29 处裸写所在样式表（清单见 Step 4）
- Test: `desktop/tests/motionTokens.test.ts`（新建）

**Interfaces:**
- Produces: `--ds-motion-fast`(140ms) / `--ds-motion`(200ms) / `--ds-motion-slow`(320ms)、`--ds-ease-out`、`--ds-ease-spring`；旧名 `--ds-transition-duration{,-fast,-slow}` 与 `--ds-ease-in-out` 成为 `body` 层别名

- [ ] **Step 1: 写失败测试**

`desktop/tests/motionTokens.test.ts`：

```ts
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8')

/** 取出包含 `marker` 的那个 `selector { … }` 块的声明文本。
 *  tokens.css 里有 5 个 `body {` 块，靠选择器定位会拿到第一个（不含动效那几行）。 */
function blockWith(marker: string): string {
  const at = css.indexOf(marker)
  assert.ok(at >= 0, `tokens.css declares ${marker}`)
  const start = css.lastIndexOf('{', at)
  return css.slice(start, css.indexOf('}', at))
}

test('the motion ladder is one scale with three rungs', () => {
  const ladder = blockWith('--ds-motion-fast:')
  for (const [token, value] of [
    ['--ds-motion-fast:', '140ms'],
    ['--ds-motion:', '200ms'],
    ['--ds-motion-slow:', '320ms'],
  ] as const) {
    assert.match(ladder, new RegExp(`${token}\\s*${value};`), token)
  }
  assert.match(ladder, /--ds-ease-out:\s*cubic-bezier\(0\.22, 1, 0\.36, 1\);/)
})

test('the legacy names alias the ladder in the SAME block that defines it', () => {
  // var(--ds-motion-fast) 在 `:root` 上解析为空（token 定义在 body），
  // 整条 transition 声明会静默失效。别名必须与真值同层。
  const ladder = blockWith('--ds-motion-fast:')
  assert.match(ladder, /--ds-transition-duration-fast:\s*var\(--ds-motion-fast\);/)
  assert.match(ladder, /--ds-transition-duration:\s*var\(--ds-motion\);/)
  assert.match(ladder, /--ds-transition-duration-slow:\s*var\(--ds-motion-slow\);/)
  assert.match(ladder, /--ds-ease-in-out:\s*var\(--ds-ease-out\);/)
  // 旧名一个都不许留在 :root
  const root = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')))
  assert.doesNotMatch(root, /--ds-transition-duration|--ds-ease-in-out/)
})

test('every alias resolves to a token that exists', () => {
  for (const [, target] of css.matchAll(/--ds-transition-duration[\w-]*:\s*var\((--[\w-]+)\);/g)) {
    assert.match(css, new RegExp(`${target}:\\s*\\d`), `${target} is defined with a literal`)
  }
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/motionTokens.test.ts`
Expected: FAIL —— `--ds-motion-slow` 未定义；别名仍在 `:root`

- [ ] **Step 3: 改 `tokens.css`**

`:root` 块（48-51 行）**删除**四行 `--ds-ease-in-out` / `--ds-transition-duration{,-fast,-slow}`。`body { … --ds-motion-fast: 140ms; … }` 块（460 行附近）改为：

```css
  --ds-motion-fast: 140ms;
  --ds-motion: 200ms;
  --ds-motion-slow: 320ms;
  --ds-ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --ds-ease-spring: cubic-bezier(0.34, 1.3, 0.64, 1);

  /* The pre-ladder names, kept as aliases so 40+ call sites did not have to
   * move at once. They must live in THIS block: the tokens they point at are
   * defined here, and :root cannot see them. */
  --ds-transition-duration-fast: var(--ds-motion-fast);
  --ds-transition-duration: var(--ds-motion);
  --ds-transition-duration-slow: var(--ds-motion-slow);
  --ds-ease-in-out: var(--ds-ease-out);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `node --test tests/motionTokens.test.ts tests/tokens.test.ts`
Expected: PASS（含既有的 `tokens.test.ts` —— 它不 pin 这四行的位置，但会 pin 字体/半径梯子未被波及）

- [ ] **Step 5: 写动效守卫的失败测试**

`desktop/tests/styleMotion.test.ts`：

```ts
/** 动效守卫的匹配器：裸时长/裸缓动被报，token 与循环动画的缓动关键字放行。 */

import assert from 'node:assert/strict'
import { test } from 'node:test'
// @ts-expect-error -- plain-node guard script, no type declarations
import { scanMotionCss } from '../../scripts/check-style-motion.mjs'

test('a raw duration or a bare easing keyword is a violation', () => {
  const css = '.a { transition: opacity 120ms ease; }'
  assert.deepEqual(scanMotionCss(css, { file: 'desktop/src/ui/a.module.css' }).violations.map((v) => v.value), ['opacity 120ms ease'])
})

test('token-driven declarations pass', () => {
  const css = '.a { transition: opacity var(--ds-motion-fast) var(--ds-ease-out); }'
  assert.deepEqual(scanMotionCss(css, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

test('a looping animation may keep its easing keyword but not a raw duration', () => {
  const ok = '.a { animation: spin 1.1s linear infinite; }'
  const violations = scanMotionCss(ok, { file: 'desktop/src/ui/a.module.css' }).violations
  assert.deepEqual(violations.map((v) => v.property), ['animation'])
  const fine = '.a { animation: spin var(--ds-motion-slow) linear infinite; }'
  assert.deepEqual(scanMotionCss(fine, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

test('the palette stylesheets are exempt and a bare 0ms/0s is allowed', () => {
  assert.deepEqual(scanMotionCss('.a { transition: none; }', { file: 'desktop/src/styles/tokens.css' }).violations, [])
  assert.deepEqual(scanMotionCss('.a { transition: opacity 0s; }', { file: 'desktop/src/ui/a.module.css' }).violations, [])
})
```

Run: `node --test tests/styleMotion.test.ts`
Expected: FAIL —— `Cannot find module '../../scripts/check-style-motion.mjs'`

- [ ] **Step 6: 实现守卫脚本**

`scripts/check-style-motion.mjs`：结构照抄 `check-style-tokens.mjs`（同样的 `walk()`、`EXEMPT_FILES`、`BASELINE = []`、stale 检测、`invokedDirectly` 入口），约束：

- 扫描 `SCAN_ROOT = desktop/src`，只收 `*.module.css`；
- `EXEMPT_FILES` 用 `styles/tokens.css`、`styles/themes.css`、`styles/base.css`（palette 层允许字面时长）；
- 只解析 `transition` / `transition-*` / `animation` / `animation-duration` 声明的值；
- 违规 = 值里（剥掉 `var(...)` 之后）出现 `\d+(\.\d+)?m?s`（`0s`/`0ms` 放行）或裸 `ease`/`ease-in`/`ease-out`/`ease-in-out`/`cubic-bezier(`/`steps(`；
- **例外**：`animation` 声明允许缓动关键字（`linear`/`ease*`/`steps()`），因为循环动效的节奏靠词法表达；`cubic-bezier(` 在 `animation` 里也允许。时长仍然必须 token。
- 导出 `scanMotionCss(source, { file, baseline })`，返回 `{ violations: {line, property, value}[], skipped, stale }`，签名与 `scanCss` 一致。

- [ ] **Step 7: 跑守卫的单测与全仓扫描**

Run: `node --test tests/styleMotion.test.ts`
Expected: PASS，4 个 test

Run: `node ../scripts/check-style-motion.mjs`
Expected: FAIL，列出 29 处违规（逐条 file:line + 声明值）

- [ ] **Step 8: 把 29 处裸写换成 token**

按 spec 的归类逐处改（`--ds-motion-fast` 给 opacity/color/background/border-color/box-shadow/transform；`--ds-motion` 给 width/height/grid-template-*；缓动一律 `--ds-ease-out`）。文件清单与当前行号：

| 文件 | 行 |
|---|---|
| `src/ui/AppFrame.module.css` | 61 |
| `src/ui/Sidebar.module.css` | 108, 255, 288 |
| `src/ui/primitives.module.css` | 141（`120ms ease 400ms` → `var(--ds-motion-fast) var(--ds-ease-out) 400ms`）, 以及 `transform 90ms ease` 一处 |
| `src/ui/chat/ToolRowView.module.css` | 36 |
| `src/ui/chat/ChatWidthHandles.module.css` | 38 |
| `src/ui/chat/ChatView.module.css` | `transform 90ms ease` 一处 |
| `src/ui/chat/TurnProcess.module.css` | 51 |
| `src/ui/chat/AssistantNodeView.module.css` | 51, 256 |
| `src/ui/chat/TurnActions.module.css` | 8, `transform 90ms ease` 一处 |
| `src/ui/chat/ConversationMinimap.module.css` | 38 |
| `src/ui/ask/AskCard.module.css` | 64, 两个 `110ms` 行 |
| `src/ui/composer/ComposerPlate.module.css` | 三个 100ms 行 |
| `src/ui/composer/InputBar.module.css` | `transform 90ms ease` 一处 |
| `src/ui/rightbar/RightPanel.module.css` | 34 |
| `src/ui/pages/PluginSettings.module.css` | 271, 291 |
| `src/ui/pages/LimitSettings.module.css` | 108（同时去掉 `var(--token, 120ms)` 的 fallback 字面量） |
| `src/ui/pages/SkillSettings.module.css` | 169, 189 |
| `src/ui/approval/ApprovalPanel.module.css` | `transform 90ms ease` 一处 |
| `src/ui/chat/RetryRow.module.css` | `animation: retry-spin 1.1s linear infinite` |
| `src/ui/composer/Docks.module.css` | `todoShimmerSweep 2.6s`, `contextCardIn`（Task 3 已删） |
| `src/ui/primitives.module.css` / `panes.module.css` / `RunningMark.module.css` / `App.module.css` / `SettingsPage.module.css` | 其余循环动画时长 |

循环动画的时长映射：`1.1s`→`--ds-motion-slow`(320ms) 会太快，因此**为循环动画新增一档**：`--ds-motion-loop: 1100ms`，并把 shimmer（2.6s→`--ds-motion-loop-shimmer: 2600ms`）与 pulse（1s→`--ds-motion-loop: 1100ms`）各自对齐到最近的一档，取两档：`--ds-motion-loop: 1100ms`、`--ds-motion-loop-slow: 2600ms`。三档以上的重复值一律回落到这两档。

- [ ] **Step 9: 跑全仓守卫与全量单测**

Run: `node ../scripts/check-style-motion.mjs && pnpm lint:tokens && pnpm test`
Expected: `style motion OK — N module stylesheets, 0 baselined, 0 violations`；`style tokens OK`；396+ 全绿

- [ ] **Step 10: 把守卫接进 CI**

`.github/workflows/ci.yml` 的 desktop job 在 `pnpm lint:tokens` 之后加一行 `- run: node ../scripts/check-style-motion.mjs`（与 tokens 守卫同一条注释的理由：它只在这一处跑）。`desktop/package.json` 的 scripts 增加 `"lint:motion": "node ../scripts/check-style-motion.mjs"`（**这不是新依赖**）。

- [ ] **Step 11: 提交**

```bash
git add desktop/src/styles/tokens.css desktop/tests/motionTokens.test.ts desktop/tests/styleMotion.test.ts scripts/check-style-motion.mjs desktop/package.json .github/workflows/ci.yml desktop/src
git commit -m "refactor(desktop): one motion token ladder, guarded against raw durations"
```

---

### Task 5: 滚动跟随状态机（纯函数）

**Files:**
- Create: `desktop/src/state/scrollFollow.ts`
- Test: `desktop/tests/scrollFollow.test.ts`

**Interfaces:**
- Produces:
  - `type ScrollMode = 'following' | 'free' | 'settling'`
  - `interface ScrollSnapshot { mode: ScrollMode; showJump: boolean; atTop: boolean; unseen: number }`
  - `const SCROLL_EDGE = 8`、`const SCROLL_JUMP_THRESHOLD = 160`
  - `function nextScrollSnapshot(prev: ScrollSnapshot, input: ScrollInput): ScrollSnapshot`，`ScrollInput = { scrollTop, scrollHeight, clientHeight, lastScrollTop, programmatic: boolean }`
  - `function countUnseen(nodes: ReadonlyArray<{ kind: string; id: string }>, seenIds: ReadonlySet<string>): number`

- [ ] **Step 1: 写失败测试**

`desktop/tests/scrollFollow.test.ts`：

```ts
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { countUnseen, nextScrollSnapshot, type ScrollSnapshot } from '../src/state/scrollFollow.ts'

const following = (): ScrollSnapshot => ({ mode: 'following', showJump: false, atTop: false, unseen: 0 })
const input = (over: Partial<Parameters<typeof nextScrollSnapshot>[1]>) => ({
  scrollTop: 0, scrollHeight: 1000, clientHeight: 400, lastScrollTop: 0, programmatic: false, ...over,
})

test('at the bottom the mode is following and the jump pill is hidden', () => {
  const next = nextScrollSnapshot(following(), input({ scrollTop: 600, lastScrollTop: 600 }))
  assert.equal(next.mode, 'following')
  assert.equal(next.showJump, false)
  assert.equal(next.unseen, 0)
})

test('scrolling up past the edge releases follow and reveals the pill', () => {
  const released = nextScrollSnapshot(following(), input({ scrollTop: 300, lastScrollTop: 420 }))
  assert.equal(released.mode, 'free')
  assert.equal(released.showJump, true)   // distance 300 > 160
})

test('a programmatic scroll never changes the mode', () => {
  // 跳到底部 / 点 minimap 时组件自己改 scrollTop，它触发的事件不得把 following 打成 free
  const next = nextScrollSnapshot(following(), input({ scrollTop: 100, lastScrollTop: 600, programmatic: true }))
  assert.equal(next.mode, 'following')
})

test('content growth alone does not release follow', () => {
  // scrollHeight 变大、scrollTop 未变（现实里是向上的唯一方向），distance 变大了
  const next = nextScrollSnapshot(following(), input({ scrollTop: 600, scrollHeight: 2000, lastScrollTop: 600 }))
  assert.equal(next.mode, 'following')
})

test('returning to the bottom clears the pill and the unseen count', () => {
  const free: ScrollSnapshot = { mode: 'free', showJump: true, atTop: false, unseen: 3 }
  const back = nextScrollSnapshot(free, input({ scrollTop: 600, scrollHeight: 1000, lastScrollTop: 590 }))
  assert.equal(back.mode, 'following')
  assert.equal(back.showJump, false)
  assert.equal(back.unseen, 0)
})

test('atTop is true only inside the edge band', () => {
  assert.equal(nextScrollSnapshot(following(), input({ scrollTop: 4 })).atTop, true)
  assert.equal(nextScrollSnapshot(following(), input({ scrollTop: 40, lastScrollTop: 40 })).atTop, false)
})

test('between the two thresholds the pill keeps its previous visibility', () => {
  const free: ScrollSnapshot = { mode: 'free', showJump: true, atTop: false, unseen: 1 }
  const next = nextScrollSnapshot(free, input({ scrollTop: 480, scrollHeight: 1000, clientHeight: 400, lastScrollTop: 500 }))
  assert.equal(next.mode, 'free')     // distance 120 < 160
  assert.equal(next.showJump, true)   // 不改，避免 160 附近抖动
})

test('countUnseen counts user rows added after the last seen id', () => {
  const nodes = [
    { kind: 'user', id: 'u1' }, { kind: 'assistant', id: 'a1' },
    { kind: 'user', id: 'u2' }, { kind: 'assistant', id: 'a2' },
    { kind: 'user', id: 'u3' },
  ]
  assert.equal(countUnseen(nodes, new Set(['u1'])), 2)
  assert.equal(countUnseen(nodes, new Set(['u1', 'u2', 'u3'])), 0)
  // 窗口化裁掉旧行后仍按 id 计：u1 已不在数组里，u2/u3 就不是"新的"
  assert.equal(countUnseen(nodes.slice(2), new Set(['u1', 'u2', 'u3'])), 0)
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/scrollFollow.test.ts`
Expected: FAIL —— `Cannot find module '../src/state/scrollFollow.ts'`

- [ ] **Step 3: 实现 `scrollFollow.ts`**

`nextScrollSnapshot` 的顺序即规则（spec 决定 3 的 1–4 条）。关键实现点：`distance = scrollHeight - scrollTop - clientHeight`；`scrolledUp = scrollTop < lastScrollTop - 0.5`（亚像素容差，照 shadcn 的 `SCROLL_POSITION_EPSILON`）；`programmatic` 为真时**整个 mode 分支跳过**，只算 `atTop` 与 `distance`。`countUnseen` 用 `seenIds` 过滤并按 `kind === 'user'` 计数（**不看索引**，见 Review Focus 5）。

- [ ] **Step 4: 跑测试确认通过**

Run: `node --test tests/scrollFollow.test.ts`
Expected: PASS，8 个 test

- [ ] **Step 5: 提交**

```bash
git add desktop/src/state/scrollFollow.ts desktop/tests/scrollFollow.test.ts
git commit -m "feat(desktop): the scroll-follow state machine as a pure module"
```

---

### Task 6: ChatView 接入 + 未见计数浮标

**Files:**
- Modify: `desktop/src/ui/chat/ChatView.tsx`（497 → ≤ 470 行）
- Modify: `desktop/src/ui/chat/ChatView.module.css`（`.jump` 的徽标）
- Modify: `desktop/src/i18n/messages.ts`（`chat.jumpUnseen`）
- Test: `desktop/e2e/follow.spec.ts`（新建）

**Interfaces:**
- Consumes: Task 5 的 `nextScrollSnapshot` / `countUnseen` / `SCROLL_EDGE` / `ScrollSnapshot`
- Produces: 测试锚点 `[data-jump]`、`[data-jump][data-unseen]`

- [ ] **Step 1: 加文案（先加，落地后 i18nKeys 才绿）**

`messages.ts` 的 `zh` 与 `en` 各加一条，占位符必须一致：

```ts
'chat.jumpUnseen': '下方 {count} 条新消息',   // zh
'chat.jumpUnseen': '{count} new messages below',  // en
```

- [ ] **Step 2: 写失败测试**

`desktop/e2e/follow.spec.ts`：

```ts
/** Follow-tail: leaving the bottom raises the jump pill with the number of user
 * messages that arrived since; returning to the bottom clears it, and a
 * programmatic jump does not flicker the pill. */

import { expect, test } from '@playwright/test'
import assert from 'node:assert/strict'
import { boot, emitEvent, initialState } from './harness.ts'

const long = (n: number) =>
  Array.from({ length: n }, (_, i) => [
    { kind: 'user', id: `u${i}`, text: `第 ${i} 轮提问`, timestamp: i * 2 + 1 },
    { kind: 'assistant', id: `a${i}`, text: `回复 ${i}`, timestamp: i * 2 + 2 },
  ]).flat()

test('scrolling up raises the pill and counting user turns that arrive', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState({ running: true, transcript: long(20) }))
  const scroller = page.locator('[data-chat-scroll]')
  const jump = page.locator('[data-jump]')

  await expect(jump).toHaveCount(0)                       // 起手在底部
  await scroller.evaluate((el) => { el.scrollTop = 200 })
  await expect(jump).toBeVisible()

  await emitEvent(page, { type: 'message_start', message: { role: 'user', text: '新的一问' } })
  await expect(jump).toContainText('1')
})

test('returning to the bottom clears the pill and its count', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState({ running: true, transcript: long(20) }))
  const scroller = page.locator('[data-chat-scroll]')
  await scroller.evaluate((el) => { el.scrollTop = 200 })
  const jump = page.locator('[data-jump]')
  await expect(jump).toBeVisible()

  await jump.click()
  await expect(jump).toHaveCount(0, { timeout: 2000 })
})

test('a streamed delta at the bottom keeps following without showing the pill', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState({ running: true, transcript: long(20) }))
  const jump = page.locator('[data-jump]')
  for (const delta of ['一', '二', '三']) {
    await emitEvent(page, { type: 'message_update', assistant_event: { type: 'text_delta', delta } })
  }
  await expect(jump).toHaveCount(0)
  // 仍然贴着底部：尾部与视口底边的距离在 edge 之内
  const distance = await page.locator('[data-chat-scroll]').evaluate((el) =>
    el.scrollHeight - el.scrollTop - el.clientHeight,
  )
  assert.ok(distance <= 8, `follow-tail distance ${distance}`)
})
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pnpm exec playwright test e2e/follow.spec.ts`
Expected: FAIL —— `[data-jump]` 不存在（现在只有没有锚点的 `.jump` 按钮）

- [ ] **Step 4: 接入 ChatView**

把 `follow` / `showJump` / `activeId` 三个 `useState` 收成一个 `const [snap, setSnap] = useState<ScrollSnapshot>(...)`，并加 `const snapRef = useRef(snap)`（`onScroll` 闭包里读最新值，避免把 `onScroll` 绑到每次 state 变化上重建监听）。加 `const seenIds = useRef(new Set<string>())` 与 `const programmatic = useRef(false)`。

- `scrollToRow`、`jumpTo` 与 `revealEarlier` 里的 `scrollTo` 调用前后把 `programmatic.current` 置真/在下一帧置假（`requestAnimationFrame`）；
- `onScroll` 改为 `setSnap((prev) => nextScrollSnapshot(prev, { scrollTop, scrollHeight, clientHeight, lastScrollTop: lastTop.current, programmatic: programmatic.current }))`，之后 `lastTop.current = scrollTop`；`activeId` 的计算与 `revealTop()` 调用保留（`atTop` 为真时）；
- 「新内容贴底」的 effect 改读 `snapRef.current.mode === 'following'`；
- 浮标按钮：`{snap.showJump && (<button data-jump data-unseen={snap.unseen > 0 || undefined} …>)}`，`unseen > 0` 时渲染 `<span className={css.jumpBadge}>{snap.unseen > 9 ? '9+' : snap.unseen}</span>` 并把 `aria-label` 换成 `t('chat.jumpUnseen', { count: snap.unseen })`；
- **逐条 user 节点**：`useEffect` 在 `[revision]` 变化时，若 `mode === 'following'` 把 `seenIds.current` 同步为当前全部 user id，否则 `setSnap((s) => ({ ...s, unseen: countUnseen(nodes, seenIds.current) }))`。

- [ ] **Step 5: 样式**

`ChatView.module.css`：`.jump` 增加 `position: relative` 与 `opacity` + `transform` 的 `transition`（用 Task 4 的 token）；`.jumpBadge` 为 16px 圆形徽标（`min-width`, `padding-inline: 4px`, `font-size: var(--ds-font-11)`, 背景 `var(--dsw-alias-state-business-primary)`，前景 `var(--dsw-alias-label-primary-foreground)`）。`data-unseen` 时宽高改为 `auto` 胶囊。

- [ ] **Step 6: 跑测试确认通过**

Run: `pnpm exec playwright test e2e/follow.spec.ts e2e/transcript.spec.ts e2e/session.spec.ts e2e/followup.spec.ts`
Expected: 全 PASS。`e2e/transcript.spec.ts:74` 的窗口化用例（`toHaveCount(60)` / `显示更早的 240 条`）必须不变——本次不动窗口逻辑。

- [ ] **Step 7: 检查行数与提交**

Run: `wc -l src/ui/chat/ChatView.tsx`
Expected: ≤ 470

```bash
git add desktop/src/ui/chat/ChatView.tsx desktop/src/ui/chat/ChatView.module.css desktop/src/i18n/messages.ts desktop/e2e/follow.spec.ts
git commit -m "feat(desktop): follow-tail through the scroll state machine, with an unseen count on the jump pill"
```

---

## Self-Review

**1. Spec coverage** — 决定 1（退场动画）→ Task 1/2/3；决定 2（token 阶梯 + 守卫）→ Task 4；决定 3（滚动状态机）→ Task 5/6。spec 的「不做」清单在 Task 6 的 Interfaces 里没有反向要求，无遗漏。

**2. Step scan** — 每个实现步骤给的是签名、相位表、属性清单与目标态，没有一处替实现者写函数体（except `nextPhase` 的映射表与 `tokens.css` 的别名块——前者是唯一的多分支判定，后者是逐字文案，属 spec 钉死的值）。

**3. Type consistency** — `PresencePhase` / `presenceState` / `isMounted` 在 Task 1 定义，Task 2/3 只用 `usePresence().mounted/.state`；`ScrollSnapshot` 字段（`mode`/`showJump`/`atTop`/`unseen`）在 Task 5 定义，Task 6 逐字段引用，无别名漂移。`scanMotionCss` 的返回形状与 `scanCss` 一致（`violations`/`skipped`/`stale`），Task 4 的测试与守卫脚本共用。

**4. Review Focus** — 5 条各自的测试：①审批卡幂等 → Task 3 Step 1 的第一个用例；②reduced-motion → Task 1 的定时器设计 + Task 3 删掉 `animation: none` 覆盖（不再有需要降级的 animation）；③程序化滚动 → Task 5 的 `programmatic` 用例 + Task 6 Step 2 第三个用例；④token 作用域 → Task 4 的 `:root` 负向断言；⑤窗口化计数 → Task 5 `countUnseen` 的最后一条断言。

**5. Proportion** — 计划约 350 行，spec 约 190 行；两者都远小于它们描述的代码改动量，无逐行转录。
