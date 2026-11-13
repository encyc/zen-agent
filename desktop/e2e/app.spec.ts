/** The real React shell in Chromium, driven through a scripted Tauri bridge.
 * No `zen` binary and no Rust side are involved: `invoke`/`listen` are faked
 * with the same command and frame shapes the Tauri bridge uses. */

import assert from 'node:assert/strict'
import { expect, test, type Page } from '@playwright/test'

import { boot, initialState, presetState, type BridgeState } from './harness'
async function emitEvent(page: Page, event: Record<string, unknown>) {
  await page.evaluate((payload) => {
    window.__ZEN_E2E_EMIT__('rpc:frame', {
      event: 'rpc:frame',
      id: 1,
      payload: { frame: { type: 'event', event: payload } },
    })
  }, event)
}

async function setPending(page: Page, items: PendingItemMock[]) {
  await page.evaluate((pending) => {
    window.__ZEN_E2E__.pending = pending
  }, items)
}

const paletteKey = process.platform === 'darwin' ? 'Meta+k' : 'Control+k'
const sidebarKey = process.platform === 'darwin' ? 'Meta+b' : 'Control+b'

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

test('a new project joins the rail and can be removed', async ({ page }) => {
  await boot(page, initialState({ running: false, dialogSelection: '/work/newproj' }))
  await page.getByRole('button', { name: '新建项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="newproj"]')).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.length ?? 0)).toBe(1)
  assert.equal(
    await page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root),
    '/work/newproj',
  )
  await page.locator('[data-sidebar-project-group="newproj"] [data-remove-project]').click()
  await expect(page.locator('[data-sidebar-project-group="newproj"]')).toHaveCount(0)
})

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

test('the work panel opens on files and renders review hunks', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'edit_file',
          args_preview: '{"path":"/src/a.ts"}',
          args: { path: '/src/a.ts' },
          status: 'ok',
          diff: ['@@ -1,2 +1,2 @@', ' keep', '-old', '+new'].join('\n'),
        },
      ],
      fsEntries: {
        '': [
          { name: 'src', path: 'src', kind: 'dir', size: 0, hidden: false },
          { name: 'README.md', path: 'README.md', kind: 'file', size: 12, hidden: false },
        ],
        src: [
          { name: 'a.ts', path: 'src/a.ts', kind: 'file', size: 20, hidden: false },
        ],
      },
      fsFiles: {
        'src/a.ts': {
          path: 'src/a.ts',
          kind: 'text',
          size: 20,
          text: 'const a = 1\nexport { a }\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await panel.getByRole('tab', { name: '审核' }).click()
  await expect(panel.getByRole('tab', { name: '审核' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('a.ts')
  await expect(panel).toContainText('@@ -1,2 +1,2 @@')
  await expect(panel.locator('[data-kind="add"]')).toContainText('new')
  await expect(panel.locator('[data-kind="del"]')).toContainText('old')

  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '文件' }).click()
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await panel.getByRole('button', { name: 'src' }).click()
  await panel.getByRole('button', { name: 'a.ts' }).click()
  await expect(panel).toContainText('export { a }')
  await expect(panel.locator('[data-line="2"]')).toBeVisible()
  await panel.getByRole('button', { name: '返回文件列表' }).click()
  await expect(panel.getByRole('button', { name: 'README.md' })).toBeVisible()

  await page.getByRole('button', { name: '关闭 文件' }).click()
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveCount(0)
  await expect(panel.getByRole('tab', { name: '审核' })).toHaveAttribute('aria-selected', 'true')

  // Review is a plain tab: closing it retires the last tab and the dock with it.
  await page.getByRole('button', { name: '关闭 审核' }).click()
  await expect(panel).toHaveCount(0)

  // Reopening always lands on files, never on the surface the user just shut.
  await page.getByRole('button', { name: '打开工作面板' }).click()
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel.getByRole('button', { name: 'README.md' })).toBeVisible()
  await expect(panel.getByRole('tab', { name: '审核' })).toHaveCount(0)
})

test('a file mentioned in the reply opens in the work panel', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: '改了哪里？', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: '我改了 `src/a.ts`，不是 `store.messages`。',
          timestamp: 2,
        },
      ],
      fsFiles: {
        'src/a.ts': {
          path: 'src/a.ts',
          kind: 'text',
          size: 20,
          text: 'const a = 1\nexport { a }\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )

  // Only the real path becomes a link; the dotted identifier stays text.
  const ref = page.locator('[data-file-path="src/a.ts"]')
  await expect(ref).toHaveText('src/a.ts')
  await expect(page.locator('[data-file-path="store.messages"]')).toHaveCount(0)

  await ref.click()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('export { a }')
  await expect(panel.getByRole('button', { name: '返回文件列表' })).toBeVisible()

  // Back to the tree, then the same reference reopens the viewer.
  await panel.getByRole('button', { name: '返回文件列表' }).click()
  await expect(panel.getByRole('button', { name: '返回文件列表' })).toHaveCount(0)
  await ref.click()
  await expect(panel).toContainText('export { a }')
})

test('the file viewer highlights source with the matching grammar', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: '入口在哪？', timestamp: 1 },
        { kind: 'assistant', id: 'a1', text: '入口在 `src/main.py`。', timestamp: 2 },
      ],
      fsFiles: {
        'src/main.py': {
          path: 'src/main.py',
          kind: 'text',
          size: 44,
          text: 'def greet(name):\n    return f"hi {name}"\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )

  await page.locator('[data-file-path="src/main.py"]').click()
  const panel = page.locator('[data-work-panel]')
  const code = panel.locator('[data-lang="python"]')
  await expect(code).toBeVisible()
  await expect(code).toContainText('return f"hi {name}"')
  // The grammar loads lazily; the keyword paints with a theme color once ready.
  await expect(code.locator('[data-line="1"] span[style*="color"]', { hasText: 'def' })).toHaveCount(1)
})

