/** Exit phases are observable as DOM lifetime: the node survives its close
 * click, then leaves after the leaving window. No computed-style assertions —
 * a transition's getComputedStyle changes every frame. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, paletteKey } from './harness.ts'

test('the command palette survives its close and stops taking clicks while leaving', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const palette = page.locator('[data-palette]')
  await expect(palette).toHaveAttribute('data-state', 'open')

  await page.keyboard.press('Escape')
  await expect(palette).toHaveAttribute('data-state', 'closed')
  await expect(palette).toHaveCount(1) // 退场中仍在 DOM

  // 退场期间遮罩不吃点击：底下的 textarea 仍应拿到焦点
  await page.locator('[data-composer-plate] textarea').click()
  await expect(page.locator('[data-composer-plate] textarea')).toBeFocused()

  await expect(palette).toHaveCount(0, { timeout: 2000 }) // 一次 leaving 窗口后卸载
})

test('the notifications popover keeps its node through the exit phase', async ({ page }) => {
  await boot(page, initialState())
  const bell = page.getByRole('button', { name: '通知' })
  await bell.click()
  const popover = page.locator('[data-notifications-popover]')
  await expect(popover).toHaveAttribute('data-state', 'open')

  await bell.click()
  await expect(popover).toHaveAttribute('data-state', 'closed')
  await expect(popover).toHaveCount(1)
  await expect(popover).toHaveCount(0, { timeout: 2000 })
  assert.ok(true)
})
