/** First launch, branding and the update surface: the home hero, the
 * about page's wordmark and update button, sidebar notifications and
 * the running-app mark that rotates while a turn streams. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, presetState } from './harness.ts'

test('a returning user lands on the home hero and auto-connects', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work'))
  await presetState(page, initialState())
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: /ZenAgent/ })).toBeVisible()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.locator('[data-home-hero]')).toBeVisible()
})
test('the home hero shows the padded app icon', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work'))
  await presetState(page, initialState())
  await page.goto('/')
  const logo = page.locator('[data-home-hero] [data-home-logo]')
  await expect(logo).toBeVisible()
  const metrics = await logo.evaluate(async (element) => {
    const img = element as HTMLImageElement
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const context = canvas.getContext('2d')!
    context.drawImage(img, 0, 0)
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height)
    let left = canvas.width
    let top = canvas.height
    let right = -1
    let bottom = -1
    for (let y = 0; y < canvas.height; y += 1) {
      for (let x = 0; x < canvas.width; x += 1) {
        if (data[(y * canvas.width + x) * 4 + 3] < 8) continue
        if (x < left) left = x
        if (x > right) right = x
        if (y < top) top = y
        if (y > bottom) bottom = y
      }
    }
    return {
      width: canvas.width,
      height: canvas.height,
      left,
      top,
      right,
      bottom,
      cornerAlpha: data[3],
    }
  })
  assert.equal(metrics.width, metrics.height)
  assert.equal(metrics.cornerAlpha, 0)
  const widthRatio = (metrics.right - metrics.left + 1) / metrics.width
  const heightRatio = (metrics.bottom - metrics.top + 1) / metrics.height
  // macOS icons keep the artwork inside an 824px tile of a 1024px canvas.
  assert.ok(widthRatio > 0.76 && widthRatio < 0.85, `width ratio ${widthRatio}`)
  assert.ok(heightRatio > 0.76 && heightRatio < 0.85, `height ratio ${heightRatio}`)
})
test('the about page carries the ZEN wordmark', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '信息' }).click()
  const mark = page.locator('[data-page="settings"] [data-zen-wordmark]')
  await expect(mark).toBeVisible()
  await expect(mark).toHaveAttribute('role', 'img')
  await expect(mark).toHaveAttribute('aria-label', 'ZEN')
})
test('the about page offers an update button beside the manual check', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      'zen.update.cache',
      JSON.stringify({ checkedAt: Date.now(), latest: '99.0.0', notified: null }),
    )
  })
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '信息' }).click()
  const about = page.locator('[data-page="settings"]')
  await expect(about.getByRole('button', { name: '检查更新' })).toBeVisible()
  await expect(about.getByRole('button', { name: '更新', exact: true })).toBeEnabled()
})
test('the about update button stays disabled without a newer release', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      'zen.update.cache',
      JSON.stringify({ checkedAt: Date.now(), latest: '0.0.1', notified: null }),
    )
  })
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '信息' }).click()
  await expect(
    page.locator('[data-page="settings"]').getByRole('button', { name: '更新', exact: true }),
  ).toBeDisabled()
})
test('the about update button waits while a turn is running', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      'zen.update.cache',
      JSON.stringify({ checkedAt: Date.now(), latest: '99.0.0', notified: null }),
    )
  })
  await boot(
    page,
    initialState({
      running: true,
      transcript: [{ kind: 'user', id: 'u1', text: 'go', timestamp: 1 }],
    }),
  )
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '信息' }).click()
  await expect(
    page.locator('[data-page="settings"]').getByRole('button', { name: '更新', exact: true }),
  ).toBeDisabled()
})
test('the sidebar brand keeps the ZEN wordmark in the DOM', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  // The brand row is hidden on macOS; on other platforms the same mark shows.
  await expect(page.locator('[data-shell-sidebar] [data-zen-wordmark]')).toHaveCount(1)
})
test('a waiting release lives in the sidebar notifications, not above the chat', async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.locale', JSON.stringify('en'))
    localStorage.setItem(
      'zen.update.cache',
      JSON.stringify({ checkedAt: Date.now(), latest: '99.0.0', notified: null }),
    )
  })
  await boot(page, initialState({ running: false }))

  const bell = page.getByRole('button', { name: 'Notifications' })
  await expect(page.getByTestId('notification-dot')).toBeVisible()
  await expect(page.getByText('ZenAgent 99.0.0 is available')).toHaveCount(0)

  await bell.click()
  const notice = page.locator('[data-update-notice="available"]')
  await expect(notice).toBeVisible()
  await expect(notice.getByText('ZenAgent 99.0.0 is available')).toBeVisible()
  await expect(notice.getByRole('button', { name: 'Update and restart' })).toBeVisible()

  // Dismissing clears both the card and the bell badge.
  await notice.getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.locator('[data-update-notice]')).toHaveCount(0)
  await expect(page.getByTestId('notification-dot')).toHaveCount(0)
})
test('a running turn rotates the app mark', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: true,
      transcript: [{ kind: 'user', id: 'u1', text: 'go', timestamp: 1 }],
    }),
  )
  const mark = page.locator('[data-chat-flow] [data-running-logo]')
  await expect(mark).toBeVisible()
  const spin = await mark.evaluate((element) => {
    const style = getComputedStyle(element)
    return { name: style.animationName, iterations: style.animationIterationCount }
  })
  assert.notEqual(spin.name, 'none')
  assert.equal(spin.iterations, 'infinite')
  await expect(page.locator('[data-shell-sidebar] [data-running-logo]')).toBeVisible()

  await page.emulateMedia({ reducedMotion: 'reduce' })
  assert.equal(
    await mark.evaluate((element) => getComputedStyle(element).animationName),
    'none',
  )
})
test('an idle session shows no running mark', async ({ page }) => {
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
  await expect(page.locator('[data-running-logo]')).toHaveCount(0)
})
test('a running tool turn rotates the mark in the process header', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: true,
      transcript: [
        { kind: 'user', id: 'u1', text: 'go', timestamp: 1 },
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'read_file',
          args_preview: '{"path":"/work/a.ts"}',
          status: 'ok',
          result_text: 'export {}',
          duration_ms: 20,
        },
      ],
    }),
  )
  const mark = page.locator('[data-turn-process] [data-running-logo]')
  await expect(mark).toBeVisible()
  await expect(page.locator('[data-chat-flow] [data-running-logo]')).toHaveCount(1)
})
test('a first launch goes straight into the chat shell', async ({ page }) => {
  await presetState(page, initialState())
  await page.goto('/')
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.starts[0]?.cwd))
    .toBe('/Users/e2e')
  // The shell boots hidden; reaching the ready tree must reveal the window.
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.showCalls ?? 0))
    .toBe(1)
})
test('a shell that never reaches ready still reveals its window', async ({ page }) => {
  // `rpc_start` never settles, so the ready tree (and its show) never runs.
  // The unconditional boot fail-safe must reveal the window anyway.
  await presetState(page, initialState({ startHangs: true }))
  await page.goto('/')
  await expect(page.locator('[data-topbar-title]')).toHaveCount(0)
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.showCalls ?? 0), { timeout: 5000 })
    .toBeGreaterThanOrEqual(1)
})
