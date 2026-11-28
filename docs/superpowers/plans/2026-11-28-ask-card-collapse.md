# 提问卡片：更小、更密、可折叠、进正文流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `ask` 卡片从浮动 dock 搬进正文滚动区，压小、加密、加一个折叠按钮，并让它在长高时把正文推上去而不是盖住。

**Architecture:** 三块独立改动：`state/askDraft.ts` 加折叠位（纯函数，可离线测）；`ui/ask/AskCard.*` 变小并加折叠按钮，样式与动画照抄 `ui/composer/Docks.module.css` 的 todo dock；`ui/chat/ChatView.tsx` 负责挂载卡片、量它的高度并发布成 `--ask-card-height`，供 jump pill / minimap / 宽度把手避让，同时在「跟随底部」时把长高钉回底部。

**Tech Stack:** React 18 + TS + CSS Modules（Vite）；测试 `node --test`（纯模块）与 Playwright（`desktop/e2e`）。

**Spec:** [docs/superpowers/specs/2026-11-28-ask-card-collapse-design.md](../specs/2026-11-28-ask-card-collapse-design.md)

## Global Constraints

- 单文件 ≤ 800 行（`scripts/check_architecture.py`）。当前 `ChatView.tsx` 494 行、`AskCard.tsx` 239 行、`AskCard.module.css` 315 行；本计划新增量不得把任何一个推过线。
- 组件样式表只能用 token：不得出现裸色值（`scripts/check-style-tokens.mjs`）、不得出现裸时长/缓动（`scripts/check-style-motion.mjs`）。
- 不新增图标、不新增 design token、不新增依赖。
- 新增用户可见文案必须中英双份进 `desktop/src/i18n/messages.ts`（`desktop/tests/locale.test.ts` 钉住键集与占位符一致）。
- 不改 `ask` 协议、不改 `resolveAsk` 语义、不动 `ApprovalPanel`。
- 归档门禁：`bash scripts/check.sh`（ruff / mypy / 架构预算 / rpc 新鲜度 / worktree / pytest）+ `cd desktop && pnpm test && pnpm lint:tokens && pnpm lint:motion && npx tsc --noEmit`。
- 所有提交在 `main` 上直接做（本仓约定），提交信息用 `feat(desktop):` / `refactor(desktop):` 前缀。

## Review Focus

- **面板分栏时出现两张卡**：`pendingForProject` 对没有 `session` 标签的提问（老服务端）一律放行，若每个 `ChatView` 都挂卡片，多个 pane 会各渲染一张、回答两次。必须只有聚焦 pane 挂载。
- **卡片被遮罩渐隐吃掉**：卡片落在滚动区底部，正好压在 `.scroll` 的 `mask-image` 渐隐带上（默认 16px），下沿会发灰。高度进 `--ask-card-height` 后遮罩起点必须随之下移。
- **jump pill 盖在卡片页脚上**：pill 按 `--composer-dock-height` 定位，卡片搬进滚动区后两者重合。
- **跟随底部时卡片长高**：展开 / 多选 / 自定义输入框出现都会让卡片变高，若只靠 `revision` 变化钉底，卡片会长在视口外。
- **回答完的收缩**：卡片消失时滚动位置要能被浏览器 clamp，不能把读者甩到别处。

---

### Task 1: 折叠态进草稿（纯模块）

**Files:**
- Modify: `desktop/src/state/askDraft.ts`
- Test: `desktop/tests/askDraft.test.ts`

**Interfaces:**
- Consumes: 无（这是本计划的第一块）。
- Produces: `AskDraft` 多一个必填字段 `collapsed: boolean`；导出 `setCollapsed(draft: AskDraft, collapsed: boolean): AskDraft`。`emptyAskDraft` 默认 `collapsed: false`。Task 2 用这两个。

- [ ] **Step 1: 写失败的测试**

追加到 `desktop/tests/askDraft.test.ts`（文件顶部 `import` 里加 `setCollapsed`）：

