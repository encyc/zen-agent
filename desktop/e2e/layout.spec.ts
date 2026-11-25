/** Shell layout and the session sidebar: the project rail, sidebar
 * height and collapse, zen-workspace filtering, pinned/project groups,
 * row menus (pin, rename, delete, fork, export), the context ring card
 * and the collapsible session/project sections. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, presetState, sidebarKey } from './harness.ts'

test('a new project joins the rail and can be removed', async ({ page }) => {
  await boot(page, initialState({ running: false, dialogSelection: '/work/newproj' }))
  await page.getByRole('button', { name: '新建项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="newproj"]')).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.length ?? 0)).toBe(1)
  assert.equal(
    await page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root),
    '/work/newproj',
  )
  await page.locator('[data-sidebar-project-group="newproj"] [data-remove-project]').click()
  await expect(page.locator('[data-sidebar-project-group="newproj"]')).toHaveCount(0)
})
test('hovering the context ring shows the composition card', async ({ page }) => {
  await boot(page, initialState())
  const card = page.getByTestId('context-card')
  await expect(card).toHaveCount(0)

  await page.getByTestId('context-meter').hover()
  await expect(card).toBeVisible()
  await expect(card).toContainText('已用 32K')
  await expect(card).toContainText('25%')
  await expect(card).toContainText('32K / 128K')
  await expect(card).toContainText('系统提示词')
  await expect(card).toContainText('≈3.2K · 2.5%')
  await expect(card).toContainText('工具 schema')
  await expect(card).toContainText('≈4.8K · 3.8%')
  await expect(card).toContainText('会话')
  await expect(card).toContainText('≈24K · 18.8%')
  await expect(card).toContainText('空闲')
  await expect(card).toContainText('≈96K · 75.0%')

  // Reading takes time: moving onto the card must keep it open.
  await card.hover()
  await expect(card).toBeVisible()

  await page.mouse.move(5, 5)
  await expect(card).toHaveCount(0)
})
test('the shell keeps the sidebar full height and docks the composer inside the center column', async ({
  page,
}) => {
  await boot(page, initialState())
  const sidebar = await page.locator('[data-shell-sidebar]').boundingBox()
  const viewport = page.viewportSize()
  assert.ok(sidebar !== null && viewport !== null)
  assert.equal(Math.round(sidebar.height), viewport.height)
  assert.equal(Math.round(sidebar.y), 0)
  const composer = await page.locator('[data-shell-composer]').boundingBox()
  const center = await page.locator('[data-shell-center]').boundingBox()
  assert.ok(composer !== null && center !== null)
  assert.ok(composer.x >= center.x)
  assert.ok(composer.x + composer.width <= center.x + center.width + 1)
  assert.ok(composer.y + composer.height <= center.y + center.height + 1)
})
test('collapsing the sidebar gives the conversation the full width', async ({ page }) => {
  await boot(page, initialState())
  const center = page.locator('[data-shell-center]')
  const open = await center.boundingBox()
  assert.ok(open !== null)
  assert.ok(open.x > 0, `center starts at ${open.x}`)

  await page.getByRole('button', { name: 'toggle sidebar' }).click()
  await expect.poll(async () => (await center.boundingBox())?.x ?? -1).toBe(0)
  const collapsed = await center.boundingBox()
  const viewport = page.viewportSize()
  assert.ok(collapsed !== null && viewport !== null)
  assert.equal(Math.round(collapsed.width), viewport.width)

  await page.keyboard.press(sidebarKey)
  await expect.poll(async () => (await center.boundingBox())?.x ?? -1).toBe(Math.round(open.x))
})
test('the sidebar lists old zen-owned sessions as ordinary coding rows', async ({ page }) => {
  const row = (path: string, title: string, current: boolean) => ({
    path,
    name: title,
    title,
    project: 'p',
    project_path: '/work/p',
    messages: 1,
    current,
    pinned: false,
    created_at: 1,
    updated_at: current ? 2 : 1,
    model: 'deepseek-flash',
    derived_from: '',
  })
  await boot(
    page,
    initialState({
      title: 'coding chat',
      statusSession: '/sessions/coding.jsonl',
      sessionRows: [
        row('/sessions/coding.jsonl', 'coding chat', true),
        row('/sessions/zen.jsonl', 'zen chat', false),
      ],
    }),
  )

  await expect(page.locator('[data-session-path="/sessions/coding.jsonl"]')).toBeVisible()
  await expect(page.locator('[data-session-path="/sessions/zen.jsonl"]')).toBeVisible()
})
test('the sidebar renders pinned, sessions and collapsible project groups', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-sidebar-section="sessions"]')).toBeVisible()
  await expect(page.locator('[data-project-group="p"]')).toBeVisible()
  const header = page.getByRole('button', { name: '项目 p' })
  await expect(header).toHaveAttribute('aria-expanded', 'true')
  await header.click()
  await expect(header).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toBeHidden()
  await header.click()
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toBeVisible()
})
test('a filtered, collapsed project stays collapsed and loads more on demand', async ({ page }) => {
  await boot(page, initialState({ projectSessions: 12 }))
  const header = page.getByRole('button', { name: '项目 p' })
  const visibleRows = page.locator('[data-sidebar-project-group="p"] [data-session-path]:visible')
  await expect(visibleRows).toHaveCount(10)
  await expect(page.getByRole('button', { name: '加载更多（2）' })).toBeVisible()
  await page.getByRole('button', { name: '加载更多（2）' }).click()
  await expect(visibleRows).toHaveCount(12)

  await header.click()
  await page.getByPlaceholder('搜索会话').fill('额外会话')
  await expect(header).toHaveAttribute('aria-expanded', 'false')
  await expect(visibleRows).toHaveCount(0)
  await page.getByPlaceholder('搜索会话').fill('')
  await expect(header).toHaveAttribute('aria-expanded', 'false')
  await header.click()
  await expect(visibleRows).toHaveCount(12)
})
test('opening another project’s session switches the runtime there with that session', async ({ page }) => {
  await boot(page, initialState())
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'list' && call.scope === 'all'),
      ),
    )
    .toBe(true)
  await expect(page.locator('[data-project-group="q"]')).toBeVisible()
  const cross = page.locator('[data-session-path="/sessions/c.jsonl"]')
  await expect(cross).not.toHaveAttribute('aria-disabled', 'true')
  await cross.click()
  // The resident host takes the request: one project op, no replacement
  // process, no separate resume round-trip for a cold slot.
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return { root: op?.root, resume: op?.resume }
      }),
    )
    .toEqual({ root: '/work/q', resume: '/sessions/c.jsonl' })
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some(
        (call) => call.type === 'session' && call.op === 'resume',
      ),
    ),
    false,
  )
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)

  // A late exit from a pid the shell no longer tracks must not end the run;
  // the tracked pid still does.
  const live = await page.evaluate(() => window.__ZEN_E2E__.starts[0]?.pid ?? 0)
  const emitExit = (pid: number) =>
    page.evaluate(
      (value) =>
        window.__ZEN_E2E_EMIT__('rpc:exit', {
          event: 'rpc:exit',
          id: 1,
          payload: { code: 0, pid: value },
        }),
      pid,
    )
  await emitExit(live + 999)
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.getByText(/会话进程已退出/)).toHaveCount(0)

  await emitExit(live)
  await expect(page.getByText(/会话进程已退出/)).toBeVisible()
})
test('a project discovered from sessions can start its own session', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-project-group="q"]')).toBeVisible()
  await page.getByRole('button', { name: '在 q 中新建会话' }).click()
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return op === undefined
          ? null
          : { root: op.root ?? null, resume: op.resume ?? null }
      }),
    )
    .toEqual({ root: '/work/q', resume: null })
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'new'),
      ),
    )
    .toBe(true)
})
test('the current row menu pins and renames; other rows omit both actions', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '置顶' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'pin' && call.argument === 'on',
        ),
      ),
    )
    .toBe(true)

  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '重命名' }).click()
  const input = page.locator('[data-session-path="/sessions/a.jsonl"] input')
  await input.fill('行内标题')
  await input.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'rename' && call.argument === '行内标题',
        ),
      ),
    )
    .toBe(true)

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '置顶' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: '重命名' })).toHaveCount(0)
})
test('delete needs two clicks on the same row', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '删除' }).click()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'delete'),
    ),
    false,
  )
  // Closing the menu drops the arm: reopen and the label is plain delete again.
  await page.keyboard.press('Escape')
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '删除？' })).toHaveCount(0)
  await page.getByRole('menuitem', { name: '删除' }).click()
  await page.getByRole('menuitem', { name: '删除？' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'delete' && call.path === '/sessions/b.jsonl',
        ),
      ),
    )
    .toBe(true)
})
test('the footer opens a session file and exports a chosen format', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: '/tmp/imported.jsonl' }))
  await page.getByRole('button', { name: '打开会话文件…' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'import' && call.path === '/tmp/imported.jsonl',
        ),
      ),
    )
    .toBe(true)

  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 HTML' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) =>
            call.type === 'session' &&
            call.op === 'export' &&
            call.path === '/sessions/a.jsonl' &&
            call.format === 'html',
        ),
      ),
    )
    .toBe(true)
  await expect(page.getByRole('alert')).toHaveText('已导出到 /sessions/a.html')
})
test('a failed export surfaces in the notice banner', async ({ page }) => {
  await boot(page, initialState({ failExport: true }))
  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 Markdown' }).click()
  // The banner belongs to the composer dock: it must clear the floating plate
  // instead of being painted underneath it at the window's bottom edge.
  await expect(page.locator('[data-shell-composer] [role="alert"]')).toHaveText('导出失败')
  const bannerBox = await page.getByRole('alert').boundingBox()
  const plateBox = await page.locator('[data-composer-plate]').boundingBox()
  assert.ok(bannerBox !== null && plateBox !== null)
  assert.ok(
    bannerBox.y + bannerBox.height <= plateBox.y,
    `banner bottom ${bannerBox.y + bannerBox.height} must sit above the plate top ${plateBox.y}`,
  )
})
test('a cancelled open dialog sends no import', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: null }))
  await page.getByRole('button', { name: '打开会话文件…' }).click()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'import'),
    ),
    false,
  )
})
test('fork is only offered on the current session row', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '从此分叉' })).toHaveCount(1)
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '从此分叉' })).toHaveCount(0)
})
test('escaping a row menu neither cancels the run nor traps the keyboard', async ({ page }) => {
  await boot(page, initialState())
  const trigger = page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]')
  await trigger.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem', { name: '重命名' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: '置顶' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(trigger).toBeFocused()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'cancel'),
    ),
    false,
  )

  await page.getByRole('button', { name: '导出会话' }).click()
  await expect(page.getByRole('menuitem', { name: '导出为 HTML' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'cancel'),
    ),
    false,
  )
})
test('a recorded project root stays openable without a current session', async ({ page }) => {
  await presetState(page, initialState({ noCurrent: true }))
  await page.goto('/')
  await expect(page.locator('[data-session-path="/sessions/a.jsonl"]')).toBeVisible()
  await expect(page.locator('[data-session-path="/sessions/a.jsonl"]')).not.toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).not.toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('[data-session-path="/sessions/c.jsonl"]')).not.toHaveAttribute('aria-disabled', 'true')
})
test('the session and project sections collapse independently and persist', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.getByPlaceholder('搜索会话')).toBeVisible()
  await page.getByRole('button', { name: '折叠会话' }).click()
  await expect(page.getByPlaceholder('搜索会话')).toHaveCount(0)
  await expect(page.locator('[data-sidebar-project-group="p"]')).toBeVisible()
  await page.getByRole('button', { name: '折叠项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="p"]')).toHaveCount(0)

  // With both sections folded the footer owns the leftover space, so the icon
  // row stays on the sidebar's bottom edge instead of riding up under it.
  await expect(page.locator('[data-sidebar-footer]')).toBeVisible()
  const bottomGap = await page.evaluate(() => {
    const sidebar = document.querySelector('[data-shell-sidebar]')!.getBoundingClientRect()
    const footer = document.querySelector('[data-sidebar-footer]')!.getBoundingClientRect()
    return Math.round(sidebar.bottom - footer.bottom)
  })
  expect(Math.abs(bottomGap)).toBeLessThanOrEqual(1)

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.getByPlaceholder('搜索会话')).toHaveCount(0)
  await expect(page.locator('[data-sidebar-project-group="p"]')).toHaveCount(0)

  await page.getByRole('button', { name: '展开会话' }).click()
  await expect(page.getByPlaceholder('搜索会话')).toBeVisible()
  await page.getByRole('button', { name: '展开项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="p"]')).toBeVisible()
})
