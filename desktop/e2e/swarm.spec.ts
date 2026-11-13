import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, type BridgeState } from './harness'
import { swarmFixture } from '../src/dev/swarmMock'

const roomModels = [
  {
    id: 'deepseek-chat',
    provider: 'deepseek',
    context_window: 128000,
    max_tokens: 8192,
    reasoning: false,
    supports_vision: false,
    available: true,
    reason: null,
    current: true,
  },
  {
    id: 'deepseek-reasoner',
    provider: 'deepseek',
    context_window: 128000,
    max_tokens: 8192,
    reasoning: true,
    supports_vision: false,
    available: true,
    reason: null,
    current: false,
  },
]

test('switch to swarm, delegate, and see the chain', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()
  await expect(page.locator('[data-swarm-page]')).toBeVisible()
  // The room shares the chat/zen composer plate instead of its own IM box.
  await expect(page.locator('[data-swarm-composer][data-composer-plate]')).toBeVisible()

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
  const post = calls.find(
    (call) => call.type === 'supervisor' && call.op === 'dispatch' && call.mode === 'swarm',
  )
  assert.equal(post?.prompt, '@research 查 X')
})

test('the room @ menu picks a role inside the shared plate', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@res')
  const option = page.locator('[data-swarm-mention="research"]')
  await expect(option).toBeVisible()
  await composer.press('Enter')
  await expect(composer).toHaveValue('@research ')
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

test('configured roles render even when no role runtime is live', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ swarmAgents: [] })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await expect(page.locator('[data-swarm-agent]')).toHaveCount(2)
  await expect(page.locator('[data-swarm-agent="research"]')).toContainText('Research')
  await expect(page.locator('[data-swarm-agent="builder"]')).toContainText('Builder')
})

test('create a role from settings', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'Swarm', exact: true }).click()
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

test('swarm settings merge project context, policy and the role roster', async ({ page }) => {
  // The card names the project the host is serving; the room fixture belongs
  // to that same root so the roster loads.
  await boot(page, initialState(swarmFixture({ swarmProject: '/Users/e2e' })))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'Swarm', exact: true }).click()
  await expect(page.locator('[data-settings-swarm]')).toBeVisible()

  await expect(page.getByRole('heading', { name: '当前项目' })).toBeVisible()
  await expect(page.getByText('角色凭据齐全')).toBeVisible()

  const panel = page.locator('[data-knob-table="swarm"]')
  await expect(page.locator('[data-knob="swarm.max_concurrent"]')).toContainText('3')
  await expect(page.locator('[data-knob="swarm.max_chain_depth"]')).toContainText('8')
  await expect(page.locator('[data-knob="swarm.max_chain_cost"]')).toContainText('5')

  // The roster here is the same CRUD the room sidebar offers.
  await expect(page.locator('[data-agent-role="research"]')).toBeVisible()

  await panel.getByRole('button', { name: '修改' }).click()
  await page.locator('[data-knob="swarm.max_concurrent"]').getByRole('textbox').fill('4')
  await panel.getByRole('button', { name: '保存（1 项改动）' }).click()
  const sets = (await page.evaluate(() => window.__ZEN_E2E__.calls))
    .filter((call) => call.type === 'limits' && call.op === 'set')
    .map(({ key, value }) => ({ key, value }))
  assert.deepEqual(sets, [{ key: 'swarm.max_concurrent', value: 4 }])
})

test('swarm settings surface roles whose credentials are missing', async ({ page }) => {
  const fixture = swarmFixture()
  const roles = (fixture.swarmRoles ?? []).map((role) =>
    role.id === 'research' ? { ...role, missingCredentials: ['DEEPSEEK_API_KEY'] } : role,
  )
  await boot(page, initialState({ ...fixture, swarmRoles: roles }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'Swarm', exact: true }).click()

  await expect(page.getByText('角色缺少凭据：DEEPSEEK_API_KEY')).toBeVisible()
  await page.getByRole('button', { name: '去配置模型' }).click()
  await expect(page.locator('[data-active]')).toContainText('模型')
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
  // through Settings → Swarm: that view loads the room too, and must clear
  // the old room's selection on a project switch just like the sidebar does.
  await page.locator('[data-workspace-mode="zen"]').click()
  await page.locator('[data-project-switcher]').click()
  await page.locator('[data-project-option="/work/q"]').click()
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'Swarm', exact: true }).click()
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

test('quoting a message sends its id with the post', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const source = page.locator('[data-swarm-message]', { hasText: '跑一个长任务' })
  await source.hover()
  await source.locator('[data-swarm-quote]').click()
  await expect(page.locator('[data-swarm-quote-strip]')).toContainText('跑一个长任务')

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@research 接着做')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-quote-strip]')).toHaveCount(0)
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const post = calls
    .filter((call) => call.type === 'supervisor' && call.op === 'dispatch')
    .at(-1)
  assert.equal(post?.ref, 'm_seed')
})

