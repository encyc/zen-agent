import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness'

test('plugins: install from a path, configure, persist and uninstall', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '插件' }).click()

  await expect(page.getByRole('heading', { name: '官方连接器' })).toBeVisible()
  for (const id of ['zen.dingtalk', 'zen.feishu', 'zen.wecom']) {
    await expect(page.locator(`[data-plugin="${id}"]`)).toBeVisible()
  }

  await page.locator('#plugin-path').fill('/tmp/demo-plugin')
  await page.getByRole('button', { name: '从路径安装' }).click()

  const demo = page.locator('[data-plugin="demo.local"]')
  await expect(demo).toHaveAttribute('data-status', 'needs_credentials')
  await expect(demo).toContainText('待配置')

  // A freshly installed plugin that still lacks credentials opens its drawer.
  const drawer = page.locator('[data-plugin-drawer="demo.local"]')
  await expect(drawer).toBeVisible()
  await drawer.getByLabel('Demo text').fill('hi')
  await drawer.getByRole('button', { name: '保存' }).click()

  await expect(page.locator('[data-plugin-drawer]')).toHaveCount(0)
  await expect(demo).toHaveAttribute('data-status', 'connected')
  await expect(demo).toContainText('已连接')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const configure = calls.find((call) => call.type === 'plugins' && call.op === 'configure')
  assert.equal(configure?.plugin, 'demo.local')
  assert.equal((configure?.values as Record<string, string>)?.DEMO_TEXT, 'hi')

  // Closing and reopening settings re-reads the host, which kept the plugin.
  await page.getByRole('button', { name: '返回' }).click()
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '插件' }).click()
  await expect(page.locator('[data-plugin="demo.local"]')).toHaveAttribute(
    'data-status',
    'connected',
  )

  await page
    .locator('[data-plugin="demo.local"]')
    .getByRole('button', { name: '卸载' })
    .click()
  await expect(page.locator('[data-plugin="demo.local"]')).toContainText(
    '将删除该插件的凭据与启停状态。',
  )
  await page
    .locator('[data-plugin="demo.local"]')
    .getByRole('button', { name: '确认卸载' })
    .click()
  await expect(page.locator('[data-plugin="demo.local"]')).toHaveCount(0)
  await expect(page.getByText('还没有安装插件')).toBeVisible()
})