```ts
test('a fresh draft is expanded', () => {
  assert.equal(emptyAskDraft(questions).collapsed, false)
})

test('collapsing keeps every answer and the open question', () => {
  let draft = emptyAskDraft(questions)
  draft = selectOption(draft, 0, 'SQLite', false)
  draft = openQuestion(draft, 1)
  const collapsed = setCollapsed(draft, true)

  assert.equal(collapsed.collapsed, true)
  assert.equal(collapsed.index, 1)
  assert.deepEqual(draftAnswers(collapsed), [['SQLite'], null])
  assert.equal(setCollapsed(collapsed, false).collapsed, false)
})
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd desktop && node --test tests/askDraft.test.ts`
Expected: FAIL — `setCollapsed is not a function` / `collapsed` 为 `undefined`。

- [ ] **Step 3: 实现**

`desktop/src/state/askDraft.ts`：`AskDraft` 接口加

```ts
  /** Folded to a single status row. Per request: it rides the draft so a
   * remount cannot pop the card back open. */
  collapsed: boolean
```

`emptyAskDraft` 的返回值加 `collapsed: false`（放在 `index` 之后）。文件末尾加：

```ts
export function setCollapsed(draft: AskDraft, collapsed: boolean): AskDraft {
  return { ...draft, collapsed }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `cd desktop && node --test tests/askDraft.test.ts`
Expected: PASS（原有用例也全过）。

- [ ] **Step 5: 提交**

```bash
git add desktop/src/state/askDraft.ts desktop/tests/askDraft.test.ts
git commit -m "feat(desktop): keep the ask card's folded state in its draft"
```

---

### Task 2: 卡片变小 + 折叠按钮

**Files:**
- Modify: `desktop/src/ui/ask/AskCard.tsx`, `desktop/src/ui/ask/AskCard.module.css`, `desktop/src/i18n/messages.ts`
- Test: `desktop/e2e/composer.spec.ts`（新增一条用例）

**Interfaces:**
- Consumes: `setCollapsed` / `draft.collapsed`（Task 1）。
- Produces: 卡片根节点带 `data-ask-card`（e2e 与调试锚点）；折叠按钮的 `aria-expanded` 为 `'true' | 'false'`；折叠时正文容器带 `aria-hidden="true"` 且 `visibility: hidden`。

- [ ] **Step 1: 写失败的 e2e**

追加到 `desktop/e2e/composer.spec.ts`（放在现有 ask 用例之后）：

```ts
test('the ask card folds to one status row and unfolds again', async ({ page }) => {
  await boot(page, initialState())
  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q-fold',
    questions: [
      {
        id: 'db',
        question: '用哪个数据库？',
        header: '数据库',
        options: [{ label: 'SQLite' }, { label: 'Postgres' }],
        multi: false,
      },
    ],
  })
  const card = page.getByTestId('ask-card')
  await expect(card).toBeVisible()
  await expect(card).toContainText('用哪个数据库？')

  const collapsedHeight = await card.boundingBox().then((box) => box?.height ?? 0)
  const toggle = page.getByRole('button', { name: '折叠提问' })
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await toggle.click()

  await expect(page.getByRole('button', { name: '展开提问' })).toHaveAttribute(
    'aria-expanded',
    'false',
  )
  await expect(card).toContainText('需要你的选择')
  // The body is still mounted (that is what makes the fold animate), so the
  // claim under test is visibility, not absence from the DOM.
  await expect(card.getByText('用哪个数据库？').first()).toBeHidden()
  const foldedHeight = await card.boundingBox().then((box) => box?.height ?? 0)
  assert.ok(foldedHeight > 0 && foldedHeight < collapsedHeight)

  await page.getByRole('button', { name: '展开提问' }).click()
  await expect(card.getByText('用哪个数据库？').first()).toBeVisible()
})
```

- [ ] **Step 2: 跑 e2e，确认失败**

Run: `cd desktop && pnpm e2e composer.spec.ts -g "folds to one status row"`
Expected: FAIL — 找不到名为「折叠提问」的按钮。

- [ ] **Step 3: 加文案**

`desktop/src/i18n/messages.ts`，两个字典的 `ask.*` 段末尾各加两条（中文区在 `'ask.customPlaceholder'` 之后）：

```ts
  'ask.collapse': '折叠提问',
  'ask.expand': '展开提问',
```

英文区对应：

```ts
  'ask.collapse': 'Collapse question',
  'ask.expand': 'Expand question',
