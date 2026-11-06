/** The real React shell in Chromium, driven through a scripted Tauri bridge.
 * No `zen` binary and no Rust side are involved: `invoke`/`listen` are faked
 * with the same command and frame shapes the Tauri bridge uses. */

import assert from 'node:assert/strict'
import { expect, test, type Page } from '@playwright/test'

type Lane = 'queue' | 'steer'

interface PendingItemMock {
  id: number
  lane: Lane
  text: string
  images: number
  created_at: number
}

interface BridgeState {
  running: boolean
  pending: PendingItemMock[]
  calls: Record<string, unknown>[]
  starts: { cwd?: string | null }[]
  dialogSelection: string | string[] | null
  title: string
  failExport?: boolean
  noCurrent?: boolean
  projectSessions?: number
  transcript?: Record<string, unknown>[]
  todos?: Record<string, unknown>[]
  tools?: { name: string; description: string; approval: string }[]
}

declare global {
  interface Window {
    __ZEN_E2E__: BridgeState
    __ZEN_E2E_EMIT__(event: string, payload: unknown): void
  }
}

function installBridge(state: BridgeState) {
  const callbacks = new Map<number, (payload: unknown) => void>()
  const listeners = new Map<string, ((payload: unknown) => void)[]>()
  let nextCallback = 1
  let nextEvent = 1

  const sessions = (id: unknown, scope: unknown) => ({
    type: 'result',
    id,
    ok: true,
    scope: scope === 'all' ? 'all' : 'project',
    sessions: [
      {
        path: '/sessions/a.jsonl',
        name: 'a',
        title: state.title,
        project: 'p',
        messages: 4,
        current: state.noCurrent !== true,
        pinned: false,
        created_at: 1,
        updated_at: 2,
        model: 'deepseek-flash',
        derived_from: '',
      },
      {
        path: '/sessions/b.jsonl',
        name: 'b',
        title: '另一个会话',
        project: 'p',
        messages: 2,
        current: false,
        pinned: false,
        created_at: 1,
        updated_at: 1,
        model: 'deepseek-flash',
        derived_from: '',
      },
      ...Array.from({ length: Math.max(0, (state.projectSessions ?? 2) - 2) }, (_, index) => {
        const ordinal = index + 3
        return {
          path: `/sessions/p${ordinal}.jsonl`,
          name: `p${ordinal}`,
          title: `额外会话 ${ordinal}`,
          project: 'p',
          messages: 1,
          current: false,
          pinned: false,
          created_at: 1,
          updated_at: 1,
          model: 'deepseek-flash',
          derived_from: '',
        }
      }),
      ...(scope === 'all'
        ? [
            {
              path: '/sessions/c.jsonl',
              name: 'c',
              title: '另一个项目',
              project: 'q',
              messages: 1,
              current: false,
              pinned: false,
              created_at: 1,
              updated_at: 1,
              model: 'deepseek-flash',
              derived_from: '',
            },
          ]
        : []),
    ],
  })

  const respond = (payload: Record<string, unknown>) => {
    switch (payload.type) {
      case 'status':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          running: state.running,
          messages: 2,
          model: 'deepseek-flash',
          usage: { input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 },
          termination: null,
          mode: 'zen',
          pending: state.pending,
          todos: state.todos ?? [],
        }
      case 'transcript':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          transcript: state.transcript ?? [],
          notices: [],
          compactions: [],
        }
      case 'models':
        return { type: 'result', id: payload.id, ok: true, models: [] }
      case 'tools':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          tools: state.tools ?? [
            { name: 'read_file', description: 'Read a file', approval: 'read' },
          ],
        }
      case 'commands':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          commands: [
            { name: 'compact', description: 'Compact context' },
            { name: 'rename', description: 'Rename session' },
            { name: 'quit', description: 'Quit' },
          ],
        }
      case 'command':
        if (payload.name === 'rename') state.title = String(payload.argument ?? '')
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          ...(payload.name === 'rename' ? { message: '已重命名' } : {}),
        }
      case 'session':
        switch (payload.op) {
          case 'list':
            return sessions(payload.id, payload.scope)
          case 'export': {
            if (state.failExport) return { type: 'error', message: '导出失败' }
            const suffix = payload.format === 'html' ? '.html' : '.md'
            return {
              type: 'result',
              id: payload.id,
              ok: true,
              path: String(payload.path).replace(/\.jsonl$/, suffix),
            }
          }
          case 'promote': {
            const target = state.pending.find((item) => item.id === payload.pending_id)
            if (target === undefined) {
              return { type: 'result', id: payload.id, ok: true, outcome: 'missing' }
            }
            const nextId = Math.max(0, ...state.pending.map((item) => item.id)) + 1
            state.pending = state.pending.map((item) =>
              item.id === payload.pending_id
                ? { ...item, id: nextId, lane: 'steer' as Lane }
                : item,
            )
            return { type: 'result', id: payload.id, ok: true, outcome: 'ok' }
          }
          case 'dequeue': {
            let index = -1
            for (let position = state.pending.length - 1; position >= 0; position -= 1) {
              if (state.pending[position].lane === 'queue') {
                index = position
                break
              }
            }
            const taken = index >= 0 ? state.pending.splice(index, 1)[0] : undefined
            return {
              type: 'result',
              id: payload.id,
              ok: true,
              dequeued: taken !== undefined,
              text: taken?.text ?? null,
            }
          }
          case 'clear_pending': {
            const cleared = state.pending.length
            state.pending = []
            return { type: 'result', id: payload.id, ok: true, cleared }
          }
          default:
            return { type: 'result', id: payload.id, ok: true }
        }
      default:
        return { type: 'result', id: payload.id, ok: true }
    }
  }

  const internals = {
    transformCallback(callback: (payload: unknown) => void) {
      const id = nextCallback
      nextCallback += 1
      callbacks.set(id, callback)
      return id
    },
    unregisterCallback(id: number) {
      callbacks.delete(id)
    },
    async invoke(
      cmd: string,
      args: Record<string, unknown> & { payload?: Record<string, unknown> },
    ) {
      if (cmd === 'plugin:event|listen') {
        const handler = callbacks.get(args.handler as number)
        if (handler !== undefined) {
          const event = String(args.event)
          const list = listeners.get(event) ?? []
          list.push(handler)
          listeners.set(event, list)
        }
        return nextEvent++
      }
      if (cmd === 'plugin:event|unlisten') return null
      if (cmd === 'rpc_start') {
        const options = (args.options ?? {}) as { cwd?: string | null }
        state.starts.push({ cwd: options.cwd ?? null })
        return { pid: 4242 }
      }
      if (cmd === 'rpc_stop') return null
      if (cmd === 'plugin:dialog|open') return state.dialogSelection
      if (cmd === 'plugin:path|resolve_directory') return '/Users/e2e'
      if (cmd === 'rpc_send') {
        const payload = args.payload ?? {}
        state.calls.push(payload)
        return respond(payload)
      }
      throw new Error(`unexpected invoke: ${cmd}`)
    },
  }

  ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = internals
  ;(
    window as unknown as { __TAURI_EVENT_PLUGIN_INTERNALS__: unknown }
  ).__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} }
  ;(window as unknown as { __ZEN_E2E__: BridgeState }).__ZEN_E2E__ = state
  ;(window as unknown as { __ZEN_E2E_EMIT__: (event: string, payload: unknown) => void }).__ZEN_E2E_EMIT__ = (
    event,
    payload,
  ) => {
    for (const handler of listeners.get(event) ?? []) handler(payload)
  }
}