test('a folder mentioned in the reply lands in the tree, never on a new page', async ({
  page,
}) => {
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: 'rpc 在哪？', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: '目录是 [zenagent/cli/rpc](zenagent/cli/rpc)。',
          timestamp: 2,
        },
      ],
      fsEntries: {
        '': [{ name: 'zenagent', path: 'zenagent', kind: 'dir', size: 0, hidden: false }],
        zenagent: [{ name: 'cli', path: 'zenagent/cli', kind: 'dir', size: 0, hidden: false }],
        'zenagent/cli': [
          { name: 'rpc', path: 'zenagent/cli/rpc', kind: 'dir', size: 0, hidden: false },
        ],
        'zenagent/cli/rpc': [
          {
            name: 'host.py',
            path: 'zenagent/cli/rpc/host.py',
            kind: 'file',
            size: 6,
            hidden: false,
          },
        ],
      },
      fsFiles: {
        'zenagent/cli/rpc/host.py': {
          path: 'zenagent/cli/rpc/host.py',
          kind: 'text',
          size: 6,
          text: 'x = 1\n',
          total_lines: 1,
          truncated: false,
        },
      },
    }),
  )

  const ref = page.locator('[data-file-path="zenagent/cli/rpc"]')
  await expect(ref).toHaveText('zenagent/cli/rpc')
  await ref.click()

  // The click must never navigate the webview: the shell stays mounted.
  expect(new URL(page.url()).pathname).toBe('/')
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
  // The folder opened in place: its ancestors and its contents are in the tree.
  await expect(panel.getByRole('button', { name: 'zenagent' })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'host.py' })).toBeVisible()
})

test('an absolute folder path and a slashed folder token both reveal in the tree', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work/zen'))
  await boot(
    page,
    initialState({
      transcript: [
        { kind: 'user', id: 'u1', text: 'agent 在哪？', timestamp: 1 },
        {
          kind: 'assistant',
          id: 'a1',
          text: '目录 `zenagent/agent/`，见 [rpc](/work/zen/zenagent/agent)。',
          timestamp: 2,
        },
      ],
      fsEntries: {
        '': [{ name: 'zenagent', path: 'zenagent', kind: 'dir', size: 0, hidden: false }],
        zenagent: [
          { name: 'agent', path: 'zenagent/agent', kind: 'dir', size: 0, hidden: false },
        ],
        'zenagent/agent': [
          {
            name: 'core.py',
            path: 'zenagent/agent/core.py',
            kind: 'file',
            size: 6,
            hidden: false,
          },
        ],
      },
      fsFiles: {
        'zenagent/agent/core.py': {
          path: 'zenagent/agent/core.py',
          kind: 'text',
          size: 6,
          text: 'x = 1\n',
          total_lines: 1,
          truncated: false,
        },
      },
    }),
  )

  // A trailing slash links a folder; an absolute link is a reference too.
  await expect(page.locator('[data-file-path="zenagent/agent"]')).toHaveText(
    'zenagent/agent/',
  )
  const absolute = page.locator('[data-file-path="/work/zen/zenagent/agent"]')
  await expect(absolute).toHaveText('rpc')

  await absolute.click()
  const panel = page.locator('[data-work-panel]')
  // Folded to the project-relative key: the folder reveals in place instead
  // of failing the ancestor-by-ancestor read.
  await expect(panel.getByRole('button', { name: 'core.py' })).toBeVisible()
  await expect(panel).not.toContainText('读取失败')
})

test('the file tree paints type badges and the viewer shows a breadcrumb', async ({ page }) => {
  await boot(
    page,
    initialState({
      fsEntries: {
        '': [
          { name: 'src', path: 'src', kind: 'dir', size: 0, hidden: false },
          { name: 'README.md', path: 'README.md', kind: 'file', size: 8, hidden: false },
        ],
        src: [
          { name: 'a.ts', path: 'src/a.ts', kind: 'file', size: 20, hidden: false },
          { name: 'b.py', path: 'src/b.py', kind: 'file', size: 6, hidden: false },
        ],
      },
      fsFiles: {
        'src/a.ts': {
          path: 'src/a.ts',
          kind: 'text',
          size: 20,
          text: 'const a = 1\nexport { a }\n',
          total_lines: 2,
          truncated: false,
        },
      },
    }),
  )

  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await expect(panel.locator('[data-tone="blue"]', { hasText: 'MD' })).toBeVisible()
  await panel.getByRole('button', { name: 'src' }).click()
  await expect(panel.locator('[data-tone="blue"]', { hasText: 'TS' })).toBeVisible()
  await expect(panel.locator('[data-tone="blue"]', { hasText: 'PY' })).toBeVisible()

  await panel.getByRole('button', { name: 'a.ts' }).click()
  const crumbs = panel.locator('[data-breadcrumb]')
  await expect(crumbs).toContainText('src')
  await expect(crumbs.locator('[data-crumb-last]')).toHaveText('a.ts')
})