```

- [ ] **Step 4: 组件加折叠**

`desktop/src/ui/ask/AskCard.tsx`：

1. `import { ChevronDownIcon, SparkIcon } from '../icons'`；`import { useId }` 补进 react 的 import；`askDraft` 的 import 列表加 `setCollapsed`。
2. `AskCardBody` 里取 `const collapsed = draft.collapsed`、`const bodyId = useId()`。
3. 根 `<section>` 加 `data-collapsed={collapsed || undefined}`；`<div className={css.root}>` 加 `data-ask-card`。
4. 状态条 `<header>` 的进度点之后、`</header>` 之前加折叠按钮：

```tsx
<button
  type="button"
  className={css.fold}
  aria-label={collapsed ? t('ask.expand') : t('ask.collapse')}
  title={collapsed ? t('ask.expand') : t('ask.collapse')}
  aria-controls={bodyId}
  aria-expanded={!collapsed}
  onClick={() => updateDraft(setCollapsed(draft, !collapsed))}
>
  <ChevronDownIcon size={16} />
</button>
```
`css.indicators` 的 `margin-left: auto` 改成由 `.fold` 接管右边：`.indicators { margin-left: auto; }` 保留，`.fold { margin-left: 2px }`；无进度点时 `.fold` 自己吃 `margin-left: auto`（见 Step 5 的 CSS）。
5. 折叠后要能点进度点展开：进度点的 `onClick` 改为

```tsx
onClick={() =>
  updateDraft(setCollapsed(openQuestion(draft, position), false))
}
```

6. 正文与页脚包进可折叠容器（页脚跟着一起折，折起来的形态见 spec「只剩一行」）：

```tsx
<div id={bodyId} className={css.clip} aria-hidden={collapsed}>
  <div className={css.clipInner}>
    <div className={css.body}>…原 body…</div>
    <footer className={css.footer}>…原 footer…</footer>
  </div>
</div>
```
原 `.body` / `.footer` 的闭合标签随之调整；`aria-hidden` 折叠时让 Tab 也跳过，`.clip` 折叠时 `visibility: hidden`（见 CSS）。

- [ ] **Step 5: 样式：变小 + 折叠动画**

`desktop/src/ui/ask/AskCard.module.css` 按 spec 的表格改数值，并加：

```css
/* The fold: one status row when closed. Same disclosure technique as the todo
 * dock (grid rows 0fr→1fr) so both surfaces open at the same beat. */
.clip {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows var(--ds-transition-duration) var(--ds-ease-in-out);
}

.card[data-collapsed] .clip {
  grid-template-rows: 0fr;
  visibility: hidden;
  transition:
    grid-template-rows var(--ds-transition-duration) var(--ds-ease-in-out),
    visibility 0s var(--ds-transition-duration);
}

.clipInner {
  min-height: 0;
  overflow: hidden;
}

.fold {
  display: grid;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  margin-left: auto;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: var(--ds-radius-8);
  background: none;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
  transition:
    background-color var(--ds-motion-fast) var(--ds-ease-out),
    color var(--ds-motion-fast) var(--ds-ease-out);
}

.fold:hover {
  background: var(--ds-surface-hover);
  color: var(--dsw-alias-label-primary);
}

.fold:focus-visible {
  outline: 2px solid var(--ds-accent);
  outline-offset: -2px;
}

.fold svg {
  transform: rotate(180deg);
  transition: transform var(--ds-transition-duration) var(--ds-ease-in-out);
}

