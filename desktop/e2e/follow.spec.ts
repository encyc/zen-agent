/** Follow-tail: leaving the bottom raises the jump pill with the number of user
 * turns that arrived since; returning to the bottom clears it, and a streamed
 * delta at the tail never shows the pill at all. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, emitEvent, initialState } from './harness.ts'

const long = (n: number) =>
  Array.from({ length: n }, (_, i) => [
    { kind: 'user', id: `u${i}`, text: `第 ${i} 轮提问`, timestamp: i * 2 + 1 },
    { kind: 'assistant', id: `a${i}`, text: `回复 ${i}`, timestamp: i * 2 + 2 },
  ]).flat()

/** Scroll away from the tail the way a wheel does: move scrollTop up and let
 * the component's own listener see it. */
async function scrollUp(page: import('@playwright/test').Page, top: number) {
  await page.locator('[data-chat-scroll]').evaluate((el, y) => {
    el.scrollTop = y
    el.dispatchEvent(new Event('scroll'))
  }, top)
}

test('scrolling up raises the pill and counts the user turns that arrive', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState({ running: true, transcript: long(20) }))
  const jump = page.locator('[data-jump]')

  await expect(jump).toHaveCount(0) // 起手在尾部
  await scrollUp(page, 200)
  await expect(jump).toBeVisible()
  await expect(jump).not.toHaveAttribute('data-unseen', /.*/)

  await emitEvent(page, { type: 'message_start', message: { role: 'user', text: '新的一问' } })
  await expect(jump).toHaveAttribute('data-unseen', 'true')
  await expect(jump).toContainText('1')
})

test('returning to the bottom clears the pill and its count', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState({ running: true, transcript: long(20) }))
  const jump = page.locator('[data-jump]')
  await scrollUp(page, 200)
  await expect(jump).toBeVisible()

  await jump.click()
  await expect(jump).toHaveCount(0, { timeout: 3000 })
})

test('a streamed delta at the tail keeps following without showing the pill', async ({ page }) => {
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