test('the preview says a busy role will queue', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-input]').fill('@research 再来一个')
  await expect(page.locator('[data-swarm-preview]')).toContainText('排队')
})

test('/stop cancels every live task', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('/stop')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-task]')).toHaveAttribute('data-state', 'cancelled')
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.equal(
    calls.some((call) => call.type === 'swarm' && call.op === 'cancel' && call.taskId === 't_seed'),
    true,
  )
})

test('a slash message that is not a room command still posts', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('/usr/bin 在哪')
  await composer.press('Enter')

  await expect(page.locator('[data-swarm-message]', { hasText: '/usr/bin 在哪' })).toBeVisible()
})

test('an attachment renders as a file-name chip in the room', async ({ page }) => {
  const fixture = swarmFixture()
  fixture.swarmMessages = [
    {
      id: 'm_img',
      ts: 1,
      author: { kind: 'user', id: 'you' },
      text: '@research 看看这张图',
      mentions: ['research'],
      taskId: null,
      chainDepth: 0,
      images: ['/tmp/screens/shot.png'],
    },
  ]
  await boot(page, initialState(fixture))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await expect(
    page
      .locator('[data-swarm-message]', { hasText: '看看这张图' })
      .locator('[data-swarm-image]'),
  ).toHaveText('shot.png')
})

test('the empty plate turns into a stop control while a task runs', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const stop = page.getByRole('button', { name: '停止' })
  await expect(stop).toBeVisible()
  await stop.click()
  await expect(page.locator('[data-swarm-task]')).toHaveAttribute('data-state', 'cancelled')
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.ok(calls.some((call) => call.type === 'swarm' && call.op === 'cancel'))
})

test('Escape on an empty plate stops the running tasks', async ({ page }) => {
  await boot(page, initialState(swarmFixture({ runningTask: true })))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-input]').click()
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-swarm-task]')).toHaveAttribute('data-state', 'cancelled')
})

test('/help lists the room commands instead of reopening the slash menu', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('/help')
  await composer.press('Enter')

  const help = page.locator('[data-swarm-help]')
  await expect(help).toBeVisible()
  await expect(help).toContainText('/reset')
  await expect(help).toContainText('/stop')
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(help).toHaveCount(0)
})

test('/reset opens a role picker and resets only the chosen role', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('/reset')
  await composer.press('Enter')

  const research = page.locator('[data-swarm-reset-role="research"]')
  await expect(research).toBeVisible()
  await research.click()

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const reset = calls.find((call) => call.type === 'swarm' && call.op === 'reset')
  assert.equal(reset?.agentId, 'research')
})

test('a role target shows its model, mode and thinking', async ({ page }) => {
  await boot(page, initialState(swarmFixture()))
  await page.locator('[data-workspace-mode="swarm"]').click()

  await page.locator('[data-swarm-input]').fill('@research ')

  const targets = await page.locator('[data-swarm-preview]').getAttribute('data-swarm-targets')
  assert.match(targets ?? '', /deepseek-chat/)
  assert.match(targets ?? '', /\bask\b/)
  assert.match(targets ?? '', /medium/)
})

test('typing @ also offers workspace files, with a quoted path', async ({ page }) => {
  await boot(page, {
    ...initialState(swarmFixture()),
    fsIndex: [{ path: 'src/room.ts', kind: 'file' as const }],
  })
  await page.locator('[data-workspace-mode="swarm"]').click()

  const composer = page.locator('[data-swarm-input]')
  await composer.fill('@room')

  const file = page.locator('[data-swarm-file-mention="src/room.ts"]')
  await expect(file).toBeVisible()
  await file.click()
  await expect(composer).toHaveValue('@"src/room.ts" ')
})

test('the room model chip applies to every role without a mention', async ({ page }) => {
  await boot(page, initialState({ ...swarmFixture(), models: roomModels }))
  await page.locator('[data-workspace-mode="swarm"]').click()

  const chip = page.locator('[data-swarm-model]')
  await expect(chip).toContainText('deepseek-chat')
  await chip.click()

  const menu = page.locator('[data-role-model-menu]')
  await expect(menu).toBeVisible()
  await menu.locator('[data-model="deepseek-reasoner"]').click()

  await expect(chip).toContainText('deepseek-reasoner')
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const updates = calls.filter(
    (call) => call.type === 'swarm' && call.op === 'roles' && call.action === 'update',
  )
  assert.deepEqual(updates.map((call) => call.roleId).sort(), ['builder', 'research'])
  for (const update of updates) {
    assert.deepEqual((update.manifest as Record<string, unknown>).model, 'deepseek-reasoner')
  }
})
