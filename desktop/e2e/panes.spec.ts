/** Multi-session panes in coding mode: open a second session in split from
 * the sidebar, watch both panes stream, submit into a pane, and close back to
 * the single-view layout. The pop-out is Tauri-only, so in the browser it
 * must fall back to a notice and keep the pane. */

import { expect, test } from '@playwright/test'
import { boot, emitEvent, initialState, presetState, type BridgeState } from './harness.ts'

test('two sessions sit side by side after open-in-split', async ({ page }) => {
  await boot(page, initialState())

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.locator('[data-open-split]').click()

  const strip = page.locator('[data-pane-strip]')
  await expect(strip).toBeVisible()
  await expect(strip.locator('[data-pane]')).toHaveCount(2)
  await expect(page.locator('[data-pane="/sessions/a.jsonl"]')).toBeVisible()
  await expect(page.locator('[data-pane="/sessions/b.jsonl"]')).toBeVisible()
})

test('focusing a pane resumes its session and its composer addresses it', async ({ page }) => {
  // Idle, so Enter in the dock composer submits a prompt (busy steers).
  const state: BridgeState = initialState({ running: false })
  await presetState(page, state)
  await page.goto('/')
  await expect(page.locator('[data-topbar-title]')).toHaveText(state.title)

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.locator('[data-open-split]').click()
  await expect(page.locator('[data-pane-strip]')).toBeVisible()

  // Click the second pane: it becomes the current session (a resume goes out).
  await page.locator('[data-pane="/sessions/b.jsonl"] [data-pane-header]').click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window.__ZEN_E2E__.calls as { type: string; op?: string; path?: string }[]).some(
          (call) => call.type === 'session' && call.op === 'resume' && call.path === '/sessions/b.jsonl',
        ),
      ),
    )
    .toBe(true)

  // The focused pane carries the full dock composer; typing there addresses
  // the pane's session (the current session), not the other one.
  const composer = page.locator('[data-pane="/sessions/b.jsonl"] [data-composer-plate] textarea')
  await composer.fill('查看这条会话')
  await composer.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window.__ZEN_E2E__.calls as { type: string; session?: string; text?: string }[]).some(
          (call) => call.type === 'prompt' && call.session === '/sessions/b.jsonl' && call.text === '查看这条会话',
        ),
      ),
    )
    .toBe(true)

  // A live event tagged with the pane's session paints inside the pane only.
  // The fake host's frames are untagged by project (v2 shape); the session
  // tag alone is what routes them into the pane.
  await page.evaluate(() => {
    window.__ZEN_E2E_EMIT__('rpc:frame', {
      event: 'rpc:frame',
      id: 1,
      payload: {
        frame: {
          type: 'event',
          session: '/sessions/b.jsonl',
          event: { type: 'message_start', message: { role: 'user', text: 'pane b 的行' } },
        },
      },
    })
  })
  const paneB = page.locator('[data-pane="/sessions/b.jsonl"]')
  await expect(paneB.getByText('pane b 的行')).toBeVisible()
  const paneA = page.locator('[data-pane="/sessions/a.jsonl"]')
  await expect(paneA.getByText('pane b 的行')).toHaveCount(0)
})

test('closing panes returns the single-view layout', async ({ page }) => {
  await boot(page, initialState())

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.locator('[data-open-split]').click()
  await expect(page.locator('[data-pane-strip]')).toBeVisible()

  await page.locator('[data-pane="/sessions/b.jsonl"] [data-pane-close]').click()
  await expect(page.locator('[data-pane-strip]')).toHaveCount(0)
  // The current session never left: the single view shows it again.
  await expect(page.locator('[data-topbar-title]')).toHaveText('修复登录超时')
})

test('the pop-out falls back to a notice in the browser and keeps the pane', async ({ page }) => {
  await boot(page, initialState())

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.locator('[data-open-split]').click()
  await expect(page.locator('[data-pane-strip]')).toBeVisible()

  await page.locator('[data-pane="/sessions/b.jsonl"] [data-pane-popout]').click()

  // The failure surfaces through the notification feed (the banner lives in
  // the dock, which the strip replaces) and the pane stays.
  await expect(page.getByTestId('notification-dot')).toBeVisible()
  await expect(page.locator('[data-pane="/sessions/b.jsonl"]')).toBeVisible()
})

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