function initialState(overrides: Partial<BridgeState> = {}): BridgeState {
  return {
    running: true,
    pending: [],
    calls: [],
    starts: [],
    dialogSelection: null,
    title: '修复登录超时',
    ...overrides,
  }
}

async function boot(page: Page, state: BridgeState) {
  await page.addInitScript(installBridge, state)
  await page.goto('/')
  await expect(page.locator('[data-topbar-title]')).toHaveText(state.title)
}

async function setPending(page: Page, items: PendingItemMock[]) {
  await page.evaluate((pending) => {
    window.__ZEN_E2E__.pending = pending
  }, items)
}

const paletteKey = process.platform === 'darwin' ? 'Meta+k' : 'Control+k'

test('a returning user lands on the home hero and auto-connects', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('zen.last.cwd', '/work'))
  await page.addInitScript(installBridge, initialState())
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: /ZenAgent/ })).toBeVisible()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
  await expect(page.locator('[data-home-hero]')).toBeVisible()
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
      todos: Array.from({ length: 14 }, (_, index) => ({
        text: `待办项 ${index}`,
        status: 'pending',
      })),
    }),
  )
  await page.getByRole('button', { name: /待办/ }).click()
  await expect(page.getByText('待办项 13')).toBeVisible()
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

test('the work panel opens on review and switches to context', async ({ page }) => {
  await boot(
    page,
    initialState({
      transcript: [
        {
          id: 't1',
          call_id: 'c1',
          name: 'edit_file',
          args_preview: '{"path":"/src/a.ts"}',
          args: { path: '/src/a.ts' },
          status: 'ok',
          diff: ['@@ -1,2 +1,2 @@', ' keep', '-old', '+new'].join('\n'),
        },
      ],
    }),
  )
  await page.getByRole('button', { name: '打开工作面板' }).click()
  await expect(page.getByRole('tab', { name: '审核' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-work-panel]').getByText('a.ts', { exact: true })).toBeVisible()
  const file = page.locator('[data-work-panel]').getByRole('button', { name: /^a\.ts/ })
  await file.click()
  await expect(file).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('[data-work-panel] pre')).toContainText('+new')
  await page.getByRole('tab', { name: '上下文' }).click()
  await expect(page.getByTestId('context-used')).toBeVisible()
  await page.getByRole('button', { name: '关闭工作面板' }).click()
  await expect(page.locator('[data-work-panel]')).toHaveCount(0)
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

test('a settled turn folds its tool steps into one process row', async ({ page }) => {
  await boot(
    page,
    initialState({
      running: false,
      transcript: [
        { kind: 'user', id: 'u1', text: 'go', timestamp: 1 },
        { kind: 'assistant', id: 'a1', text: 'done', timestamp: 2, duration_ms: 900 },
        {
          id: 't1',
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
  await expect(fold).toHaveAttribute('aria-expanded', 'false')
  await fold.click()
  await expect(page.getByText('read_file')).toBeVisible()
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

test('the sidebar lists every project and refuses cross-project opens', async ({ page }) => {
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
  await expect(cross).toHaveAttribute('aria-disabled', 'true')
  // aria-disabled rows fail Playwright's actionability check; force the pointer.
  await cross.click({ force: true })
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'resume'),
    ),
    false,
  )
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

test('an unknown current project leaves every listed session disabled', async ({ page }) => {
  await page.addInitScript(installBridge, initialState({ noCurrent: true }))
  await page.goto('/')
  await expect(page.locator('[data-session-path="/sessions/a.jsonl"]')).toBeVisible()
  await expect(page.locator('[data-session-path="/sessions/a.jsonl"]')).toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('[data-session-path="/sessions/b.jsonl"]')).toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('[data-session-path="/sessions/c.jsonl"]')).toHaveAttribute('aria-disabled', 'true')
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

test('settings and tools replace the shell and return to chat', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: '设置' }).click()
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()
  await expect(page.getByRole('button', { name: '深色' })).toBeVisible()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()

  await page.getByRole('button', { name: '工具' }).click()
  await expect(page.getByText('read_file')).toBeVisible()
  await expect(page.getByText('Read a file')).toBeVisible()
  await page.getByRole('button', { name: '返回' }).click()
  await expect(page.locator('[data-shell-composer]')).toBeVisible()
})

test('a cross-project palette result is disabled and Enter does nothing', async ({ page }) => {
  await boot(page, initialState())
  await page.keyboard.press(paletteKey)
  const input = page.getByRole('dialog').getByRole('combobox')
  await input.fill('另一个项目')
  const option = page.getByRole('option', { name: /另一个项目/ })
  await expect(option).toHaveAttribute('aria-disabled', 'true')
  // aria-disabled options are unclickable under Playwright's actionability check.
  await option.click({ force: true })
  await page.keyboard.press('Enter')
  assert.equal(
    await page.evaluate(() =>
      window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'resume'),
    ),
    false,
  )
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
})
