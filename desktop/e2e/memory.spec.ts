/** The memory tab: listing, filtering, creating, editing and deleting
 * project notes, plus /memory opening the tab from the composer. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState } from './harness.ts'

test('the memory tab lists, filters, creates, edits and deletes project notes', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      memory: [
        {
          id: 'aaaa',
          title: '构建命令',
          body: '用 uv 跑测试，用 ruff 检查。',
          created: '2026-11-05T09:00:00',
          updated: '2026-11-05T09:00:00',
          source: 'agent',
        },
        {
          id: 'bbbb',
          title: '发布流程',
          body: '**打 tag** 后上传安装包。',
          created: '2026-11-06T09:00:00',
          updated: '2026-11-06T09:00:00',
          source: 'ui',
        },
      ],
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '记忆' }).click()
  await expect(panel.getByRole('tab', { name: '记忆' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('2 条记忆')
  await expect(panel.locator('[data-memory-id]').first()).toHaveAttribute('data-memory-id', 'bbbb')
  await expect(panel).toContainText('模型')
  await expect(panel).toContainText('界面')

  const search = panel.getByRole('textbox', { name: '搜索标题或正文' })
  await search.fill('ruff')
  await expect(panel.locator('[data-memory-id]')).toHaveCount(1)
  await expect(panel.locator('[data-memory-id="aaaa"]')).toBeVisible()
  await search.fill('')

  await panel.getByRole('button', { name: '新建' }).click()
  await panel.getByRole('textbox', { name: '标题' }).fill('部署备注')
  await panel.getByRole('textbox', { name: '正文' }).fill('发布前先跑全量门禁。')
  await panel.getByRole('button', { name: '保存' }).click()
  await expect(panel).toContainText('3 条记忆')
  await expect(panel.locator('[data-memory-id="c003"]')).toContainText('部署备注')

  await panel.locator('[data-memory-id="bbbb"]').click()
  await expect(panel.locator('[data-memory-markdown]')).toContainText('打 tag 后上传安装包。')
  await expect(panel.locator('[data-memory-markdown] strong')).toContainText('打 tag')
  await panel.getByRole('button', { name: '编辑记忆' }).click()
  const title = panel.getByRole('textbox', { name: '标题' })
  await expect(title).toHaveValue('发布流程')
  await expect(panel.getByRole('textbox', { name: '正文' })).toHaveValue('**打 tag** 后上传安装包。')
  await title.fill('发布流程 v2')
  await panel.getByRole('button', { name: '保存' }).click()
  await expect(panel.locator('[data-memory-id="bbbb"]')).toContainText('发布流程 v2')

  await panel.locator('[data-memory-id="aaaa"]').click()
  await expect(panel.locator('[data-memory-markdown]')).toContainText('用 uv 跑测试')
  await panel.getByRole('button', { name: '删除记忆' }).click()
  await expect(panel.locator('[data-memory-confirm]')).toBeVisible()
  await panel.getByRole('button', { name: '删除', exact: true }).click()
  await expect(panel.locator('[data-memory-id="aaaa"]')).toHaveCount(0)
  await expect(panel).toContainText('2 条记忆')

  const ops = await page.evaluate(() =>
    window.__ZEN_E2E__.calls
      .filter((call) => call.type === 'memory')
      .map((call) => call.op),
  )
  assert.equal(ops[0], 'list')
  assert.deepEqual(ops.slice(-3), ['create', 'update', 'delete'])

  await page.evaluate(() => {
    window.__ZEN_E2E__.memory = [
      {
        id: 'dddd',
        title: '外部写入',
        body: '模型在面板打开时写入。',
        created: '2026-11-07T12:00:00',
        updated: '2026-11-07T12:00:00',
        source: 'agent',
      },
    ]
  })
  await panel.getByRole('button', { name: '刷新记忆' }).click()
  await expect(panel.locator('[data-memory-id="dddd"]')).toContainText('外部写入')
})
test('submitting /memory opens the memory tab', async ({ page }) => {
  await boot(page, initialState({ running: false, memory: [] }))
  const input = page.locator('[data-composer-plate] textarea')
  await input.fill('/memory')
  await input.press('Enter')
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '记忆' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('这个项目还没有记忆')
})
