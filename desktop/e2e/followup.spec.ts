/** The kernel follow-up lane: while a turn runs, Ctrl/Cmd+Enter extends it
 * instead of steering, and the composer dock reports what is queued. */

import { expect, test, type Page } from '@playwright/test'

import { boot, initialState } from './harness'

const followKey = process.platform === 'darwin' ? 'Meta+Enter' : 'Control+Enter'

test('the follow-up key continues the running turn and shows the dock', async ({ page }) => {
  await boot(page, initialState({ running: true }))

  const input = page.locator('[data-composer-plate] textarea')
  // the busy placeholder is the discovery surface for the shortcut
  await expect(input).toHaveAttribute('placeholder', /Ctrl\+Enter/)

  await input.fill('顺便把测试补上')
  await input.press(followKey)

  await expect(input).toHaveValue('')
  const dock = page.locator('[data-continuing-dock]')
  await expect(dock).toBeVisible()
  await expect(dock).toContainText('1')

  // the frame carried the text (the bridge records every request)
  const sent = await page.evaluate(() =>
    (window.__ZEN_E2E__.calls as { type: string; text?: string }[]).filter(
      (call) => call.type === 'follow_up',
    ),
  )
  expect(sent).toHaveLength(1)
  expect(sent[0]?.text).toBe('顺便把测试补上')
})

test('a follow-up is not the next-run queue', async ({ page }) => {
  await boot(page, initialState({ running: true }))

  const input = page.locator('[data-composer-plate] textarea')
  await input.fill('继续')
  await input.press(followKey)

  await expect(page.locator('[data-continuing-dock]')).toBeVisible()
  // the waiting queue dock is the other lane and stays empty
  await expect(page.locator('[data-lane="queue"]')).toHaveCount(0)
})

interface PendingRow {
  id: number
  lane: 'queue' | 'steer'
  text: string
  images: number
  created_at: number
}

async function setPending(page: Page, items: PendingRow[]) {
  await page.evaluate((pending) => {
    window.__ZEN_E2E__.pending = pending as never
  }, items)
}

test('the composer offers a visible continue button while running', async ({ page }) => {
  await boot(page, initialState({ running: true }))
  const input = page.locator('[data-composer-plate] textarea')
  await input.fill('然后补测试')

  const button = page.getByRole('button', { name: '接续' })
  await expect(button).toBeVisible()
  await button.click()

  await expect(input).toHaveValue('')
  await expect(page.locator('[data-continuing-dock]')).toContainText('1')
})

test('a steering row can be taken back into the composer', async ({ page }) => {
  await boot(page, initialState({ running: true }))
  await setPending(page, [
    { id: 21, lane: 'steer', text: '停，先看日志', images: 0, created_at: 5 },
  ])
  const group = page.locator('[data-lane="steer"]')
  await expect(group).toContainText('停，先看日志', { timeout: 10_000 })

  await group.getByRole('button', { name: '取回' }).click()
  await expect(page.locator('[data-composer-plate] textarea')).toHaveValue('停，先看日志')
  await expect(group).toHaveCount(0)
})

test('the continuation row can be cancelled on its own', async ({ page }) => {
  await boot(page, initialState({ running: true }))
  const input = page.locator('[data-composer-plate] textarea')
  await input.fill('继续')
  await input.press(process.platform === 'darwin' ? 'Meta+Enter' : 'Control+Enter')

  const dock = page.locator('[data-continuing-dock]')
  await expect(dock).toContainText('1')
  await dock.getByRole('button', { name: '清空全部' }).click()
  await expect(dock).toHaveCount(0)
})
