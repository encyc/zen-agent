/** A provider request that fails before its first token is retried silently;
 * the transcript has to say so. These tests drive the wire the same way the
 * child does: a `retry` event before each backoff, then a status carrying the
 * friendly run failure once the attempts are spent. */

import { expect, test, type Page } from '@playwright/test'

import { boot, initialState, type BridgeState } from './harness'

async function emitEvent(page: Page, event: Record<string, unknown>) {
  await page.evaluate((payload) => {
    window.__ZEN_E2E_EMIT__('rpc:frame', {
      event: 'rpc:frame',
      id: 1,
      payload: { frame: { type: 'event', event: payload } },
    })
  }, event)
}

function seeded(overrides: Partial<BridgeState> = {}): BridgeState {
  return initialState({
    transcript: [
      { kind: 'user', id: 'u1', text: '再试一次', timestamp: 1 },
      { kind: 'assistant', id: 'v1', text: '好。', timestamp: 2 },
    ],
    ...overrides,
  })
}

test('a retrying request shows progress, then settles as retried', async ({ page }) => {
  await boot(page, seeded())
  await emitEvent(page, {
    type: 'retry_attempt',
    attempt: 1,
    max_retries: 3,
    delay_ms: 500,
    error: 'connection reset',
  })

  const row = page.locator('[data-retry-state]')
  await expect(row).toHaveAttribute('data-retry-state', 'retrying')
  await expect(row).toContainText('正在重试模型请求')
  await expect(row).toContainText('(1/3)')
  await expect(row.locator('[data-retry-wait]')).toHaveText(/\d+(\.\d)?s/)

  // The chevron reveals the swallowed attempt's raw failure.
  await row.locator('[data-retry-toggle]').click()
  await expect(row).toContainText('第 1 次')
  await expect(row).toContainText('connection reset')

  await emitEvent(page, {
    type: 'retry_attempt',
    attempt: 2,
    max_retries: 3,
    delay_ms: 1000,
    error: 'connect timeout',
  })
  await expect(row).toContainText('(2/3)')

  await emitEvent(page, {
    type: 'message_update',
    assistant_event: { type: 'text_delta', delta: '好了' },
  })
  await expect(row).toHaveAttribute('data-retry-state', 'recovered')
  await expect(row).toContainText('已重试模型请求')
  // The answer streams below the row, which stays as history.
  await expect(page.locator('[data-chat-flow]')).toContainText('好了')
})

test('an exhausted retry ends inline, not as an error popup', async ({ page }) => {
  await boot(page, seeded({ running: false }))
  await emitEvent(page, {
    type: 'retry_attempt',
    attempt: 3,
    max_retries: 3,
    delay_ms: 2000,
    error: 'connection reset',
  })
  // The attempts are spent: the child marks the run failed, and a turn
  // boundary reads status — that is where the classified failure lands.
  await page.evaluate(() => {
    window.__ZEN_E2E__.lastError =
      '无法连接模型服务：检查网络或代理后重新发送即可。 ｜ connection reset'
  })
  await emitEvent(page, { type: 'turn_end' })

  const row = page.locator('[data-retry-state]')
  await expect(row).toHaveAttribute('data-retry-state', 'failed')
  await expect(row).toContainText('(3/3)')

  const failure = page.locator('[data-run-failure]')
  await expect(failure).toBeVisible()
  await expect(failure).toContainText('本轮运行失败')
  await expect(failure).toContainText('connection reset')
  // The run failure lives in the transcript; the banner stays for RPC errors.
  await expect(page.getByRole('alert')).toHaveCount(0)
})