.fold[aria-expanded='true'] svg {
  transform: rotate(0deg);
}
```

注意 `.indicators` 已有 `margin-left: auto`；有进度点时它先吃满，`.fold` 紧随其后，`margin-left: auto` 在 `.fold` 上无副作用。`.strip` 的 padding 改成 `8px 8px 0 12px`（右侧给按钮留位）。

- [ ] **Step 6: 跑 e2e，确认通过**

Run: `cd desktop && pnpm e2e composer.spec.ts`
Expected: PASS（新增两条 + 原 ask 用例 + 其余 composer 用例）。

- [ ] **Step 7: 跑样式门禁与单测**

Run: `cd desktop && pnpm lint:tokens && pnpm lint:motion && pnpm test && npx tsc --noEmit`
Expected: 全绿。

- [ ] **Step 8: 提交**

```bash
git add desktop/src/ui/ask desktop/src/i18n/messages.ts desktop/e2e/composer.spec.ts
git commit -m "feat(desktop): denser ask card with a fold toggle"
```

---

### Task 3: 卡片搬进正文流

**Files:**
- Modify: `desktop/src/ui/composer/InputBar.tsx`, `desktop/src/ui/chat/ChatView.tsx`, `desktop/src/ui/chat/ChatView.module.css`, `desktop/src/ui/panes/PaneView.tsx`
- Test: `desktop/e2e/composer.spec.ts`（新增两条用例）

**Interfaces:**
- Consumes: 卡片自带 `data-ask-card`（Task 2）；`useAskStore` / `pendingForProject`（既有模块）。
- Produces: `ChatViewProps` 新增可选 `askCard?: boolean`（默认 `true`，`false` 表示这个实例不挂卡片）；`ChatView` 根节点发布 CSS 变量 `--ask-card-height`（px 字符串，无卡片时为 `0px`）；正文座位层带 `data-ask-seat`。

- [ ] **Step 1: 写失败的 e2e**

追加到 `desktop/e2e/composer.spec.ts`（两条都要，它们钉的是同一件事的两面：卡片在正文里，且离开视口时还有回去的路）。两个文件顶部的 `harness` import 都已在文件里，无需新增。

```ts
test('the ask card lives in the transcript and never covers it', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 12 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: '回复 '.repeat(60), timestamp: index * 2 + 2 },
  ]).flat()
  await boot(page, initialState({ transcript }))
  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q-seat',
    questions: [
      {
        id: 'db',
        question: '用哪个数据库？',
        options: [{ label: 'SQLite' }, { label: 'Postgres' }],
        multi: false,
      },
    ],
  })
  const card = page.getByTestId('ask-card')
  await expect(card).toBeVisible()
  // The card is a transcript row, not a floating layer over it.
  await expect(page.locator('[data-ask-seat] [data-ask-card]')).toHaveCount(1)

  const overlap = await page.evaluate(() => {
    const seat = document.querySelector<HTMLElement>('[data-ask-seat]')
    const rows = document.querySelectorAll<HTMLElement>('[data-minimap-id]')
    const last = rows[rows.length - 1]
    if (seat === null || last === undefined) return Number.POSITIVE_INFINITY
    return last.getBoundingClientRect().bottom - seat.getBoundingClientRect().top
  })
  assert.ok(overlap <= 1, `last row overlaps the ask card by ${overlap}px`)

  // Scrolled up, the card leaves the viewport instead of hanging over the reader.
  await page.locator('[data-chat-scroll]').evaluate((el) => {
    el.scrollTop = 0
    el.dispatchEvent(new Event('scroll'))
  })
  const visible = await card.evaluate((el) => {
    const box = el.getBoundingClientRect()
    return box.bottom > 0 && box.top < window.innerHeight
  })
  assert.equal(visible, false)
})

test('a pending question keeps a way back to the tail from anywhere', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 20 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: '回复 '.repeat(60), timestamp: index * 2 + 2 },
  ]).flat()
  await boot(page, initialState({ transcript }))
  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q-pill',
    questions: [{ id: 'go', question: '继续吗？', options: [{ label: '继续' }], multi: false }],
  })
  // Scroll away the way a wheel does, so the view's own listener sees it.
  await page.locator('[data-chat-scroll]').evaluate((el) => {
    el.scrollTop = 0
    el.dispatchEvent(new Event('scroll'))
  })
  const pill = page.locator('[data-jump]')
  await expect(pill).toBeVisible()
  await pill.click()
  await expect(page.getByTestId('ask-card')).toBeInViewport()
})
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd desktop && pnpm e2e composer.spec.ts -g "lives in the transcript"`
Expected: FAIL — 没有 `[data-ask-seat]`（卡片还在 dock 里）。

- [ ] **Step 3: `InputBar` 交出卡片**

`desktop/src/ui/composer/InputBar.tsx`：删掉 `import { AskCard } from '../ask/AskCard'`、`const askCount = useAskStore((state) => state.queue.length)`、以及 `{askCount > 0 && <AskCard />}` 这一行；`useAskStore` 若再无其他用处，从 `../../state/stores` 的 import 里去掉。

- [ ] **Step 4: `ChatView` 挂卡片并量高度**

`desktop/src/ui/chat/ChatView.tsx`：

1. import：`import { AskCard } from '../ask/AskCard'`、`import { useAskStore } from '../../state/stores'`、`import { pendingForProject } from '../../state/pendingProject'`。
2. `ChatViewProps` 加：

```ts
  /** Whether this transcript instance hosts the ask card. Only one may: the
   * queue is global and an untagged request would otherwise render in every
   * pane. Panes pass it only for the focused one. */
  askCard?: boolean
