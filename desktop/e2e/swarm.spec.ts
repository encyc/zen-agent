import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, type BridgeState } from './harness'
import { swarmFixture } from '../src/dev/swarmMock'

test('switch to swarm, delegate, and see the chain', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-page]')).toBeVisible()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 查 X')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-message]', { hasText: '查 X' })).toBeVisible()
  // The report lands in the room, and its @ opens the second leg of the chain.
  await expect(page.getByText('结论是 Z')).toBeVisible()
  await expect(page.getByText('复核完毕')).toBeVisible()

  const tasks = page.locator('[data-swarm-task]')
  await expect(tasks).toHaveCount(2)
  await expect(tasks.nth(0)).toContainText('task #1')
  await expect(tasks.nth(0)).toHaveAttribute('data-state', 'done')
  await expect(tasks.nth(1)).toContainText('task #2')
  await expect(tasks.nth(1)).toHaveAttribute('data-state', 'done')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const post = calls.find((call) => call.type === 'swarm' && call.op === 'post')
  assert.equal(post?.text, '@research 查 X')
})

test('cancel a running task', async ({ page }) => {
  const fixture: Partial<BridgeState> = initialState(swarmFixture({ runningTask: true }))
  await boot(page, fixture)
  await page.locator('[data-workspace-mode="swarm"]').click()

  const task = page.locator('[data-swarm-task]')
  await expect(task).toHaveCount(1)
  await expect(task).toHaveAttribute('data-state', 'running')
  await task.locator('[data-swarm-cancel]').click()
  await expect(task).toHaveAttribute('data-state', 'cancelled')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const cancel = calls.find((call) => call.type === 'swarm' && call.op === 'cancel')
  assert.equal(cancel?.taskId, 't_seed')
})

test('create a role from settings', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '角色' }).click()
  await page.getByRole('button', { name: '新建角色' }).click()

  await page.locator('#agent-id').fill('reviewer')
  await page.locator('#agent-name').fill('Reviewer')
  await page.locator('#agent-description').fill('审查改动')
  await page.locator('#agent-prompt').fill('You review code.')
  await page.locator('#agent-root').fill('/tmp/reviewer')
  await page.getByRole('button', { name: '保存' }).click()

  await expect(page.locator('[data-agent-role="reviewer"]')).toBeVisible()
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const save = calls.find(
    (call) => call.type === 'swarm' && call.op === 'roles' && call.action === 'create',
  )
  assert.equal(save?.roleId, 'reviewer')
  const manifest = save?.manifest as Record<string, unknown> | undefined
  assert.equal(manifest?.name, 'Reviewer')
  assert.equal(manifest?.systemPrompt, 'You review code.')
  assert.equal(manifest?.root, '/tmp/reviewer')

  // The refresh after the save lands the new agent in the room's roster.
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.locator('[data-swarm-agent="reviewer"]')).toBeVisible()
})

test('configure an agent from the room roster', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-configure="research"]').click()
  const modal = page.locator('[data-swarm-role-modal]')
  await expect(modal).toBeVisible()
  await expect(modal.locator('#agent-id')).toHaveValue('research')
  await expect(modal.locator('#agent-skills')).toHaveValue('skills')
  await expect(modal.locator('#agent-mcp')).toHaveValue(/echo/)

  // Invalid MCP JSON stays in the editor with an error.
  await modal.locator('#agent-mcp').fill('not json')
  await modal.getByRole('button', { name: '保存' }).click()
  await expect(modal.getByText('MCP 服务器需要是 JSON 对象。')).toBeVisible()

  await modal.locator('#agent-plugins').fill('zen.feishu')
  await modal.locator('#agent-mcp').fill('{"local": {"command": ["echo", "v2"]}}')
  await modal.locator('#agent-inherit-project').check()
  await modal.getByRole('button', { name: '保存' }).click()

  await expect(modal).toHaveCount(0)
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const save = calls.find(
    (call) => call.type === 'swarm' && call.op === 'roles' && call.action === 'update',
  )
  assert.equal(save?.roleId, 'research')
  const manifest = save?.manifest as Record<string, unknown> | undefined
  assert.deepEqual(manifest?.skills, ['skills'])
  assert.deepEqual(manifest?.plugins, ['zen.feishu'])
  assert.deepEqual(manifest?.mcpServers, { local: { command: ['echo', 'v2'] } })
  assert.equal(manifest?.inheritProject, true)
})

test('create an agent from the room roster', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-new-role]').click()
  const modal = page.locator('[data-swarm-role-modal]')
  await modal.locator('#agent-id').fill('reviewer')
  await modal.locator('#agent-name').fill('Reviewer')
  await modal.getByRole('button', { name: '保存' }).click()

  await expect(modal).toHaveCount(0)
  await expect(page.locator('[data-swarm-agent="reviewer"]')).toBeVisible()

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const save = calls.find(
    (call) => call.type === 'swarm' && call.op === 'roles' && call.action === 'create',
  )
  assert.equal(save?.roleId, 'reviewer')
})

test('show role warnings above the room and reload them away', async ({ page }) => {
  await boot(
    page,
    initialState(swarmFixture({ swarmWarnings: ['role research: bad manifest'] })),
  )
  await page.locator('[data-workspace-mode="swarm"]').click()

  const warnings = page.locator('[data-swarm-warnings]')
  await expect(warnings).toContainText('role research: bad manifest')

  // The fixture's reload reports no problems, so the stale banner clears.
  await page.locator('[data-swarm-reload]').click()
  await expect(warnings).toHaveCount(0)

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.ok(calls.some((call) => call.type === 'swarm' && call.op === 'reload'))
})

test('open a task detail in the conversation column', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 查 X')
  await composer.press('Enter')

  const task = page.locator('[data-swarm-task]').first()
  await expect(task).toHaveAttribute('data-state', 'done')

  await task.locator('[data-swarm-task-toggle]').click()
  await expect(task).toHaveAttribute('data-selected', 'true')

  const detail = page.locator('[data-swarm-main] [data-swarm-task-detail]')
  await expect(detail).toBeVisible()
  await expect(detail).toContainText('任务 ID')
  await expect(detail).toContainText('t_1')
  await expect(detail).toContainText('查 X')
  await expect(detail).toContainText('结论是 Z')
  // The run's steps (tool calls, interim text) come from the role session.
  await expect(detail.locator('[data-swarm-task-steps]')).toBeVisible()
  await expect(detail.locator('[data-swarm-task-steps]')).toContainText('read_skill')
  await expect(detail.locator('[data-swarm-task-steps]')).toContainText('research 查完了')
  // The task list stays a compact index; nothing expands inside it.
  await expect(page.locator('[data-swarm-tasks] [data-swarm-task-detail]')).toHaveCount(0)

  await detail.locator('[data-swarm-task-detail-close]').click()
  await expect(detail).toHaveCount(0)
  await expect(task).not.toHaveAttribute('data-selected', 'true')
})
