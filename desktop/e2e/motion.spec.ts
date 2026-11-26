/** Exit phases are observable as DOM lifetime: the node survives its close
 * click, then leaves after the leaving window. No computed-style assertions —
 * a transition's getComputedStyle changes every frame. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, emitEvent, initialState, paletteKey } from './harness.ts'

test('the command palette survives its close and stops taking clicks while leaving', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const palette = page.locator('[data-palette]')
  await expect(palette).toHaveAttribute('data-state', 'open')

  await page.evaluate(() => ((window as any).__pal = []))
  await page.keyboard.press('Escape')
  try {
    await expect(palette).toHaveAttribute('data-state', 'closed', { timeout: 1500 })
  } catch (error) {
    console.log('RENDERS', JSON.stringify(await page.evaluate(() => (window as any).__pal)))
    console.log('PRESENCE', JSON.stringify(await page.evaluate(() => (window as any).__presence)))
    console.log('VIS', await page.evaluate(() => `${document.visibilityState} hidden=${String(document.hidden)}`))
    console.log('ACTIVE', await page.evaluate(() => `${document.activeElement?.tagName}/${document.activeElement?.className}`))
    throw error
  }
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

  await expect(panel).toHaveCount(0, { timeout: 2000 })
})

test('the retry details expand and collapse through a state attribute', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: '再试一次', timestamp: 1 },
        { kind: 'assistant', id: 'v1', text: '好。', timestamp: 2 },
      ],
    }),
  )
  await emitEvent(page, {
    type: 'retry_attempt',
    attempt: 1,
    max_retries: 3,
    delay_ms: 500,
    error: 'connection reset',
  })
  // A row whose details were never opened keeps no DOM at all: the presence
  // window only exists once something has to animate out.
  const details = page.locator('[data-retry-details]')
  await expect(details).toHaveCount(0)
  await page.locator('[data-retry-toggle]').click()
  await expect(details).toHaveAttribute('data-state', 'open')
  await expect(details).toContainText('第 1 次')
  await page.locator('[data-retry-toggle]').click()
  await expect(details).toHaveAttribute('data-state', 'closed')
  await expect(details).toHaveCount(1) // 折叠后仍挂载（尺寸过渡跑完再卸）
  await expect(details).toHaveCount(0, { timeout: 2000 })
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
  await expect(banner).toHaveCount(0, { timeout: 2000 })
})

test('a session held by another runtime explains itself instead of pointing at a log', async ({ page }) => {
  // The server's frame carries the concrete cause; the notice must say so.
  // Before, `session_lease_busy` fell through to the generic "see the server
  // log" copy and sent the reader somewhere with nothing in it.
  await boot(page, initialState())
  // A refusal must arrive as a REPLY (`type: 'error'`), not as an event: the
  // router's error branch is on handleResultFrame. The event wrapper used by
  // the other specs nests it under `frame.event`, which routes elsewhere.
  await page.evaluate(() => {
    window.__ZEN_E2E_EMIT__('rpc:frame', {
      event: 'rpc:frame',
      id: 1,
      payload: {
        frame: {
          type: 'error',
          code: 'session_lease_busy',
          message: 'session is already open by another runtime',
          params: { op: 'resume' },
        },
      },
    })
  })
  const banner = page.getByRole('alert')
  await expect(banner).toContainText('另一个 ZenAgent 实例')
  await expect(banner).not.toContainText('日志')
})