test('the worktree tab lists worktrees and marks the current one', async ({ page }) => {
  await boot(
    page,
    initialState({
      worktrees: {
        root: '/work/p',
        is_repo: true,
        is_worktree: false,
        main: '/work/p',
        branch: 'main',
        worktrees: [
          {
            path: '/work/p',
            head: 'abc12345',
            branch: 'main',
            bare: false,
            detached: false,
            current: true,
          },
          {
            path: '/work/p-feature',
            head: 'def67890',
            branch: 'feature',
            bare: false,
            detached: false,
            current: false,
          },
        ],
        status: { branch: 'main', staged: 0, dirty: 2, untracked: 1, clean: false },
      },
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '工作树' }).click()
  await expect(panel.getByRole('tab', { name: '工作树' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('feature')
  await expect(panel).toContainText('/work/p-feature')
  await expect(panel).toContainText('当前')
  await expect(panel).toContainText('3 个未提交变更')
  await expect(panel.getByRole('button', { name: '切换' })).toHaveCount(1)
  await page.getByRole('button', { name: '关闭工作面板' }).click()
  await expect(page.locator('[data-work-panel]')).toHaveCount(0)
})


test('a task row opens the subagent conversation in the work panel', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'delegate', timestamp: 1 },
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'task',
          args_preview: '{"name":"Researcher"}',
          args: { name: 'Researcher', prompt: 'find x' },
          status: 'ok',
          result_text: 'done',
          duration_ms: 1200,
        },
        { kind: 'assistant', id: 'a1', text: 'handled', timestamp: 2 },
      ],
      subagents: [
        {
          call_id: 'c1',
          name: 'Researcher',
          status: 'ok',
          duration_ms: 1200,
          cost: 0.0021,
          tokens: 1500,
          has_transcript: true,
          transcript_truncated: false,
        },
      ],
      subagentTranscripts: {
        c1: [
          { kind: 'user', text: 'find x', timestamp: 1 },
          {
            id: 'ct1',
            kind: 'tool',
            call_id: 'ct1',
            name: 'bash',
            args_preview: '{"command":"rg timeout"}',
            args: { command: 'rg timeout' },
            status: 'ok',
            result_text: 'gateway.py:42',
            duration_ms: 30,
          },
          { kind: 'assistant', text: 'Found it in gateway.py', timestamp: 2 },
        ],
      },
    }),
  )
  const task = page.locator('[data-tool="task"] button')
  await expect(task).toHaveAttribute('title', '打开子智能体过程')
  await task.click()

  const panel = page.locator('[data-work-panel]')
  await expect(panel.getByRole('tab', { name: /Researcher/ })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  const transcript = page.getByTestId('subagent-transcript')
  await expect(transcript).toContainText('find x')
  await expect(transcript).toContainText('Found it in gateway.py')
  await transcript.locator('[data-tool="bash"] button').click()
  await expect(transcript).toContainText('gateway.py:42')
  await expect(transcript).toContainText('已完成')
  await expect(transcript).toContainText('1.5k tok')
  // The parent row stays one line: the conversation lives in the panel only.
  await expect(page.locator('[data-tool="task"] pre')).toHaveCount(0)

  await page.getByRole('button', { name: '关闭 Researcher' }).click()
  await expect(panel.getByRole('tab', { name: /Researcher/ })).toHaveCount(0)
  await expect(panel.getByRole('tab', { name: '文件' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
})

test('hovering the context ring shows the composition card', async ({ page }) => {
  await boot(page, initialState())
  const card = page.getByTestId('context-card')
  await expect(card).toHaveCount(0)

  await page.getByTestId('context-meter').hover()
  await expect(card).toBeVisible()
  await expect(card).toContainText('已用 32K')
  await expect(card).toContainText('25%')
  await expect(card).toContainText('32K / 128K')
  await expect(card).toContainText('系统提示词')
  await expect(card).toContainText('≈3.2K · 2.5%')
  await expect(card).toContainText('工具 schema')
  await expect(card).toContainText('≈4.8K · 3.8%')
  await expect(card).toContainText('会话')
  await expect(card).toContainText('≈24K · 18.8%')
  await expect(card).toContainText('空闲')
  await expect(card).toContainText('≈96K · 75.0%')

  // Reading takes time: moving onto the card must keep it open.
  await card.hover()
  await expect(card).toBeVisible()

  await page.mouse.move(5, 5)
  await expect(card).toHaveCount(0)
})

test('the shell keeps the sidebar full height and docks the composer inside the center column', async ({
  page,
}) => {
  await boot(page, initialState())
  const sidebar = await page.locator('[data-shell-sidebar]').boundingBox()
  const viewport = page.viewportSize()
  assert.ok(sidebar !== null && viewport !== null)
  assert.equal(Math.round(sidebar.height), viewport.height)
  assert.equal(Math.round(sidebar.y), 0)
  const composer = await page.locator('[data-shell-composer]').boundingBox()
  const center = await page.locator('[data-shell-center]').boundingBox()
  assert.ok(composer !== null && center !== null)
  assert.ok(composer.x >= center.x)
  assert.ok(composer.x + composer.width <= center.x + center.width + 1)
  assert.ok(composer.y + composer.height <= center.y + center.height + 1)
})

test('collapsing the sidebar gives the conversation the full width', async ({ page }) => {
  await boot(page, initialState())
  const center = page.locator('[data-shell-center]')
  const open = await center.boundingBox()
  assert.ok(open !== null)
  assert.ok(open.x > 0, `center starts at ${open.x}`)

  await page.getByRole('button', { name: 'toggle sidebar' }).click()
  await expect.poll(async () => (await center.boundingBox())?.x ?? -1).toBe(0)
  const collapsed = await center.boundingBox()
  const viewport = page.viewportSize()
  assert.ok(collapsed !== null && viewport !== null)
  assert.equal(Math.round(collapsed.width), viewport.width)

  await page.keyboard.press(sidebarKey)
  await expect.poll(async () => (await center.boundingBox())?.x ?? -1).toBe(Math.round(open.x))
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

test('the topbar shows the project crumb and renames the current session inline', async ({
  page,
}) => {
  await boot(page, initialState())
  await expect(page.locator('[data-topbar-project]')).toContainText('p')
  await expect(page.locator('[data-topbar-project] svg')).toBeVisible()
  await expect(page.locator('[data-topbar-sep]')).toHaveText('/')
  await page.getByRole('button', { name: '重命名会话标题' }).click()
  const input = page.getByRole('textbox', { name: '会话标题' })
  await input.fill('新的标题')
  await input.press('Enter')
  await expect(page.locator('[data-topbar-title]')).toHaveText('新的标题')
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'rename' && call.argument === '新的标题',
        ),
      ),
    )
    .toBe(true)
})

test('a settled turn keeps its tool steps expanded by default', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'go', timestamp: 1 },
        { kind: 'assistant', id: 'a1', text: 'done', timestamp: 2, duration_ms: 900 },
        {
          id: 't1',
          kind: 'tool',
          call_id: 'c1',
          name: 'read_file',
          args_preview: '{"path":"a.ts"}',
          args: { path: 'a.ts' },
          status: 'ok',
          result_text: 'file',
          duration_ms: 400,
        },
        {
          id: 't2',
          kind: 'tool',
          call_id: 'c2',
          name: 'bash',
          args_preview: '{"command":"ls"}',
          args: { command: 'ls' },
          status: 'ok',
          result_text: 'a.ts',
          duration_ms: 600,
        },
      ],
    }),
  )
  const fold = page.getByRole('button', { name: /已处理.*步骤/ })
  await expect(fold).toBeVisible()
  await expect(fold).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('read_file')).toBeVisible()
  await fold.click()
  await expect(fold).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByText('read_file')).toHaveCount(0)
})

