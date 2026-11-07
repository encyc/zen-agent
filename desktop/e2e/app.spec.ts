/** The real React shell in Chromium, driven through a scripted Tauri bridge.
 * No `zen` binary and no Rust side are involved: `invoke`/`listen` are faked
 * with the same command and frame shapes the Tauri bridge uses. */

import assert from 'node:assert/strict'
import { expect, test, type Page } from '@playwright/test'

import { boot, installBridge, initialState, type BridgeState } from './harness'
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
  await page.addInitScript(installBridge, initialState())
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: /ZenAgent/ })).toBeVisible()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.locator('[data-home-hero]')).toBeVisible()
})

test('the home hero shows the padded app icon', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work'))
  await page.addInitScript(installBridge, initialState())
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

test('the sidebar brand keeps the ZEN wordmark in the DOM', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  // The brand row is hidden on macOS; on other platforms the same mark shows.
  await expect(page.locator('[data-shell-sidebar] [data-zen-wordmark]')).toHaveCount(1)
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
  await page.addInitScript(installBridge, initialState())
  await page.goto('/')
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => window.__ZEN_E2E__.starts[0]?.cwd))
    .toBe('/Users/e2e')
})

test('a new project joins the rail and can be removed', async ({ page }) => {
  await boot(page, initialState({ running: false, dialogSelection: '/work/newproj' }))
  await page.getByRole('button', { name: '新建项目' }).click()
  await expect(page.locator('[data-sidebar-project-group="newproj"]')).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.__ZEN_E2E__.starts.length)).toBe(2)
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

const taijiSnapshot = {
  graph: {
    root: '/work/p',
    is_repo: true,
    commits: [
      { sha: 'c3', short: 'c3abc12', parents: ['c1'], subject: 'taiji: merge task/fix', author: 'justin', ts: 3, lane: 0 },
      { sha: 'c2', short: 'c2abc12', parents: ['c1'], subject: 'fix: login timeout', author: 'worker', ts: 2, lane: 1 },
      { sha: 'c1', short: 'c1abc12', parents: [], subject: 'init', author: 'justin', ts: 1, lane: 0 },
    ],
    branches: [
      { name: 'main', sha: 'c3', is_head: true },
      { name: 'task/fix', sha: 'c2', is_head: false },
    ],
    max_lane: 1,
  },
  sessions: {
    repo_root: '/work/p',
    projects: [
      {
        key: 'p-main',
        root: '/work/p',
        branch: 'main',
        worktree: false,
        sessions: [
          {
            name: 's1',
            title: 'Router talk',
            updated_at: Math.floor(Date.now() / 1000),
            messages: 4,
          },
        ],
      },
      {
        key: 'p-task',
        root: '/work/p-task',
        branch: 'task/fix',
        worktree: true,
        sessions: [
          {
            name: 's2',
            title: 'fix login',
            updated_at: Math.floor(Date.now() / 1000) - 720,
            messages: 2,
            worker: 'login-timeout',
            status: 'running',
            branch: 'task/fix',
          },
        ],
      },
    ],
  },
}

test('the taiji tab lists commits, branches and sessions', async ({ page }) => {
  await boot(page, initialState({ taiji: taijiSnapshot, statusSession: '/home/u/projects/p-main/sessions/s1.jsonl' }))
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '太极' }).click()
  await expect(panel.getByRole('tab', { name: '太极' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('taiji: merge task/fix')
  await expect(panel).toContainText('fix: login timeout')
  await expect(panel).toContainText('main')
  await expect(panel).toContainText('task/fix')
  await expect(panel).toContainText('你在这里')
  await expect(panel).toContainText('运行中')
  await expect(panel).toContainText('task/fix')
  await page.getByRole('button', { name: '关闭工作面板' }).click()
  await expect(page.locator('[data-work-panel]')).toHaveCount(0)
})

test('the taiji tab shows an empty state outside a repository', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: '打开工作面板' }).click()
  const panel = page.locator('[data-work-panel]')
  await page.getByRole('button', { name: '打开工作面板标签' }).click()
  await panel.getByRole('menuitem', { name: '太极' }).click()
  await expect(panel.getByRole('tab', { name: '太极' })).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('还没有 git 仓库')
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

test('opening another project’s session restarts the runtime there, then resumes', async ({ page }) => {
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
  await expect
    .poll(() =>
      page.evaluate(() => {
        const starts = window.__ZEN_E2E__.starts
        return starts[starts.length - 1]?.cwd
      }),
    )
    .toBe('/work/q')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'resume' && call.path === '/sessions/c.jsonl',
        ),
      ),
    )
    .toBe(true)

  // The stopped process reports its exit after the replacement is already
  // ready; that stale event must not be mistaken for the live process dying.
  const pids = await page.evaluate(() => window.__ZEN_E2E__.starts.map((start) => start.pid))
  const stale = pids[0]
  const live = pids[pids.length - 1]
  assert.notEqual(stale, live)
  const emitExit = (pid: number | undefined) =>
    page.evaluate(
      (value) =>
        window.__ZEN_E2E_EMIT__('rpc:exit', {
          event: 'rpc:exit',
          id: 1,
          payload: { code: 0, pid: value },
        }),
      pid,
    )
  await emitExit(stale)
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
        const starts = window.__ZEN_E2E__.starts
        return starts[starts.length - 1]?.cwd
      }),
    )
    .toBe('/work/q')
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
  await expect(page.getByRole('alert')).toHaveText('导出失败')
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
  await page.addInitScript(installBridge, initialState({ noCurrent: true }))
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
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'resume' && call.path === '/sessions/b.jsonl',
        ),
      ),
    )
    .toBe(true)
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

test('settings and tools replace the shell and return to chat', async ({ page }) => {
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

  await page.getByRole('button', { name: '工具' }).click()
  await expect(page.getByText('read_file')).toBeVisible()
  await expect(page.getByText('Read a file')).toBeVisible()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
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

  // Opening another folder restarts the runtime there and returns to chat.
  await other.getByRole('button', { name: '打开' }).click()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  const starts = await page.evaluate(() => window.__ZEN_E2E__.starts)
  assert.equal(starts.at(-1)?.cwd, '/work/q')

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
        const starts = window.__ZEN_E2E__.starts
        return starts[starts.length - 1]?.cwd
      }),
    )
    .toBe('/work/q')
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some(
          (call) => call.type === 'session' && call.op === 'resume' && call.path === '/sessions/c.jsonl',
        ),
      ),
    )
    .toBe(true)
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