```
3. 组件里加：

```ts
  // Same slot the card itself resolves — AskCard reads the app store's project,
  // so the pill must not disagree with it about whether a question is pending.
  const askProject = useAppStore((state) => state.project)
  const askQueue = useAskStore((state) => state.queue)
  const askPending =
    props.askCard !== false &&
    pendingForProject(askQueue, askProject, currentPath).length > 0
  const rootRef = useRef<HTMLDivElement | null>(null)
  const askSeatRef = useRef<HTMLDivElement | null>(null)
```
（`currentPath` 已在组件里算好。）
4. 座位：

```tsx
  const askSeat =
    props.askCard === false ? null : (
      <div ref={askSeatRef} className={css.askSeat} data-ask-seat>
        <AskCard />
      </div>
    )
```
渲染点到两处：`isEmpty` 分支里 `<div className={css.emptyHost}>…</div>` 之后、以及 `.flow` 的 turn 列表 + running row 之后（`</div>` 之前），各放 `{askSeat}`。
5. 观测与发布。位置有讲究：放在 `const isEmpty = nodes.length === 0` 这一行**之后**、`return` 之前（依赖数组里要用 `isEmpty`，而 `const` 在声明前求值会抛 TDZ）：

```tsx
  // The card is a transcript row, so its height is the transcript's bottom
  // band: the floating pill/minimap/handles must sit above it, and a card that
  // grows while the reader follows the tail must stay in view. One measurement
  // serves both (the fade already clears it — see the design doc).
  useLayoutEffect(() => {
    const node = askSeatRef.current
    const root = rootRef.current
    if (node === null || root === null) return
    const publish = () => {
      const height = Math.round(node.getBoundingClientRect().height)
      root.style.setProperty('--ask-card-height', `${Math.max(0, height)}px`)
      const scroller = scrollerRef.current
      if (scroller === null || height === 0) return
      if (snapRef.current.mode !== 'following') return
      markProgrammatic()
      scroller.scrollTop = scroller.scrollHeight
    }
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(node)
    return () => observer.disconnect()
  }, [markProgrammatic, isEmpty, props.askCard])
```

依赖里必须带 `isEmpty` 与 `props.askCard`：座位在空/非空两个分支里是两个不同的 DOM
节点，换分支时 ref 换了人而 effect 不重跑，就把观察者留在了旧节点上。
6. 根节点加 ref：`<div className={css.root} ref={rootRef}>`。
7. jump pill 的显示条件改为 `(snap.showJump || askPending) && (...)`，给按钮加 `data-ask={askPending || undefined}`；`aria-label` 改为三段：`snap.unseen > 0` 用 `t('chat.jumpUnseen', { count: snap.unseen })`，否则 `askPending` 为真用 `t('chat.backToQuestion')`，其余保持 `'scroll to bottom'`。`onClick` 不变（它已经把 `mode` 设为 `following` 并钉到底部）。为此加文案（Step 5）。

- [ ] **Step 5: 文案**

`desktop/src/i18n/messages.ts` 加一对：

```ts
  'chat.backToQuestion': '回到待答的提问',
```
```ts
  'chat.backToQuestion': 'Back to the pending question',
