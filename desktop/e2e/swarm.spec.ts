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
  // A role created inside a project inherits its context unless opted out.
  assert.equal(manifest?.inheritProject, true)

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
  // The detail owns the conversation column; the room feed steps aside.
  await expect(page.locator('[data-swarm-feed]')).toHaveCount(0)
  await expect(detail).toContainText('任务 ID')
  await expect(detail).toContainText('t_1')
  await expect(detail).toContainText('查 X')
  // The run's steps (tool calls, interim text) come from the role session.
  await expect(detail.locator('[data-swarm-task-steps]')).toBeVisible()
  await expect(detail.locator('[data-swarm-task-steps]')).toContainText('read_skill')
  await expect(detail.locator('[data-swarm-task-steps]')).toContainText('research 查完了')
  // The task list stays a compact index; nothing expands inside it.
  await expect(page.locator('[data-swarm-tasks] [data-swarm-task-detail]')).toHaveCount(0)

  await detail.locator('[data-swarm-task-detail-close]').click()
  await expect(detail).toHaveCount(0)
  await expect(page.locator('[data-swarm-feed]')).toBeVisible()
  await expect(task).not.toHaveAttribute('data-selected', 'true')
})

test('a running task detail streams live steps before the run ends', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 查 X')
  await composer.press('Enter')

  const task = page.locator('[data-swarm-task]').first()
  await task.locator('[data-swarm-task-toggle]').click()
  const detail = page.locator('[data-swarm-main] [data-swarm-task-detail]')
  await expect(detail).toBeVisible()

  // The role's session file has not landed yet: these rows can only come
  // from swarm_event frames, and they must arrive before the task settles.
  const steps = detail.locator('[data-swarm-task-steps]')
  await expect(steps).toContainText('read_skill')
  await expect(task).toHaveAttribute('data-state', 'running')
  await expect(steps).toContainText('research 查完了')
  // The injected task prompt is protocol plumbing, never a user bubble.
  await expect(steps).not.toContainText('[swarm]')

  // Settling reconciles against the file transcript; the content survives.
  await expect(task).toHaveAttribute('data-state', 'done')
  await expect(steps).toContainText('read_skill')
  await expect(steps).toContainText('research 查完了')
})

test('a settled task keeps its live tail when the transcript fetch fails', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ swarmFailTranscript: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 查 X')
  await composer.press('Enter')

  const task = page.locator('[data-swarm-task]').first()
  await task.locator('[data-swarm-task-toggle]').click()
  const detail = page.locator('[data-swarm-main] [data-swarm-task-detail]')
  const steps = detail.locator('[data-swarm-task-steps]')
  await expect(steps).toContainText('research 查完了')

  // The transcript op fails (a reset/compacted role session): the streamed
  // tail stays on screen instead of blanking the panel.
  await expect(task).toHaveAttribute('data-state', 'done')
  await expect(steps).toContainText('read_skill')
  await expect(steps).toContainText('research 查完了')
})

test('a collapsed sidebar can be restored from the room', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.getByRole('button', { name: 'toggle sidebar' }).click()
  await expect(page.locator('[data-sidebar-collapsed]')).toHaveCount(1)

  const expand = page.locator('[data-shell-expand]')
  await expect(expand).toBeVisible()
  await expand.click()
  await expect(page.locator('[data-sidebar-collapsed]')).toHaveCount(0)
  await expect(page.locator('[data-shell-sidebar]')).toBeVisible()
})

test('copy a library role into one project and not the other', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/p')
    localStorage.setItem(
      'zen.projects',
      JSON.stringify([
        { path: '/work/p', name: 'p', at: 1 },
        { path: '/work/q', name: 'q', at: 1 },
      ]),
    )
  })
  await boot(
    page,
    initialState(swarmFixture({ swarmProject: '/work/p', swarmRoles: [], swarmAgents: [] })),
  )
  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-page]')).toBeVisible()
  await expect(page.locator('[data-swarm-agent]')).toHaveCount(0)

  await page.locator('[data-swarm-copy-role]').click()
  const picker = page.locator('[data-library-picker]')
  await expect(picker.locator('[data-library-role="research"]')).toBeVisible()
  await picker.locator('[data-library-copy="research"]').click()
  await expect(page.locator('[data-swarm-agent="research"]')).toBeVisible()
  // The row now reads as already present: copying twice stays disabled.
  await expect(picker.locator('[data-library-copy="research"]')).toBeDisabled()

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const copy = calls.find(
    (call) => call.type === 'swarm' && call.op === 'roles' && call.action === 'copy',
  )
  assert.equal(copy?.roleId, 'research')

  await picker.getByRole('button', { name: '关闭' }).click()
  await expect(picker).toHaveCount(0)

  // Project B owns a separate room: the copied role never leaks there.
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '项目' }).click()
  await page.locator('[data-project="/work/q"]').getByRole('button', { name: '打开' }).click()
  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-page]')).toBeVisible()
  await expect(page.locator('[data-swarm-agent="research"]')).toHaveCount(0)
})

