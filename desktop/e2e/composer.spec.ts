/** The composer surface: coverage of the last message, the todo dock,
 * approval and ask cards, the slash/@ menus, the model chip's reasoning
 * control, the resizable plate, the queued-message lane and the image
 * picker. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, emitEvent, setPending } from './harness.ts'

test('the composer never covers the last message on a short window', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 10 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: '回复 '.repeat(120), timestamp: index * 2 + 2 },
  ]).flat()
  await boot(
    page,
    initialState({
      running: false,
      transcript,
      todos: [
        {
          name: 'Tasks',
          tasks: Array.from({ length: 14 }, (_, index) => ({
            content: `待办项 ${index}`,
            status: 'pending',
          })),
        },
      ],
    }),
  )
  await page.getByRole('button', { name: '展开任务清单' }).click()
  await expect(
    page.getByRole('button', { name: '收起任务清单' }),
  ).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('待办项 13')).toHaveCount(1)
  await expect
    .poll(async () => {
      await page
        .locator('[data-chat-flow]')
        .evaluate((el) => el.scrollIntoView({ block: 'end' }))
      return page.evaluate(() => {
        const rows = document.querySelectorAll<HTMLElement>('[data-minimap-id]')
        const last = rows[rows.length - 1]
        const dock = document.querySelector<HTMLElement>('[data-shell-composer]')
        if (last === undefined || dock === null) return Number.POSITIVE_INFINITY
        return last.getBoundingClientRect().bottom - dock.getBoundingClientRect().top
      })
    })
    .toBeLessThanOrEqual(1)
})
test('the todo dock sits above the composer with progress and an expandable list', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: true,
      todos: [
        {
          name: 'Tasks',
          tasks: [
            { content: '读取会话', status: 'completed' },
            { content: '重写 todo dock', status: 'in_progress' },
            { content: '补充 e2e', status: 'pending' },
          ],
        },
      ],
    }),
  )
  const dock = page.locator('[data-todo-dock]')
  await expect(dock).toContainText('已完成 1 个任务（共 3 个）')
  await expect(dock).toContainText('重写 todo dock')
  const dockBox = await dock.boundingBox()
  const plateBox = await page.locator('[data-composer-plate]').boundingBox()
  assert.ok(dockBox !== null && plateBox !== null)
  assert.ok(dockBox.y + dockBox.height <= plateBox.y + 0.5)

  const toggle = page.getByRole('button', { name: '展开任务清单' })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await expect(
    page.getByRole('button', { name: '收起任务清单' }),
  ).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('补充 e2e')).toBeVisible()
  assert.equal(
    await page.getByText('读取会话').evaluate((el) => getComputedStyle(el).textDecorationLine),
    'line-through',
  )

  await page.getByRole('button', { name: '清除任务清单' }).click()
  await expect(dock).toHaveCount(0)
})
test('an approval stacks above a still-mounted composer', async ({ page }) => {
  await boot(page, initialState())
  await page.evaluate(() => {
    window.__ZEN_E2E_EMIT__('rpc:frame', {
      event: 'rpc:frame',
      id: 1,
      payload: {
        frame: {
          type: 'event',
          event: {
            type: 'approval_request',
            request_id: 'req-1',
            tool_name: 'bash',
            args: '{"command":"git push --force"}',
            risk: 'high',
            target: 'bash',
            reason: '这条命令会改写远端历史',
          },
        },
      },
    })
  })
  const panel = page.locator('[data-approval]')
  await expect(panel).toContainText('这条命令会改写远端历史')
  await expect(page.locator('[data-composer-plate] textarea')).toBeVisible()
  const panelBox = await panel.boundingBox()
  const plateBox = await page.locator('[data-composer-plate]').boundingBox()
  assert.ok(panelBox !== null && plateBox !== null)
  assert.ok(panelBox.y + panelBox.height <= plateBox.y + 0.5)
  await page.getByRole('button', { name: '拒绝' }).click()
  await expect(panel).toHaveCount(0)
  assert.ok(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some(
        (call) =>
          call.type === 'approval' &&
          call.request_id === 'req-1' &&
          call.decision === 'deny',
      ),
    ),
  )
})
test('the ask card collects answers and resolves the blocked tool call', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: '帮我选一个数据库', timestamp: 1 },
      ],
    }),
  )
  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q0',
    questions: [
      {
        id: 'db',
        question: '用哪个数据库？',
        header: '数据库',
        options: [
          { label: 'SQLite', description: '单文件，零配置' },
          { label: 'Postgres' },
        ],
        multi: false,
        recommended: 'SQLite',
      },
      {
        id: 'scope',
        question: '需要哪些能力？',
        options: [{ label: '读' }, { label: '写' }],
        multi: true,
      },
    ],
  })

  const card = page.getByTestId('ask-card')
  await expect(card).toBeVisible()
  await expect(card).toContainText('1/2')
  await expect(card).toContainText('推荐')
  await expect(page.locator('[data-composer-plate] textarea')).toBeVisible()

  await card.getByRole('radio', { name: /SQLite/ }).click()
  await card.getByRole('button', { name: '下一步' }).click()

  await expect(card).toContainText('2/2')
  await card.getByRole('checkbox', { name: '读', exact: true }).click()
  await card.getByRole('checkbox', { name: '写', exact: true }).click()
  await card.getByRole('checkbox', { name: '其他（自己填写）' }).click()
  await card.getByPlaceholder('输入你的答案…').fill('审计日志')
  await card.getByRole('button', { name: '提交' }).click()

  await expect(card).toHaveCount(0)
  const ask = await page.evaluate(() =>
    window.__ZEN_E2E__.calls.find((call) => call.type === 'ask'),
  )
  assert.equal(ask?.request_id, 'q0')
  assert.deepEqual(ask?.answers, [['SQLite'], ['读', '写', '审计日志']])

  await emitEvent(page, {
    type: 'ask_request',
    request_id: 'q1',
    questions: [
      {
        id: 'continue',
        question: '继续吗？',
        options: [{ label: '继续' }, { label: '停止' }],
        multi: false,
      },
    ],
  })
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: '不回答' }).click()
  await expect(card).toHaveCount(0)
  assert.deepEqual(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.find(
        (call) => call.type === 'ask' && call.request_id === 'q1',
      )?.answers,
    ),
    [null],
  )
})
test('composer menus open directly above their own trigger', async ({ page }) => {
  await boot(page, initialState())
  const mode = page.locator('[data-menu-trigger="mode"]')
  await mode.click()
  const modeChip = await mode.boundingBox()
  const modeMenu = await page.getByRole('menu').boundingBox()
  assert.ok(modeChip !== null && modeMenu !== null)
  assert.ok(Math.abs(modeMenu.x - modeChip.x) <= 1, `mode menu x ${modeMenu.x} vs ${modeChip.x}`)
  assert.ok(
    modeChip.y - (modeMenu.y + modeMenu.height) <= 12,
    `mode menu gap ${modeChip.y - (modeMenu.y + modeMenu.height)}`,
  )

  const model = page.locator('[data-menu-trigger="model"]')
  await model.click()
  const modelChip = await model.boundingBox()
  const modelMenu = await page.getByRole('menu').boundingBox()
  assert.ok(modelChip !== null && modelMenu !== null)
  assert.ok(
    Math.abs(modelMenu.x + modelMenu.width - (modelChip.x + modelChip.width)) <= 1,
    `model menu right ${modelMenu.x + modelMenu.width} vs ${modelChip.x + modelChip.width}`,
  )
  assert.ok(
    modelChip.y - (modelMenu.y + modelMenu.height) <= 12,
    `model menu gap ${modelChip.y - (modelMenu.y + modelMenu.height)}`,
  )
})
test('the composer slash menu offers skills and runs the picked skill', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  const input = page.locator('[data-composer-plate] textarea')
  const options = page.getByRole('option')

  await input.fill('/')
  await expect(options.filter({ hasText: '/compact' })).toHaveCount(1)
  await expect(options.filter({ hasText: '/skill:review' })).toHaveCount(1)
  await expect(options.filter({ hasText: '/skill:tidy' })).toHaveCount(1)

  // Skills match on their own name as well as on the literal `skill:` prefix.
  await input.fill('/skill:r')
  await expect(options).toHaveCount(1)
  await expect(options.first()).toContainText('/skill:review')
  await expect(options.first()).toContainText('技能 · 按 checklist 审查改动')

  await page.keyboard.press('Enter')
  await expect(input).toHaveValue('/skill:review ')
  await expect(options).toHaveCount(0)

  await page.keyboard.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) =>
            call.type === 'command' && call.name === 'skill:review' && call.argument === '',
        ),
      ),
    )
    .toBe(true)

  // The turn's injected prompt projects as a skill card, not raw SKILL.md text.
  await emitEvent(page, {
    type: 'message_start',
    message: {
      role: 'user',
      text: 'review',
      skill: true,
      skill_args: 'src/app.ts',
      skill_body: '# Review\ncheck the diff',
    },
  })
  const card = page.locator('[data-skill-card="review"]')
  await expect(card).toBeVisible()
  await expect(card).toContainText('src/app.ts')
  await expect(card).toContainText('check the diff')
})
test('the composer @ menu completes file paths and boxes the reference', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      fsIndex: [
        { path: 'docs', kind: 'dir' },
        { path: 'docs/guide.md', kind: 'file' },
        { path: 'src', kind: 'dir' },
        { path: 'src/main.ts', kind: 'file' },
        { path: 'src/my notes.md', kind: 'file' },
      ],
    }),
  )
  const input = page.locator('[data-composer-plate] textarea')
  const options = page.getByRole('option')

  await input.fill('look at @')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'fs_index'),
      ),
    )
    .toBe(true)
  await expect(options).toHaveCount(5)
  await expect(page.locator('[data-token-kind]')).toHaveCount(0)

  // A fuzzy name hit, not a directory-scoped tree.
  await input.fill('look at @main')
  await expect(options).toHaveCount(1)
  await expect(options.first()).toContainText('src/main.ts')
  await expect(options.first()).toContainText('文件')

  await page.keyboard.press('Enter')
  await expect(input).toHaveValue('look at @src/main.ts ')
  await expect(options).toHaveCount(0)
  await expect(page.locator('[data-token-kind="file"]')).toBeVisible()

  // A path with spaces switches to the quoted form.
  await input.fill('open @my')
  await expect(options).toHaveCount(1)
  await page.keyboard.press('Enter')
  await expect(input).toHaveValue('open @"src/my notes.md" ')
  await expect(page.locator('[data-token-kind="file"]')).toBeVisible()

  await page.keyboard.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() => {
        const call = window.__ZEN_E2E__.calls.find((entry) => entry.type === 'prompt')
        return (call?.text as string | undefined) ?? null
      }),
    )
    .toBe('open @"src/my notes.md"')
})
test('the reasoning level rides on the model chip instead of its own control', async ({ page }) => {
  await boot(page, initialState({ thinking: 'high' }))
  const model = page.locator('[data-menu-trigger="model"]')
  await expect(model).toContainText('deepseek-flash')
  await expect(model).toContainText('high')
  await expect(page.locator('[data-menu-trigger="thinking"]')).toHaveCount(0)

  await model.click()
  const levels = page.getByRole('radiogroup', { name: '思考深度' })
  await expect(levels).toBeVisible()
  await expect(levels.getByRole('radio')).toHaveCount(5)
  await levels.getByRole('radio', { name: 'max' }).click()
  await expect(levels.getByRole('radio', { name: 'max' })).toHaveAttribute('aria-checked', 'true')
  await expect(model).toContainText('max')
})
test('the reasoning levels stay pinned under a scrolling model list', async ({ page }) => {
  const models = Array.from({ length: 30 }, (_, index) => ({
    id: `model-${index}`,
    provider: 'p',
    context_window: 128000,
    max_tokens: 4096,
    reasoning: true,
    supports_vision: false,
    available: true,
    reason: null,
    current: index === 0,
  }))
  await boot(page, initialState({ models, thinking: 'high' }))
  await page.locator('[data-menu-trigger="model"]').click()

  const menu = page.getByRole('menu')
  const levels = page.getByRole('radiogroup', { name: '思考深度' })
  await expect(levels).toBeVisible()
  const menuBox = await menu.boundingBox()
  const levelsBox = await levels.boundingBox()
  assert.ok(menuBox !== null && levelsBox !== null)
  assert.ok(
    levelsBox.y + levelsBox.height <= menuBox.y + menuBox.height + 1,
    'the effort row must sit inside the menu',
  )

  const scrolled = await menu.evaluate((el) => {
    const list = [...el.querySelectorAll('div')].find((node) => {
      const style = getComputedStyle(node)
      return style.overflowY === 'auto' && node.scrollHeight > node.clientHeight
    })
    if (list === undefined) return false
    list.scrollTop = list.scrollHeight
    return true
  })
  assert.equal(scrolled, true, 'the model list must actually scroll')

  const pinnedBox = await levels.boundingBox()
  assert.ok(pinnedBox !== null)
  assert.equal(pinnedBox.y, levelsBox.y, 'the effort row must not move with the list')
})
test('the composer plate spans the transcript band', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await boot(
    page,
    initialState({
      transcript: [{ kind: 'user', id: 'u1', text: '对齐宽度', timestamp: 1 }],
    }),
  )
  const plate = await page.locator('[data-composer-plate]').boundingBox()
  const flow = await page.locator('[data-chat-flow]').boundingBox()
  assert.ok(plate !== null && flow !== null)
  assert.ok(
    Math.abs(plate.width - flow.width) <= 1,
    `plate ${plate.width} vs flow ${flow.width}`,
  )
})
test('dragging the composer top edge grows and shrinks the draft area', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await boot(page, initialState())
  const area = page.locator('[data-composer-plate] textarea')
  const handle = page.getByRole('separator', { name: '调整输入框高度' })
  const metrics = await area.evaluate((element) => {
    const styles = getComputedStyle(element)
    return {
      lineHeight: Number.parseFloat(styles.lineHeight),
      padding:
        Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom),
    }
  })
  const initial = await area.boundingBox()
  assert.ok(initial !== null)

  const dragHandleTo = async (toY: number) => {
    const box = await handle.boundingBox()
    assert.ok(box !== null)
    const x = box.x + box.width / 2
    const y = box.y + box.height / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x, toY, { steps: 6 })
    await page.mouse.up()
  }

  const handleBox = await handle.boundingBox()
  assert.ok(handleBox !== null)
  await dragHandleTo(handleBox.y + handleBox.height / 2 - 96)
  const grown = await area.boundingBox()
  assert.ok(grown !== null)
  assert.ok(grown.height >= initial.height + 90, `${initial.height} -> ${grown.height}`)

  // Pulling far past the floor parks the draft at exactly two text lines.
  await dragHandleTo(700)
  const shrunk = await area.boundingBox()
  assert.ok(shrunk !== null)
  const floor = metrics.lineHeight * 2 + metrics.padding
  assert.ok(Math.abs(shrunk.height - floor) <= 1, `${shrunk.height} vs ${floor}`)

  await handle.dblclick()
  await expect
    .poll(async () => Math.round((await area.boundingBox())?.height ?? 0))
    .toBe(Math.round(initial.height))
})
test('a queued message can be promoted to steering', async ({ page }) => {
  await boot(page, initialState())
  await setPending(page, [
    { id: 7, lane: 'queue', text: '把测试补上', images: 0, created_at: 1 },
  ])
  await expect(page.getByText('把测试补上')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: '升为插话' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'promote' && call.pending_id === 7,
        ),
      ),
    )
    .toBe(true)
  await expect(page.locator('[data-lane="queue"]')).toBeHidden()
  await expect(
    page.locator('[data-lane="steer"]').getByText('把测试补上'),
  ).toBeVisible()
})
test('every queued row takes itself back', async ({ page }) => {
  await boot(page, initialState())
  await setPending(page, [
    { id: 8, lane: 'queue', text: '第一条', images: 0, created_at: 2 },
    { id: 9, lane: 'queue', text: '取回我', images: 0, created_at: 3 },
  ])
  const queue = page.locator('[data-lane="queue"]')
  await expect(queue).toContainText('取回我', { timeout: 10_000 })
  // row-by-row: both rows are listed, each owning its actions
  await expect(queue).toContainText('第一条')

  await queue
    .getByRole('listitem')
    .filter({ hasText: '取回我' })
    .getByRole('button', { name: '取回' })
    .click()

  await expect(page.locator('textarea')).toHaveValue('取回我')
  await expect(queue).not.toContainText('取回我')
  await expect(queue).toContainText('第一条')
})
test('clearing drops the pending dock', async ({ page }) => {
  await boot(page, initialState())
  await setPending(page, [
    { id: 9, lane: 'queue', text: '第一条', images: 0, created_at: 3 },
    { id: 10, lane: 'queue', text: '第二条', images: 0, created_at: 4 },
  ])
  await expect(page.getByText('第二条')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: '清空全部' }).click()
  await expect(page.getByText('第二条')).toBeHidden()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'clear_pending',
        ),
      ),
    )
    .toBe(true)
})
test('the native picker adds images and drops other files', async ({ page }) => {
  await boot(
    page,
    initialState({ dialogSelection: ['/tmp/a.png', '/tmp/b.txt'] }),
  )
  await page.getByRole('button', { name: '添加图片' }).click()
  await expect(page.getByText('a.png')).toBeVisible()
  await expect(page.getByText('b.txt')).toHaveCount(0)
})
