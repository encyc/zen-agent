/** Settings surfaces: the command palette, font size, and the model,
 * skill, limit, mcp, project and thinking panels, plus the
 * Enter-to-send switch and the context ring's used-share lead. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, paletteKey } from './harness.ts'

test('the palette opens with the keyboard and resumes a matching session', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const dialog = page.getByRole('dialog', { name: '搜索' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('combobox').fill('另一个会话')
  await page.getByRole('option', { name: /另一个会话/ }).click()
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return { root: op?.root, resume: op?.resume }
      }),
    )
    .toEqual({ root: '/work/p', resume: '/sessions/b.jsonl' })
  await expect(dialog).toBeHidden()
})
test('palette commands run bare or insert their prefix', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  await page.getByRole('dialog').getByRole('combobox').fill('compact')
  await page.keyboard.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'compact' && call.argument === '',
        ),
      ),
    )
    .toBe(true)

  await page.keyboard.press(paletteKey)
  await page.getByRole('dialog').getByRole('combobox').fill('rename')
  await page.keyboard.press('Enter')
  await expect(page.locator('textarea')).toHaveValue('/rename ')

  await page.keyboard.press(paletteKey)
  await page.getByRole('dialog').getByRole('combobox').fill('quit')
  await expect(page.getByRole('option', { name: /quit|Quit/ })).toHaveCount(0)
})
test('the font size setting scales chat and settings together', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zen.fontScale', JSON.stringify(1.2)))
  await boot(page, initialState())
  const fontOf = (locator: string) =>
    page.locator(locator).first().evaluate((el) => getComputedStyle(el).fontSize)

  // pi multiplies every type token by the scale, so entering settings never
  // changes the apparent zoom level. The composer body is 14px * 1.2.
  assert.equal(await fontOf('[data-composer-plate] textarea'), '16.8px')

  await page.getByRole('button', { name: '设置' }).click()
  assert.equal(await fontOf('nav button'), '15.6px')
})
test('settings replaces the shell and returns to chat', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: '设置' }).click()
  await expect(page.getByRole('heading', { name: '外观' })).toBeVisible()
  await page.getByRole('button', { name: '主题' }).click()
  await page.getByRole('option', { name: '深色' }).click()
  await expect(page.locator('body')).toHaveAttribute('data-ds-dark-theme', '')
  await page.getByRole('button', { name: '对话' }).click()
  await expect(page.getByRole('heading', { name: '对话', exact: true })).toBeVisible()
  await expect(page.getByRole('switch', { name: '显示会话时间' })).toBeVisible()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()

  // The footer keeps the previous theme cycle next to the settings cog.
  await expect(page.getByRole('button', { name: '外观: 深色' })).toBeVisible()
  await page.getByRole('button', { name: '外观: 深色' }).click()
  await expect(page.getByRole('button', { name: '外观: 跟随系统' })).toBeVisible()
  await expect(page.locator('body')).not.toHaveAttribute('data-ds-dark-theme', '')
})
test('model settings saves a provider key without echoing it', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '模型' }).click()

  await expect(page.getByRole('heading', { name: '模型服务' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '搜索服务' })).toBeVisible()
  const deepseek = page.locator('[data-provider="deepseek"]')
  await expect(deepseek).toContainText('未配置')

  await deepseek.getByRole('button', { name: '设置' }).click()
  const input = deepseek.getByLabel('设置')
  await expect(input).toHaveAttribute('type', 'password')
  await input.fill('sk-e2e-secret')
  await deepseek.getByRole('button', { name: '保存' }).click()

  await expect(deepseek).toContainText('已配置')
  await expect(deepseek.getByLabel('设置')).toHaveCount(0)
  await expect(page.locator('[data-page="settings"]')).not.toContainText('sk-e2e-secret')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const saved = calls.find((call) => call.type === 'secrets' && call.op === 'set')
  assert.equal(saved?.name, 'DEEPSEEK_API_KEY')
  assert.equal(saved?.value, 'sk-e2e-secret')

  // Reopening starts blank: the server never returns key material.
  await deepseek.getByRole('button', { name: '更换' }).click()
  await expect(deepseek.getByLabel('设置')).toHaveValue('')
})
test('model settings clears a stored key', async ({ page }) => {
  await boot(
    page,
    initialState({ running: false, secretsStored: ['DEEPSEEK_API_KEY'] }),
  )
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '模型' }).click()

  const deepseek = page.locator('[data-provider="deepseek"]')
  await expect(deepseek).toContainText('已配置')
  await deepseek.getByRole('button', { name: '清除' }).click()
  await expect(deepseek).toContainText('未配置')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const cleared = calls.find((call) => call.type === 'secrets' && call.op === 'clear')
  assert.equal(cleared?.name, 'DEEPSEEK_API_KEY')
})
test('model settings tests model and search connections inline', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      secretsStored: ['DEEPSEEK_API_KEY', 'TAVILY_API_KEY'],
      secretTests: { tavily: { connected: false, message: 'HTTP 401' } },
    }),
  )
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '模型' }).click()

  const deepseek = page.locator('[data-provider="deepseek"]')
  await deepseek.getByRole('button', { name: '测试连接' }).click()
  await expect(deepseek).toContainText('连接正常')

  const tavily = page.locator('[data-provider="tavily"]')
  await tavily.getByRole('button', { name: '测试连接' }).click()
  await expect(tavily).toContainText('连接失败：HTTP 401')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const tests = calls.filter((call) => call.type === 'secrets' && call.op === 'test')
  assert.deepEqual(
    tests.map((call) => [call.scope, call.target]),
    [
      ['model', 'deepseek'],
      ['search', 'tavily'],
    ],
  )
})
test('skill settings previews and toggles a skill', async ({ page }) => {
  await boot(page, initialState({ running: false, skillsDisabled: ['tidy'] }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '技能' }).click()

  await expect(page.getByRole('heading', { name: '技能库' })).toBeVisible()
  const review = page.locator('[data-skill="review"]')
  const tidy = page.locator('[data-skill="tidy"]')
  await expect(review).toContainText('项目')
  await expect(tidy).toContainText('全局')
  await expect(tidy.getByRole('switch')).toHaveAttribute('aria-checked', 'false')

  await review.getByRole('button', { name: '预览' }).click()
  await expect(review.locator('pre')).toContainText('body of review')

  await tidy.getByRole('switch').click()
  await expect(tidy.getByRole('switch')).toHaveAttribute('aria-checked', 'true')

  await page.getByRole('button', { name: '重新扫描' }).click()
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const toggles = calls.filter((call) => call.type === 'skills' && call.op === 'toggle')
  assert.deepEqual(
    toggles.map((call) => [call.name, call.enabled]),
    [['tidy', true]],
  )
  assert.equal(
    calls.some((call) => call.type === 'skills' && call.op === 'rescan'),
    true,
  )
})
test('limit settings lists grouped knobs and their defaults', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '参数与预算' }).click()

  await expect(page.getByRole('heading', { name: '配置文件' })).toBeVisible()
  await expect(page.locator('[data-group="skills"]')).toBeVisible()
  await expect(page.locator('[data-group="subagents"]')).toBeVisible()
  await expect(page.locator('[data-group="tools"]')).toBeVisible()

  // Read-only by default: values render as text, no editor in sight.
  const guard = page.locator('[data-knob="limits.agent.loop_guard_threshold"]')
  await expect(guard).toContainText('5')
  await expect(guard).toContainText('默认 5')
  await expect(guard).toContainText('立即生效')
  await expect(guard.getByRole('textbox')).toHaveCount(0)
  await expect(page.locator('[data-knob="task.keep_transcripts"]')).toContainText('64')
  await expect(page.getByRole('button', { name: '修改' })).toBeVisible()
})
test('limit settings: 修改 dims the copy, 保存 submits the whole batch', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '参数与预算' }).click()

  await page.getByRole('button', { name: '修改' }).click()
  const stack = page.locator('[data-editing]')
  await expect(stack).toBeVisible()
  const index = page.locator('[data-knob="limits.skills.max_index_bytes"]')
  const guard = page.locator('[data-knob="limits.agent.loop_guard_threshold"]')
  await index.getByRole('textbox').fill('16384')
  await guard.getByRole('textbox').fill('50')
  // Two changed knobs, one save: the button names the batch.
  await page.getByRole('button', { name: '保存（2 项改动）' }).click()

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const sets = calls
    .filter((call) => call.type === 'limits' && call.op === 'set')
    .map(({ type, op, key, value }) => ({ type, op, key, value }))
  assert.deepEqual(sets, [
    { type: 'limits', op: 'set', key: 'limits.skills.max_index_bytes', value: 16384 },
    { type: 'limits', op: 'set', key: 'limits.agent.loop_guard_threshold', value: 50 },
  ])
  // Back to read-only, values now read from the saved state.
  await expect(page.getByRole('button', { name: '修改' })).toBeVisible()
  await expect(guard).toContainText('50')
})
test('limit settings: one bad value blocks the whole save', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '参数与预算' }).click()

  await page.getByRole('button', { name: '修改' }).click()
  const index = page.locator('[data-knob="limits.skills.max_index_bytes"]')
  const guard = page.locator('[data-knob="limits.agent.loop_guard_threshold"]')
  await index.getByRole('textbox').fill('16384')
  await guard.getByRole('textbox').fill('99999999') // over the knob's max
  const save = page.getByRole('button', { name: '保存（2 项改动）' })
  await expect(save).toBeDisabled()
  await expect(guard.getByRole('textbox')).toHaveValue('99999999')
  await expect(guard).toHaveAttribute('data-invalid', 'true')
  await expect(page.getByText('有无效值，修正后才能保存')).toBeVisible()

  // Nothing was sent, and fixing the row re-enables the same button.
  await guard.getByRole('textbox').fill('50')
  await expect(save).toBeEnabled()
  await save.click()
  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.equal(calls.filter((call) => call.type === 'limits' && call.op === 'set').length, 2)
})
test('limit settings surfaces a refused write instead of pretending', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      limitSetError: 'unknown keys [task.future_budget] in managed tables',
    }),
  )
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '参数与预算' }).click()

  await page.getByRole('button', { name: '修改' }).click()
  const index = page.locator('[data-knob="limits.skills.max_index_bytes"]')
  await index.getByRole('textbox').fill('2048')
  await page.getByRole('button', { name: '保存（1 项改动）' }).click()

  await expect(page.locator('[data-page="settings"] .error, [data-page="settings"] [class*="error"]').last()).toContainText(
    'unknown keys',
  )
})
test('mcp settings probes a server and reports its tools', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'MCP' }).click()

  await expect(page.getByRole('heading', { name: 'MCP 服务器' })).toBeVisible()
  const filesystem = page.locator('[data-server="filesystem"]')
  const docs = page.locator('[data-server="docs"]')
  await expect(filesystem).toContainText('npx -y')
  await expect(filesystem).toContainText('环境变量：HOME')
  await expect(docs).toContainText('覆盖全局')

  await filesystem.getByRole('button', { name: '测试连接' }).click()
  await expect(filesystem).toContainText('连接正常，2 个工具：read, write')

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  assert.equal(
    calls.some((call) => call.type === 'mcp' && call.op === 'test' && call.name === 'filesystem'),
    true,
  )
})
test('mcp settings adds a project server and removes one', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: 'MCP' }).click()

  await page.getByRole('button', { name: '添加服务器' }).click()
  const form = page.locator('[data-mcp-form]')
  await form.getByLabel('名称').fill('github')
  await form.getByRole('button', { name: 'HTTP', exact: true }).click()
  await form.getByLabel('服务地址').fill('https://mcp.example.com')
  await form.getByRole('button', { name: '项目', exact: true }).click()
  await expect(form).toContainText('项目层保存在仓库内')
  await form.getByRole('button', { name: '保存服务器' }).click()

  const github = page.locator('[data-server="github"]')
  await expect(github).toContainText('https://mcp.example.com')
  await expect(github).toContainText('项目')

  const docs = page.locator('[data-server="docs"]')
  await docs.getByRole('button', { name: '移除' }).click()
  await docs.getByRole('button', { name: '确认移除' }).click()
  await expect(docs).toHaveCount(0)

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const added = calls.find((call) => call.type === 'mcp' && call.op === 'add')
  assert.equal(added?.name, 'github')
  assert.equal(added?.transport, 'http')
  assert.equal(added?.url, 'https://mcp.example.com')
  assert.equal(added?.layer, 'project')
  const removed = calls.find((call) => call.type === 'mcp' && call.op === 'remove')
  assert.deepEqual([removed?.name, removed?.layer], ['docs', 'project'])
})
test('project settings lists folders, opens one and forgets another', async ({ page }) => {
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
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '项目' }).click()

  await expect(page.getByRole('heading', { name: '项目目录' })).toBeVisible()
  const current = page.locator('[data-project="/work/p"]')
  const other = page.locator('[data-project="/work/q"]')
  await expect(current).toContainText('当前')
  await expect(current).toContainText('2 个会话')
  await expect(other).toContainText('1 个会话')
  // Relocation must not move the project the runtime is writing to.
  await expect(current.getByRole('button', { name: '更改目录' })).toBeDisabled()
  await expect(other.getByRole('button', { name: '更改目录' })).toBeEnabled()

  // Opening another folder switches the resident runtime there and returns to
  // chat, landing on that project's newest conversation instead of a blank one.
  await other.getByRole('button', { name: '打开' }).click()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  assert.deepEqual(
    await page.evaluate(() => {
      const op = window.__ZEN_E2E__.projectOps?.at(-1)
      return { root: op?.root, resume: op?.resume }
    }),
    { root: '/work/q', resume: '/sessions/c.jsonl' },
  )
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '项目' }).click()
  const first = page.locator('[data-project="/work/p"]')
  await first.getByRole('button', { name: '移除' }).click()
  await first.getByRole('button', { name: '确认移除' }).click()
  // Forgetting the registration keeps the row while its sessions exist.
  await expect(first).toContainText('来自会话')
  await expect(first.getByRole('button', { name: '移除' })).toHaveCount(0)
})
test('project settings moves a renamed folder to its new path', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/q')
    localStorage.setItem(
      'zen.projects',
      JSON.stringify([
        { path: '/work/p', name: 'p', at: 1 },
        { path: '/work/q', name: 'q', at: 1 },
      ]),
    )
  })
  await boot(page, initialState({ running: false, dialogSelection: '/work/p2' }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '项目' }).click()

  const row = page.locator('[data-project="/work/p"]')
  await expect(row).toContainText('2 个会话')
  await row.getByRole('button', { name: '更改目录' }).click()
  const confirm = row.locator('[data-project-confirm]')
  await expect(confirm).toContainText('把 2 个会话迁移到 /work/p2？')
  await confirm.getByRole('button', { name: '迁移' }).click()

  const moved = page.locator('[data-project="/work/p2"]')
  await expect(moved).toBeVisible()
  await expect(row).toHaveCount(0)
  await expect(moved).toContainText('2 个会话')
  const calls = await page.evaluate(() =>
    window.__ZEN_E2E__.calls.filter((call) => call.type === 'project'),
  )
  assert.equal(calls[0]?.op, 'relocate')
  assert.equal(calls[0]?.from, '/work/p')
  assert.equal(calls[0]?.to, '/work/p2')
  const stored = await page.evaluate(() =>
    (JSON.parse(localStorage.getItem('zen.projects') ?? '[]') as { path: string }[]).map(
      (project) => project.path,
    ),
  )
  assert.deepEqual(stored.sort(), ['/work/p2', '/work/q'])
})
test('project settings lists projects seen in sessions', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/q')
    localStorage.setItem(
      'zen.projects',
      JSON.stringify([{ path: '/work/q', name: 'q', at: 1 }]),
    )
  })
  await boot(page, initialState({ running: false, sessionProjects: ['/work/solo'] }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '项目' }).click()

  const derived = page.locator('[data-project="/work/solo"]')
  await expect(derived).toBeVisible()
  await expect(derived).toContainText('来自会话')
  await expect(derived).toContainText('1 个会话')
  await expect(derived.getByRole('button', { name: '打开' })).toBeEnabled()
  // Only manual registrations can be forgotten.
  await expect(derived.getByRole('button', { name: '移除' })).toHaveCount(0)

  const registered = page.locator('[data-project="/work/q"]')
  await expect(registered).toContainText('1 个会话')
  await expect(registered).not.toContainText('来自会话')
  await expect(registered.getByRole('button', { name: '移除' })).toBeVisible()
})
test('project settings keeps every registered folder past eight', async ({ page }) => {
  await page.addInitScript(() => {
    const projects = Array.from({ length: 10 }, (_, index) => ({
      path: `/work/p${index}`,
      name: `p${index}`,
      at: 10 - index,
    }))
    localStorage.setItem('zen.projects', JSON.stringify(projects))
  })
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '项目' }).click()

  for (let index = 0; index < 10; index += 1) {
    await expect(page.locator(`[data-project="/work/p${index}"]`)).toBeVisible()
  }
})
test('thinking settings unfold and hide settled reasoning', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'explain', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: 'answer',
          thinking: 'check the logs\nverify the timeout',
          timestamp: 2,
        },
      ],
    }),
  )
  await expect(page.getByTestId('thinking-row')).toBeVisible()
  await expect(page.getByTestId('thinking-body')).toHaveCount(0)

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '对话' }).click()
  await page.getByRole('switch', { name: '已完成思考自动收起' }).click()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.getByTestId('thinking-body')).toContainText('check the logs')

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '对话' }).click()
  await page.getByRole('switch', { name: '显示思考' }).click()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.getByTestId('thinking-row')).toHaveCount(0)
})
test('the context ring can lead with the used share', async ({ page }) => {
  await boot(page, initialState())
  const meter = page.getByTestId('context-meter')
  await expect(meter).toContainText('75%')

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '对话' }).click()
  await page.getByRole('button', { name: '已用' }).click()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(meter).toContainText('25%')
})
test('Enter only sends when the setting allows it', async ({ page }) => {
  const submitKey = process.platform === 'darwin' ? 'Meta+Enter' : 'Control+Enter'
  await boot(page, initialState({ running: false }))
  const input = page.locator('[data-composer-plate] textarea')
  await input.fill('hello')
  await input.press('Enter')
  await expect(input).toHaveValue('')

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '对话' }).click()
  await page.getByRole('switch', { name: '回车发送' }).click()
  await page.getByRole('button', { name: '返回' }).click()

  await input.fill('hello')
  await input.press('Enter')
  await expect(input).toHaveValue('hello\n')
  await input.press(submitKey)
  await expect(input).toHaveValue('')
})