test('a long transcript mounts a bounded window and reveals earlier turns on demand', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  const transcript = Array.from({ length: 150 }, (_, index) => [
    { kind: 'user', id: `u${index}`, text: `第 ${index} 轮提问`, timestamp: index * 2 + 1 },
    { kind: 'assistant', id: `a${index}`, text: `回复 ${index}`, timestamp: index * 2 + 2 },
  ]).flat()
  await boot(page, initialState({ running: false, transcript }))

  const rows = page.locator('[data-chat-flow] [data-minimap-id]')
  const earlier = page.locator('[data-earlier]')
  const dashes = page.locator('[data-minimap-dash]')
  await expect(rows).toHaveCount(60)
  // The rail covers the whole session, including turns not yet mounted.
  await expect(dashes).toHaveCount(150)
  await expect(earlier).toHaveText('显示更早的 240 条')
  // The trailing window keeps the newest turn and drops the oldest rows.
  await expect(page.locator('[data-minimap-id="a149"]')).toHaveCount(1)
  await expect(page.locator('[data-minimap-id="u0"]')).toHaveCount(0)

  await earlier.click()
  await expect(rows).toHaveCount(100)
  await expect(earlier).toHaveText('显示更早的 200 条')

  // Clicking a dash above the mounted window mounts everything down to it.
  await dashes.first().click()
  await expect(rows).toHaveCount(300)
  await expect(page.locator('[data-minimap-id="u0"]')).toHaveCount(1)
})

test('the width handles resize the band and the minimap marks each turn', async ({ page }) => {
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
  await expect(page.locator('[data-minimap-dash]').first()).toBeVisible()
  // Only the user's own turns get a dash; the assistant reply does not.
  await expect(page.locator('[data-minimap-dash]')).toHaveCount(1)
  const flow = page.locator('[data-chat-flow]')
  const before = await flow.evaluate((el) => el.getBoundingClientRect().width)
  assert.equal(Math.round(before), 760)
  const handle = page.locator('[data-chat-width-handles] [data-side="right"]')
  const box = await handle.boundingBox()
  assert.ok(box !== null)
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 - 60, box.y + box.height / 2)
  await page.mouse.up()
  const after = await flow.evaluate((el) => el.getBoundingClientRect().width)
  assert.ok(after < before)
  await expect(handle).toHaveAttribute('aria-valuenow', String(Math.round(after)))
})

test('the current row menu runs undo and compact through the session op', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '撤销上一轮' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'undo'),
      ),
    )
    .toBe(true)
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '压缩上下文' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'compact'),
      ),
    )
    .toBe(true)
})

test('the sidebar renders pinned, sessions and collapsible project groups', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-sidebar-section="sessions"]')).toBeVisible()
  await expect(page.locator('[data-project-group="p"]')).toBeVisible()
  const header = page.getByRole('button', { name: '项目 p' })
  await expect(header).toHaveAttribute('aria-expanded', 'true')
  await header.click()
  await expect(header).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toBeHidden()
  await header.click()
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toBeVisible()
})

test('a filtered, collapsed project stays collapsed and loads more on demand', async ({ page }) => {
  await boot(page, initialState({ projectSessions: 12 }))
  const header = page.getByRole('button', { name: '项目 p' })
  const visibleRows = page.locator('[data-sidebar-project-group="p"] [data-session-path]:visible')
  await expect(visibleRows).toHaveCount(10)
  await expect(page.getByRole('button', { name: '加载更多（2）' })).toBeVisible()
  await page.getByRole('button', { name: '加载更多（2）' }).click()
  await expect(visibleRows).toHaveCount(12)

  await header.click()
  await page.getByPlaceholder('搜索会话').fill('额外会话')
  await expect(header).toHaveAttribute('aria-expanded', 'false')
  await expect(visibleRows).toHaveCount(0)
  await page.getByPlaceholder('搜索会话').fill('')
  await expect(header).toHaveAttribute('aria-expanded', 'false')
  await header.click()
  await expect(visibleRows).toHaveCount(12)
})

