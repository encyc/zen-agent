/** Visual harness for the Taiji dispatch board: renders the real component
 * with a rich mocked board and screenshots it for design critique. Keeps no
 * baseline; the PNG lands in test-results/ (git-ignored). */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'

import { boot, initialState } from './harness'

const now = () => Math.floor(Date.now() / 1000)

const richBoard = {
  active: true,
  router_busy: true,
  slots: { busy: 2, max: 3, queued: 1 },
  workers: [
    {
      name: 'auth',
      branch: 'task/auth',
      base: 'main',
      status: 'ok',
      summary: '旧 token 兼容方案的取舍',
      session: '/s/auth.jsonl',
      created_at: now() - 1873,
      updated_at: now() - 42,
      attention: {
        question: '登录接口要兼容旧 token 格式吗？老格式下季度就下线了。',
        options: ['兼容旧版', '不兼容'],
      },
      diffstat: null,
    },
    {
      name: 'ui',
      branch: 'task/ui',
      base: 'main',
      status: 'ok',
      summary: '重做了侧栏折叠动画，spring 曲线对齐设计稿',
      session: '/s/ui.jsonl',
      created_at: now() - 5400,
      updated_at: now() - 300,
      attention: null,
      diffstat: [
        { path: 'src/ui/sidebar.ts', adds: 142, dels: 18 },
        { path: 'src/ui/sidebar.module.css', adds: 55, dels: 12 },
        { path: 'src/ui/index.ts', adds: 2, dels: 0 },
      ],
    },
    {
      name: 'api',
      branch: 'task/api',
      base: 'main',
      status: 'running',
      summary: '在写重定向逻辑，还差重试分支',
      session: '',
      created_at: now() - 753,
      updated_at: now(),
      attention: null,
      diffstat: null,
    },
    {
      name: 'refactor',
      branch: 'task/refactor',
      base: 'main',
      status: 'queued',
      summary: '',
      session: '',
      created_at: now() - 15,
      updated_at: now() - 15,
      attention: null,
      diffstat: null,
    },
    {
      name: 'explore',
      branch: 'task/explore',
      base: 'main',
      status: 'merged',
      summary: '独立 job 探索已并入主干',
      session: '',
      created_at: now() - 36000,
      updated_at: now() - 35000,
      attention: null,
      diffstat: null,
    },
    {
      name: 'flaky',
      branch: 'task/flaky',
      base: 'main',
      status: 'error',
      summary: 'e2e 第三次超时，定位到 mock 时钟',
      session: '/s/flaky.jsonl',
      created_at: now() - 1800,
      updated_at: now() - 600,
      attention: null,
      diffstat: null,
    },
  ],
}

const openBoard = async (page: import('@playwright/test').Page) => {
  await boot(page, initialState({ taiji: richBoard }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '太极' }).click()
  await expect(panel.getByRole('tab', { name: '太极' })).toHaveAttribute('aria-selected', 'true')
  return panel
}

test('taiji board visual state', async ({ page }) => {
  const panel = await openBoard(page)
  await expect(panel.getByText('等你回答')).toBeVisible()
  await page.waitForTimeout(250)
  await panel.screenshot({ path: 'test-results/taiji-board.png' })
})

test('an attention question answers through the board, not the composer', async ({ page }) => {
  const panel = await openBoard(page)

  await expect(panel.getByText('登录接口要兼容旧 token 格式吗')).toBeVisible()
  await panel.getByRole('button', { name: '兼容旧版' }).click()

  await expect(page.getByRole('alert')).toContainText('已投递')
  assert.deepEqual(
    await page.evaluate(() => window.__ZEN_E2E__.taijiRequests?.slice(-1)),
    [{ op: 'message', name: 'auth', text: '答复：兼容旧版' }],
  )
})
