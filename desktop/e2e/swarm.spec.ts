import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, type BridgeState } from './harness'
import { swarmFixture } from '../src/dev/swarmMock'

test('switch to swarm, delegate, and see the chain', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-page]')).toBeVisible()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 查 X')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-message]', { hasText: '查 X' })).toBeVisible()
  // The report lands in the room, and its @ opens the second leg of the chain.
  await expect(page.getByText('结论是 Z')).toBeVisible()
  await expect(page.getByText('复核完毕')).toBeVisible()

  const tasks = page.locator('[data-swarm-task]')
  await expect(tasks).toHaveCount(2)
  await expect(tasks.nth(0)).toContainText('task #1')
  await expect(tasks.nth(0)).toHaveAttribute('data-state', 'done')
  await expect(tasks.nth(1)).toContainText('task #2')
  await expect(tasks.nth(1)).toHaveAttribute('data-state', 'done')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const post = calls.find((call) => call.type === 'swarm' && call.op === 'post')
  assert.equal(post?.text, '@research 查 X')
})

test('cancel a running task', async ({ page }) => {
  const fixture: Partial<BridgeState> = initialState(swarmFixture({ runningTask: true }))
  await boot(page, fixture)
  await page.locator('[data-workspace-mode="swarm"]').click()

  const task = page.locator('[data-swarm-task]')
  await expect(task).toHaveCount(1)
  await expect(task).toHaveAttribute('data-state', 'running')
  await task.locator('[data-swarm-cancel]').click()
  await expect(task).toHaveAttribute('data-state', 'cancelled')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const cancel = calls.find((call) => call.type === 'swarm' && call.op === 'cancel')
  assert.equal(cancel?.taskId, 't_seed')
})