test('opening another project’s session switches the runtime there with that session', async ({ page }) => {
  await boot(page, initialState())
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'list' && call.scope === 'all'),
      ),
    )
    .toBe(true)
  await expect(page.locator('[data-project-group="q"]')).toBeVisible()
  const cross = page.locator('[data-session-path="/sessions/c.jsonl"]')
  await expect(cross).not.toHaveAttribute('aria-disabled', 'true')
  await cross.click()
  // The resident host takes the request: one project op, no replacement
  // process, no separate resume round-trip for a cold slot.
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return { root: op?.root, resume: op?.resume }
      }),
    )
    .toEqual({ root: '/work/q', resume: '/sessions/c.jsonl' })
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some(
        (call) => call.type === 'session' && call.op === 'resume',
      ),
    ),
    false,
  )
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)

  // A late exit from a pid the shell no longer tracks must not end the run;
  // the tracked pid still does.
  const live = await page.evaluate(() => window.__ZEN_E2E__.starts[0]?.pid ?? 0)
  const emitExit = (pid: number) =>
    page.evaluate(
      (value) =>
        window.__ZEN_E2E_EMIT__('rpc:exit', {
          event: 'rpc:exit',
          id: 1,
          payload: { code: 0, pid: value },
        }),
      pid,
    )
  await emitExit(live + 999)
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.getByText(/会话进程已退出/)).toHaveCount(0)

  await emitExit(live)
  await expect(page.getByText(/会话进程已退出/)).toBeVisible()
})

test('a project discovered from sessions can start its own session', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-project-group="q"]')).toBeVisible()
  await page.getByRole('button', { name: '在 q 中新建会话' }).click()
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return op === undefined
          ? null
          : { root: op.root ?? null, resume: op.resume ?? null }
      }),
    )
    .toEqual({ root: '/work/q', resume: null })
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'new'),
      ),
    )
    .toBe(true)
})

test('the current row menu pins and renames; other rows omit both actions', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '置顶' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'pin' && call.argument === 'on',
        ),
      ),
    )
    .toBe(true)

  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '重命名' }).click()
  const input = page.locator('[data-session-path="/sessions/a.jsonl"] input')
  await input.fill('行内标题')
  await input.press('Enter')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'rename' && call.argument === '行内标题',
        ),
      ),
    )
    .toBe(true)

  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '置顶' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: '重命名' })).toHaveCount(0)
})

test('delete needs two clicks on the same row', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await page.getByRole('menuitem', { name: '删除' }).click()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'delete'),
    ),
    false,
  )
  // Closing the menu drops the arm: reopen and the label is plain delete again.
  await page.keyboard.press('Escape')
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '删除？' })).toHaveCount(0)
  await page.getByRole('menuitem', { name: '删除' }).click()
  await page.getByRole('menuitem', { name: '删除？' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'delete' && call.path === '/sessions/b.jsonl',
        ),
      ),
    )
    .toBe(true)
})

test('the footer opens a session file and exports a chosen format', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: '/tmp/imported.jsonl' }))
  await page.getByRole('button', { name: '打开会话文件…' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'import' && call.path === '/tmp/imported.jsonl',
        ),
      ),
    )
    .toBe(true)

  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 HTML' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) =>
            call.type === 'session' &&
            call.op === 'export' &&
            call.path === '/sessions/a.jsonl' &&
            call.format === 'html',
        ),
      ),
    )
    .toBe(true)
  await expect(page.getByRole('alert')).toHaveText('已导出到 /sessions/a.html')
})

test('a failed export surfaces in the notice banner', async ({ page }) => {
  await boot(page, initialState({ failExport: true }))
  await page.getByRole('button', { name: '导出会话' }).click()
  await page.getByRole('menuitem', { name: '导出为 Markdown' }).click()
  // The banner belongs to the composer dock: it must clear the floating plate
  // instead of being painted underneath it at the window's bottom edge.
  await expect(page.locator('[data-shell-composer] [role="alert"]')).toHaveText('导出失败')
  const bannerBox = await page.getByRole('alert').boundingBox()
  const plateBox = await page.locator('[data-composer-plate]').boundingBox()
  assert.ok(bannerBox !== null && plateBox !== null)
  assert.ok(
    bannerBox.y + bannerBox.height <= plateBox.y,
    `banner bottom ${bannerBox.y + bannerBox.height} must sit above the plate top ${plateBox.y}`,
  )
})

test('a cancelled open dialog sends no import', async ({ page }) => {
  await boot(page, initialState({ dialogSelection: null }))
  await page.getByRole('button', { name: '打开会话文件…' }).click()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'import'),
    ),
    false,
  )
})

test('fork is only offered on the current session row', async ({ page }) => {
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '从此分叉' })).toHaveCount(1)
  await page.locator('[data-session-path="/sessions/b.jsonl"] [data-action="menu"]').click()
  await expect(page.getByRole('menuitem', { name: '从此分叉' })).toHaveCount(0)
})