```

- [ ] **Step 6: 样式：浮层避让、座位**

先确认一件事，它决定了要不要动遮罩：`.scroll` 的 padding-bottom 是
`calc(var(--composer-dock-height, 168px) + 16px)`，遮罩的不透明截止线是
`100% - dock - 16px`——两者是同一条线。所以「跟随底部」时卡片底边正好停在遮罩
截止线上，整张卡都在不透明区里，**不需要动 mask**（滚到中间时卡片像普通内容一样
经过底部渐隐带，这是所有滚动内容的行为）。

`desktop/src/ui/chat/ChatView.module.css`：

1. `.root` 加 `--ask-card-height: 0px;`（默认零，组件会覆盖）。
2. `.jump` 的 `bottom`：`calc(var(--composer-dock-height, 168px) + var(--ask-card-height) + 12px)`。
3. 座位：

```css
/* The ask card is a transcript row: it scrolls with the conversation instead
 * of hanging over it. Empty when no question is pending, so the reserved band
 * collapses to nothing. */
.askSeat:empty {
  display: none;
}

.askSeat {
  display: flex;
  justify-content: center;
}
```
4. `desktop/src/ui/chat/ConversationMinimap.module.css` 与
   `desktop/src/ui/chat/ChatWidthHandles.module.css` 里的 `bottom` / `inset`
   同样加上 `+ var(--ask-card-height, 0px)`，浮层不压在卡片上。

- [ ] **Step 7: 分栏只让聚焦的 pane 挂卡片**

`desktop/src/ui/panes/PaneView.tsx`：

```tsx
        <ChatView
          chat={pane.chat}
          busy={pane.busy}
          askCard={isCurrent}
          onLoadOlder={() => panes.loadOlder(pane.path)}
          onRetry={() => void panes.runCommand(pane.path, 'retry', '')}
          emptyState={<div className={css.empty}>{t('panes.empty')}</div>}
        />
