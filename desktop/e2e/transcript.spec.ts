/** The transcript: the topbar crumb with inline rename, settled tool
 * steps expanded by default, the bounded window over long transcripts,
 * the width handles with their minimap, and undo/compact from the row
 * menu. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness.ts'

test('the topbar shows the project crumb and renames the current session inline', async ({
  page,
}) => {
  await boot(page, initialState())
  await expect(page.locator('[data-topbar-project]')).toContainText('p')
  await expect(page.locator('[data-topbar-project] svg')).toBeVisible()
  await expect(page.locator('[data-topbar-sep]')).toHaveText('/')
  await page.getByRole('button', { name: '重命名会话标题' }).click()
  const input = page.getByRole('textbox', { name: '会话标题' })
  await input.fill('新的标题')
  await input.press('Enter')
  await expect(page.locator('[data-topbar-title]')).toHaveText('新的标题')
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'rename' && call.argument === '新的标题',
        ),
      ),
    )
    .toBe(true)
})
test('a settled turn keeps its tool steps expanded by default', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'go', timestamp: 1 },
        { kind: 'assistant', id: 'a1', text: 'done', timestamp: 2, duration_ms: 900 },
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'read_file',
          args_preview: '{"path":"a.ts"}',
          args: { path: 'a.ts' },
          status: 'ok',
          result_text: 'file',
          duration_ms: 400,
        },
        {
          id: 't2',
          kind: 'tool',
          call_id: 'c2',
          name: 'bash',
          args_preview: '{"command":"ls"}',
          args: { command: 'ls' },
          status: 'ok',
          result_text: 'a.ts',
          duration_ms: 600,
        },
      ],
    }),
  )
  const fold = page.getByRole('button', { name: /已处理.*步骤/ })
  await expect(fold).toBeVisible()
  await expect(fold).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('read_file')).toBeVisible()
  await fold.click()
  await expect(fold).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByText('read_file')).toHaveCount(0)
})
test('a long transcript mounts a bounded window and reveals earlier turns on demand', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 150 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮提问`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: `回复 ${index}`, timestamp: index * 2 + 2 },
  ]).flat()
  await boot(page, initialState({ running: false, transcript }))

  const rows = page.locator('[data-chat-flow] [data-minimap-id]')
  const earlier = page.locator('[data-earlier]')
  const dashes = page.locator('[data-minimap-dash]')
  await expect(rows).toHaveCount(60)
  // The rail covers the whole session, including turns not yet mounted.
  await expect(dashes).toHaveCount(150)
  await expect(earlier).toHaveText('显示更早的 240 条')
  // The trailing window keeps the newest turn and drops the oldest rows.
  await expect(page.locator('[data-minimap-id="a149"]')).toHaveCount(1)
  await expect(page.locator('[data-minimap-id="u0"]')).toHaveCount(0)

  await earlier.click()
  await expect(rows).toHaveCount(100)
  await expect(earlier).toHaveText('显示更早的 200 条')

  // Clicking a dash above the mounted window mounts everything down to it.
  await dashes.first().click()
  await expect(rows).toHaveCount(300)
  await expect(page.locator('[data-minimap-id="u0"]')).toHaveCount(1)
})
test('the width handles resize the band and the minimap marks each turn', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'go', timestamp: 1 },
        { kind: 'assistant', id: 'a1', text: 'done', timestamp: 2 },
      ],
    }),
  )
  await expect(page.locator('[data-minimap-dash]').first()).toBeVisible()
  // Only the user's own turns get a dash; the assistant reply does not.
  await expect(page.locator('[data-minimap-dash]')).toHaveCount(1)
  const flow = page.locator('[data-chat-flow]')
  const before = await flow.evaluate((el) => el.getBoundingClientRect().width)
  assert.equal(Math.round(before), 760)
  const handle = page.locator('[data-chat-width-handles] [data-side="right"]')
  const box = await handle.boundingBox()
  assert.ok(box !== null)
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 - 60, box.y + box.height / 2)
  await page.mouse.up()
  const after = await flow.evaluate((el) => el.getBoundingClientRect().width)
  assert.ok(after < before)
  await expect(handle).toHaveAttribute('aria-valuenow', String(Math.round(after)))
})
test('the current row menu runs undo and compact through the session op', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '撤销上一轮' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'undo'),
      ),
    )
    .toBe(true)
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '压缩上下文' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'compact'),
      ),
    )
    .toBe(true)
})