test('escaping a row menu neither cancels the run nor traps the keyboard', async ({ page }) => {
  await boot(page, initialState())
  const trigger = page.locator('[data-session-path="/sessions/a.jsonl"] [data-action="menu"]')
  await trigger.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem', { name: '重命名' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: '置顶' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(trigger).toBeFocused()
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'cancel'),
    ),
    false,
  )

  await page.getByRole('button', { name: '导出会话' }).click()
  await expect(page.getByRole('menuitem', { name: '导出为 HTML' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'cancel'),
    ),
    false,
  )
})

test('a recorded project root stays openable without a current session', async ({ page }) => {
  await presetState(page, initialState({ noCurrent: true }))
  await page.goto('/')
  await expect(page.locator('[data-session-path="/sessions/a.jsonl"]')).toBeVisible()
  await expect(page.locator('[data-session-path="/sessions/a.jsonl"]')).not.toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).not.toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('[data-session-path="/sessions/c.jsonl"]')).not.toHaveAttribute('aria-disabled', 'true')
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

test('dequeue takes back the newest queued text the dock headlines', async ({ page }) => {
  await boot(page, initialState())
  await setPending(page, [
    { id: 8, lane: 'queue', text: '第一条', images: 0, created_at: 2 },
    { id: 9, lane: 'queue', text: '取回我', images: 0, created_at: 3 },
  ])
  await expect(page.getByText('取回我')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('第一条')).toBeHidden()
  await page.getByRole('button', { name: '取回' }).click()
  await expect(page.locator('textarea')).toHaveValue('取回我')
  await expect(page.getByText('第一条')).toBeVisible()
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

test('the topbar terminal opens a shell in the work panel', async ({ page }) => {
  // A fixed light start makes the palette flip deterministic.
  await page.addInitScript(() => localStorage.setItem('zen.theme', JSON.stringify('light')))
  await boot(page, initialState())
  // The old new-session seat is gone; the terminal owns the topbar's right edge.
  const topbar = page.getByRole('toolbar', { name: '会话' })
  await expect(topbar.getByRole('button', { name: '新会话' })).toHaveCount(0)
  await page.getByRole('button', { name: '终端' }).click()
  await expect(page.locator('[data-work-panel]')).toHaveAttribute('data-tab', 'terminal')

  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'terminal' && call.op === 'start'),
      ),
    )
    .toBe(true)
  // React's dev double-mount starts one shell per pass; the live pane owns the
  // last token.
  const session = await page.evaluate(() =>
    String(
      window.__ZEN_E2E__.calls
        .filter((call) => call.type === 'terminal' && call.op === 'start')
        .at(-1)?.session ?? '',
    ),
  )
  await page.evaluate(
    ({ token }) => {
      window.__ZEN_E2E_EMIT__('rpc:frame', {
        event: 'rpc:frame',
        id: 1,
        payload: {
          frame: {
            type: 'event',
            event: { type: 'terminal', session: token, kind: 'output', data: 'zen@work % ' },
          },
        },
      })
    },
    { token: session },
  )
  await expect(page.locator('[data-terminal-host]')).toContainText('zen@work %')

  // The xterm pane follows the shell palette: the footer theme cycle repaints
  // the viewport through the body attributes, without remounting the shell.
  // xterm paints the theme background on its scrollable element.
  const viewport = page.locator('[data-terminal-host] .xterm-scrollable-element')
  const viewportColor = () =>
    viewport.evaluate((node) => getComputedStyle(node).backgroundColor)
  await expect.poll(viewportColor).toBe('rgb(255, 255, 255)')
  // xterm.css still paints the legacy scroll viewport #000; it has to follow the
  // palette as well, or the sub-row remainder FitAddon leaves at the bottom
  // shows through as a black seam.
  const legacyViewport = page.locator('[data-terminal-host] .xterm-viewport')
  const legacyColor = () =>
    legacyViewport.evaluate((node) => getComputedStyle(node).backgroundColor)
  await expect.poll(legacyColor).toBe('rgb(255, 255, 255)')
  await page.getByRole('button', { name: '外观: 浅色' }).click()
  await expect(page.locator('body')).toHaveAttribute('data-ds-dark-theme', '')
  await expect.poll(viewportColor).toBe('rgb(21, 21, 23)')
  await expect.poll(legacyColor).toBe('rgb(21, 21, 23)')

  await page.locator('[data-terminal-host]').click()
  await page.keyboard.type('ls')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls
          .filter((call) => call.type === 'terminal' && call.op === 'write')
          .map((call) => String(call.data ?? ''))
          .join(''),
      ),
    )
    .toBe('ls')

  // Closing the tab tells the host to end the shell.
  await page.getByRole('button', { name: '关闭 终端' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'terminal' && call.op === 'close'),
      ),
    )
    .toBe(true)
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

test('limit settings lists grouped knobs and reports the override', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '参数与预算' }).click()

  await expect(page.getByRole('heading', { name: '配置文件' })).toBeVisible()
  await expect(page.locator('[data-group="skills"]')).toBeVisible()
  await expect(page.locator('[data-group="subagents"]')).toBeVisible()
  await expect(page.locator('[data-group="tools"]')).toBeVisible()

  // Read-only by default: values render as text, no editor in sight.
  const turns = page.locator('[data-knob="limits.agent.max_turns"]')
  await expect(turns).toContainText('100')
  await expect(turns).toContainText('默认 100')
  await expect(turns).toContainText('--max-turns 20')
  await expect(turns.getByRole('textbox')).toHaveCount(0)
  await expect(page.locator('[data-knob="task.max_cost"]')).toContainText('—')
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
  const turns = page.locator('[data-knob="limits.agent.max_turns"]')
  await index.getByRole('textbox').fill('16384')
  await turns.getByRole('textbox').fill('150')
  // Two changed knobs, one save: the button names the batch.
  await page.getByRole('button', { name: '保存（2 项改动）' }).click()

  const calls = await page.evaluate(() => window.__ZEN_E2E__.calls)
  const sets = calls
    .filter((call) => call.type === 'limits' && call.op === 'set')
    .map(({ type, op, key, value }) => ({ type, op, key, value }))
  assert.deepEqual(sets, [
    { type: 'limits', op: 'set', key: 'limits.skills.max_index_bytes', value: 16384 },
    { type: 'limits', op: 'set', key: 'limits.agent.max_turns', value: 150 },
  ])
  // Back to read-only, values now read from the saved state.
  await expect(page.getByRole('button', { name: '修改' })).toBeVisible()
  await expect(turns).toContainText('150')
})