```

- [ ] **Step 8: 跑 e2e**

Run: `cd desktop && pnpm e2e composer.spec.ts panes.spec.ts follow.spec.ts`
Expected: 全 PASS。

- [ ] **Step 9: 类型、单测、样式门禁**

Run: `cd desktop && npx tsc --noEmit && pnpm test && pnpm lint:tokens && pnpm lint:motion`
Expected: 全绿。

- [ ] **Step 10: 提交**

```bash
git add desktop/src/ui/chat desktop/src/ui/composer/InputBar.tsx desktop/src/ui/panes/PaneView.tsx desktop/src/i18n/messages.ts desktop/e2e/composer.spec.ts
git commit -m "feat(desktop): move the ask card into the transcript flow"
```

---

### Task 4: 三条回归（分栏单卡 · 长高钉底 · 收缩不甩）

> 这三条钉的是 Task 3 引入的失败模式，不是新功能。先写测试：如果它们一开始就是绿的，说明 Task 3 做对了；红了就按 Step 里给的落点修，不要绕过。

**Files:**
- Test: `desktop/e2e/composer.spec.ts`, `desktop/e2e/panes.spec.ts`
- Modify: 只有前三条用例暴露问题时才改代码（改哪里由失败点决定，契约见 Task 3）。

**Interfaces:**
- Consumes: Task 2/3 的行为契约（`data-ask-card`、`data-ask-seat`、`--ask-card-height`、`ChatViewProps.askCard`）。
- Produces: 无新接口；只加测试。

- [ ] **Step 1: 分栏里只有一张卡**

追加到 `desktop/e2e/panes.spec.ts`（该文件的 harness import 要补上 `emitEvent`：
`import { boot, emitEvent, initialState, presetState, type BridgeState } from './harness.ts'`）：

```ts
test('a pending question renders in one pane only', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.locator('[data-open-split]').click()
  await expect(page.locator('[data-pane]')).toHaveCount(2)

  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q-pane',
    questions: [{ id: 'go', question: '继续吗？', options: [{ label: '继续' }], multi: false }],
  })
  await expect(page.getByTestId('ask-card')).toHaveCount(1)
})
```

- [ ] **Step 2: 跑，确认它此刻是绿的（分母就是存在性）**

Run: `cd desktop && pnpm e2e panes.spec.ts -g "one pane only"`
Expected: PASS。若 FAIL（出现 2 张），说明 Task 3 Step 7 的 `askCard={isCurrent}` 没生效或 `pendingForProject` 的 session 过滤没被用上——先修那一处，再回来。

- [ ] **Step 3: 跟随底部时卡片长高仍留在视口内**

追加到 `desktop/e2e/composer.spec.ts`：

```ts
test('a growing ask card stays in view while the reader follows the tail', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 12 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: '回复 '.repeat(60), timestamp: index * 2 + 2 },
  ]).flat()
  await boot(page, initialState({ transcript }))
  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q-grow',
    questions: [
      {
        id: 'db',
        question: '用哪个数据库？',
        options: [{ label: 'SQLite' }, { label: 'Postgres' }],
        multi: false,
      },
    ],
  })
  const card = page.getByTestId('ask-card')
  await expect(card).toBeVisible()
  // Selecting the free-text row grows the card by an input row.
  await card.getByRole('radio', { name: '其他（自己填写）' }).click()
  await expect(card.getByPlaceholder('输入你的答案…')).toBeVisible()
  await expect(card).toBeInViewport()

  const band = await page.evaluate(() =>
    document
      .querySelector('[data-shell-center]')
      ?.style.getPropertyValue('--ask-card-height'),
  )
  assert.ok(band !== undefined && Number.parseFloat(band) > 0, `--ask-card-height=${band}`)
})
```

- [ ] **Step 4: 跑，确认它此刻是绿的**

Run: `cd desktop && pnpm e2e composer.spec.ts -g "stays in view"`
Expected: PASS。若 FAIL：`--ask-card-height` 为 0 说明 Task 3 的 ResizeObserver 没观察到座位（`:empty` 或 ref 提前为 null）；`toBeInViewport` 失败说明钉底回调没在 `following` 时执行。

- [ ] **Step 5: 收缩不把读者甩走**

追加到 `desktop/e2e/composer.spec.ts`：

```ts
test('answering the question leaves the reader at the tail', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 12 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: '回复 '.repeat(60), timestamp: index * 2 + 2 },
  ]).flat()
  await boot(page, initialState({ transcript }))
  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q-shrink',
    questions: [{ id: 'go', question: '继续吗？', options: [{ label: '继续' }], multi: false }],
  })
  await page.getByTestId('ask-card').getByRole('button', { name: '不回答' }).click()
  await expect(page.getByTestId('ask-card')).toHaveCount(0)
  const distance = await page.locator('[data-chat-scroll]').evaluate((el) =>
    el.scrollHeight - el.scrollTop - el.clientHeight,
  )
  assert.ok(distance <= 8, `tail distance after the card left: ${distance}`)
})
```

- [ ] **Step 6: 跑，确认它此刻是绿的**

Run: `cd desktop && pnpm e2e composer.spec.ts -g "at the tail"`
Expected: PASS。若 FAIL：卡片卸载后 `--ask-card-height` 未归零——把 ResizeObserver 的 `publish` 在 `height === 0` 时也写属性（Task 3 Step 4 已经是这么写的），并确认座位是 `:empty` 而不是带着零高子节点。

- [ ] **Step 7: 提交**

```bash
git add desktop/e2e/composer.spec.ts desktop/e2e/panes.spec.ts
git commit -m "test(desktop): pin the ask card's seat, growth and removal"
```

---

### Task 5: 整仓门禁与实机确认

**Files:**
- 无新增；只在必要时微调 Task 2/3 涉及的 CSS。

**Interfaces:**
- Consumes: Task 1–3 的全部产物。
- Produces: 可合并的分支。

- [ ] **Step 1: 整仓门禁**

Run: `bash scripts/check.sh`
Expected: `==> tests` 之后全绿（`architecture budget: ok` 必须在输出里）。

- [ ] **Step 2: 全量 e2e**

Run: `cd desktop && pnpm e2e`
Expected: 全 PASS。

- [ ] **Step 3: 实机看两件事**

Run: `cd desktop && pnpm dev`，在浏览器里让 agent 提一个问题：

1. 停在最底部：卡片下沿不该有灰边（它正好停在遮罩截止线上）。
2. 往上滚：卡片随内容滚出视口，阅读区恢复整屏。

若第 1 条发灰，把 `.scroll` mask 的 `- 16px` / `+ 2px` 收成 `- 8px` / `+ 1px`，
重跑 Step 2 的 composer 用例。

- [ ] **Step 4: 提交（仅在 Step 3 改过东西时）**

```bash
git add desktop/src/ui/chat/ChatView.module.css
git commit -m "fix(desktop): lift the transcript fade clear of the ask card"
```