test('copy from the role library is reachable from Settings', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ swarmRoles: [], swarmAgents: [] })))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'Swarm', exact: true }).click()
  await expect(page.locator('[data-settings-swarm]')).toBeVisible()

  await page.locator('[data-settings-copy-role]').click()
  const picker = page.locator('[data-library-picker]')
  await expect(picker.locator('[data-library-role="builder"]')).toBeVisible()
  await picker.locator('[data-library-copy="builder"]').click()
  await expect(picker.locator('[data-library-role="builder"]')).toContainText('已在这个项目里')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const copy = calls.find(
    (call) => call.type === 'swarm' && call.op === 'roles' && call.action === 'copy',
  )
  assert.equal(copy?.roleId, 'builder')
})

test('switching project reloads the room and closes the old task detail', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.projects', JSON.stringify([{ path: '/work/q', name: 'q', at: 1 }]))
  })
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-task-toggle]').first().click()
  await expect(page.locator('[data-swarm-main] [data-swarm-task-detail]')).toBeVisible()
  const before = (await page.evaluate(() => window.__ZEN_E2E__.calls))
    .filter((call) => call.type === 'swarm' && call.op === 'list').length

  await page.locator('[data-project-switcher]').click()
  await page.locator('[data-project-option="/work/q"]').click()

  await expect(page.locator('[data-swarm-task-detail]')).toHaveCount(0)
  await expect(page.locator('[data-swarm-feed]')).toBeVisible()
  const lists = (await page.evaluate(() => window.__ZEN_E2E__.calls))
    .filter((call) => call.type === 'swarm' && call.op === 'list')
  assert.ok(lists.length > before, 'project switch must re-list the room')
  assert.equal(lists.at(-1)?.project, '/work/q')
})

test('saving a role keeps the open task detail', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()
  await page.locator('[data-swarm-task-toggle]').first().click()
  const detail = page.locator('[data-swarm-main] [data-swarm-task-detail]')
  await expect(detail).toBeVisible()

  await page.locator('[data-swarm-configure="research"]').click()
  const modal = page.locator('[data-swarm-role-modal]')
  await modal.getByRole('button', { name: '保存' }).click()
  await expect(modal).toHaveCount(0)
  await expect(detail).toBeVisible()
})

test('the settings detour clears the old task detail when the project switched', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/p')
    localStorage.setItem('zen.projects', JSON.stringify([{ path: '/work/q', name: 'q', at: 1 }]))
  })
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()
  await page.locator('[data-swarm-task-toggle]').first().click()
  await expect(page.locator('[data-swarm-main] [data-swarm-task-detail]')).toBeVisible()

  // Leave swarm (its sidebar unmounts), switch project in zen, then detour
  // through Settings → Agents: that view loads the room too, and must clear
  // the old room's selection on a project switch just like the sidebar does.
  await page.locator('[data-workspace-mode="zen"]').click()
  await page.locator('[data-project-switcher]').click()
  await page.locator('[data-project-option="/work/q"]').click()
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '角色' }).click()
  await page.getByRole('button', { name: '返回' }).click()
  await page.locator('[data-workspace-mode="swarm"]').click()

  await expect(page.locator('[data-swarm-task-detail]')).toHaveCount(0)
  await expect(page.locator('[data-swarm-feed]')).toBeVisible()
})

test('the swarm tab badge counts live tasks without eager-loading the room', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  // 未进 swarm：不预载，徽标不显示
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.ok(!calls.some((call) => call.type === 'swarm' && call.op === 'list'))
  await expect(page.locator('[data-swarm-badge]')).toHaveCount(0)

  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-badge]')).toHaveText('1')
  await page.locator('[data-workspace-mode="coding"]').click()
  await expect(page.locator('[data-swarm-badge]')).toHaveText('1')
})