test('limit settings: one bad value blocks the whole save', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '参数与预算' }).click()

  await page.getByRole('button', { name: '修改' }).click()
  const index = page.locator('[data-knob="limits.skills.max_index_bytes"]')
  const turns = page.locator('[data-knob="limits.agent.max_turns"]')
  await index.getByRole('textbox').fill('16384')
  await turns.getByRole('textbox').fill('99999999') // over the knob's max
  const save = page.getByRole('button', { name: '保存（2 项改动）' })
  await expect(save).toBeDisabled()
  await expect(turns.getByRole('textbox')).toHaveValue('99999999')
  await expect(turns).toHaveAttribute('data-invalid', 'true')
  await expect(page.getByText('有无效值，修正后才能保存')).toBeVisible()

  // Nothing was sent, and fixing the row re-enables the same button.
  await turns.getByRole('textbox').fill('150')
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

test('zen settings own the fleet knobs and Parameters leaves them out', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.getByRole('button', { name: '设置' }).click()

  // Zen/Swarm tables belong to their own pages; Parameters stays kernel-only.
  await page.getByRole('button', { name: '参数与预算' }).click()
  await expect(page.locator('[data-knob="zen.max_concurrent"]')).toHaveCount(0)

  await page.getByRole('button', { name: 'Zen', exact: true }).click()
  const panel = page.locator('[data-knob-table="zen"]')
  await expect(panel).toBeVisible()
  await expect(page.locator('[data-knob="zen.max_concurrent"]')).toContainText('3')
  // The per-run watchdog lives in `[task]` but is presented with the fleet.
  await expect(page.locator('[data-knob="task.timeout_s"]')).toContainText('900')

  await panel.getByRole('button', { name: '修改' }).click()
  const mode = page.locator('[data-knob="zen.worker_mode"]').getByRole('combobox')
  await expect(mode).toHaveValue('inherit')
  await mode.selectOption('auto')
  await panel.getByRole('button', { name: '保存（1 项改动）' }).click()

  const sets = (await page.evaluate(() => window.__ZEN_E2E__.calls))
    .filter((call) => call.type === 'limits' && call.op === 'set')
    .map(({ key, value }) => ({ key, value }))
  assert.deepEqual(sets, [{ key: 'zen.worker_mode', value: 'auto' }])
  await expect(page.locator('[data-knob="zen.worker_mode"]')).toContainText('auto')
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

test('the session and project sections collapse independently and persist', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.getByPlaceholder('搜索会话')).toBeVisible()
  await page.getByRole('button', { name: '折叠会话' }).click()
  await expect(page.getByPlaceholder('搜索会话')).toHaveCount(0)
  await expect(page.locator('[data-sidebar-project-group="p"]')).toBeVisible()
  await page.getByRole('button', { name: '折叠项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="p"]')).toHaveCount(0)

  // With both sections folded the footer owns the leftover space, so the icon
  // row stays on the sidebar's bottom edge instead of riding up under it.
  await expect(page.locator('[data-sidebar-footer]')).toBeVisible()
  const bottomGap = await page.evaluate(() => {
    const sidebar = document.querySelector('[data-shell-sidebar]')!.getBoundingClientRect()
    const footer = document.querySelector('[data-sidebar-footer]')!.getBoundingClientRect()
    return Math.round(sidebar.bottom - footer.bottom)
  })
  expect(Math.abs(bottomGap)).toBeLessThanOrEqual(1)

  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.getByPlaceholder('搜索会话')).toHaveCount(0)
  await expect(page.locator('[data-sidebar-project-group="p"]')).toHaveCount(0)

  await page.getByRole('button', { name: '展开会话' }).click()
  await expect(page.getByPlaceholder('搜索会话')).toBeVisible()
  await page.getByRole('button', { name: '展开项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="p"]')).toBeVisible()
})

test('a palette session from another project switches the runtime and resumes', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const input = page.getByRole('dialog').getByRole('combobox')
  await input.fill('另一个项目')
  const option = page.getByRole('option', { name: /另一个项目/ })
  await expect(option).not.toHaveAttribute('aria-disabled', 'true')
  await option.click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect
    .poll(() =>
      page.evaluate(() => {
        const op = window.__ZEN_E2E__.projectOps?.at(-1)
        return { root: op?.root, resume: op?.resume }
      }),
    )
    .toEqual({ root: '/work/q', resume: '/sessions/c.jsonl' })
})

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

