import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness'

test('entering zen shows the router roster and opens a worker drawer', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-workspace-mode="zen"]').click()

  await expect(page.locator('[data-zen-roster]')).toBeVisible()
  await expect(page.locator('[data-zen-router]')).toBeVisible()
  const worker = page.locator('[data-worker-row]').first()
  await expect(worker).toBeVisible()

  await worker.click()
  const drawer = page.locator('[data-zen-drawer-body]')
  await expect(drawer).toBeVisible()
  // The read-only transcript comes from zen_fleet op=transcript.
  await expect(drawer).toContainText('worker 报告')
})

test('stopping the fleet shows the paused banner; resume re-enables', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-workspace-mode="zen"]').click()
  await expect(page.locator('[data-worker-row]').first()).toBeVisible()

  await page.locator('[data-zen-stop]').click()
  await expect(page.locator('[data-zen-paused]')).toBeVisible()
  await expect(page.locator('[data-worker-row]')).toHaveCount(0)

  await page.locator('[data-zen-resume]').click()
  await expect(page.locator('[data-worker-row]').first()).toBeVisible()
  await expect(page.locator('[data-zen-paused]')).toHaveCount(0)
})

test('a busy-slot refusal surfaces a notice and reverts the mode', async ({ page }) => {
  await boot(page, initialState({ zenBusy: true }))
  await page.locator('[data-workspace-mode="zen"]').click()

  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.locator('[data-workspace-mode="coding"]')).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-zen-roster]')).toHaveCount(0)
})

test('zen sidebar switches project through the compact switcher', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/p')
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

test('the zen tab shows a fleet dot while the fleet is enabled', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-zen-active]')).toHaveCount(0)
  await page.locator('[data-workspace-mode="zen"]').click()
  await expect(page.locator('[data-zen-active]')).toBeVisible()
  await page.locator('[data-zen-stop]').click()
  await expect(page.locator('[data-zen-active]')).toHaveCount(0)
})