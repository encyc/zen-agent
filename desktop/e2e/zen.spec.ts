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