test('a project switch keeps the shell mounted and shows a progress strip', async ({ page }) => {
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
  await boot(page, initialState({ switchDelayMs: 800 }))
  await expect(page.locator('[data-shell-composer]')).toBeVisible()

  await page.getByRole('button', { name: '在 q 中新建会话' }).click()

  // Mid-switch the runtime is being replaced, the shell is not: no boot
  // screen, no unmounted sidebar, just a strip saying what is happening.
  await expect(page.locator('[data-switching="/work/q"]')).toBeVisible()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.getByText('正在启动 zen rpc…')).toHaveCount(0)

  await expect(page.locator('[data-switching]')).toHaveCount(0)
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root))
    .toBe('/work/q')
})

test('switching back to a project paints its cached transcript before revalidating', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.last.cwd', '/work/p')
  })
  await boot(
    page,
    initialState({
      transcripts: {
        '/work/p': [{ kind: 'user', id: 'p1', text: 'P 项目的对话', timestamp: 1 }],
        '/work/q': [{ kind: 'user', id: 'q1', text: 'Q 项目的对话', timestamp: 1 }],
      },
      transcriptDelayMs: 800,
    }),
  )
  await expect(page.getByText('P 项目的对话')).toBeVisible()

  await page.locator('[data-session-path="/sessions/c.jsonl"]').click()
  await expect(page.getByText('Q 项目的对话')).toBeVisible()

  // What the server would revalidate now carries one more row, so the cached
  // pane and the fresh snapshot differ.
  await page.evaluate(() => {
    const state = window.__ZEN_E2E__
    state.transcripts = state.transcripts ?? {}
    state.transcripts['/work/p'] = [
      { kind: 'user', id: 'p1', text: 'P 项目的对话', timestamp: 1 },
      { kind: 'user', id: 'p2', text: '后台重新校验的行', timestamp: 2 },
    ]
  })
  await page.locator('[data-session-path="/sessions/a.jsonl"]').click()

  // Cached paint lands immediately; the delayed snapshot arrives after.
  await expect(page.getByText('P 项目的对话')).toBeVisible()
  await expect(page.getByText('后台重新校验的行')).toHaveCount(0)
  await expect(page.getByText('后台重新校验的行')).toBeVisible()
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)
})

test('a warm project switch still resumes the requested session', async ({ page }) => {
  await boot(page, initialState())
  // b: cold slot for /work/p, resumed by the project op.
  await page.locator('[data-session-path="/sessions/b.jsonl"]').click()
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root))
    .toBe('/work/p')
  // c: cold slot for /work/q.
  await page.locator('[data-session-path="/sessions/c.jsonl"]').click()
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.projectOps?.at(-1)?.root))
    .toBe('/work/q')

  // Back to p is warm: the host keeps b, so the shell follows up with a resume.
  await page.locator('[data-session-path="/sessions/a.jsonl"]').click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls
          .filter((call) => call.type === 'session' && call.op === 'resume')
          .map((call) => call.path),
      ),
    )
    .toEqual(['/sessions/a.jsonl'])
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)
})

test('switching sessions in one project resumes without a project switch', async ({
  page,
}) => {
  // Both a and b live in /work/p; the switch must stay inside its slot.
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work/p'))
  await boot(page, initialState())
  await page.locator('[data-session-path="/sessions/b.jsonl"]').click()
  // Same resident runtime: the shell addresses the session directly, and the
  // host keeps whatever the other session is doing.
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls
          .filter((call) => call.type === 'session' && call.op === 'resume')
          .map((call) => call.path),
      ),
    )
    .toEqual(['/sessions/b.jsonl'])
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.projectOps?.length ?? 0), 0)
  assert.equal(await page.evaluate(() => window.__ZEN_E2E__.starts.length), 1)
})

test('a sibling session frame cannot paint the visible transcript', async ({ page }) => {
  await boot(page, initialState())
  // The session list lands asynchronously after boot; its rows prove the
  // active session is pinned before the frame assertions below.
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toBeVisible()

  const emit = (session: string, text: string) =>
    page.evaluate(
      ({ session, text }) => {
        window.__ZEN_E2E_EMIT__('rpc:frame', {
          event: 'rpc:frame',
          id: 1,
          payload: {
            frame: {
              type: 'event',
              session,
              event: {
                type: 'message_start',
                message: { role: 'user', text },
              },
            },
          },
        })
      },
      { session, text },
    )

  await emit('/sessions/b.jsonl', '后台会话的进度')
  await expect(page.getByText('后台会话的进度')).toHaveCount(0)
  await emit('/sessions/a.jsonl', '当前会话的消息')
  await expect(page.getByText('当前会话的消息')).toBeVisible()
})

test('a fresh runtime is put back onto the last mode, model and thinking', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('zen.pref.mode', 'plan')
    localStorage.setItem('zen.pref.model', 'other-model')
    localStorage.setItem('zen.pref.thinking', 'low')
  })
  await boot(page, initialState())

  // The child defaults to ask mode / catalog model / high thinking; the prefs
  // are replayed as ordinary frames, so the person never notices the restart.
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.calls.some((call) => call.type === 'mode' && call.mode === 'plan')))
    .toBe(true)
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'model' && call.model_id === 'other-model'),
      ),
    )
    .toBe(true)
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'command' && call.name === 'thinking' && call.argument === 'low',
        ),
      ),
    )
    .toBe(true)

  const order = await page.evaluate(() =>
    window.__ZEN_E2E__.calls
      .filter(
        (call) =>
          call.type === 'mode' ||
          call.type === 'model' ||
          (call.type === 'command' && call.name === 'thinking'),
      )
      .map((call) => (call.type === 'command' ? 'thinking' : String(call.type))),
  )
  // A model switch may reset the thinking level, so thinking lands last.
  assert.deepEqual(order.slice(0, 3), ['mode', 'model', 'thinking'])
})
