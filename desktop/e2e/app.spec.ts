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
  dialogSelection: string | string[] | null
  title: string
  failExport?: boolean
}

declare global {
  interface Window {
    __ZEN_E2E__: BridgeState
  }
}

function installBridge(state: BridgeState) {
  const callbacks = new Map<number, (payload: unknown) => void>()
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
        current: true,
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
        }
      case 'transcript':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          transcript: [],
          notices: [],
          compactions: [],
        }
      case 'models':
        return { type: 'result', id: payload.id, ok: true, models: [] }
      case 'commands':
        return { type: 'result', id: payload.id, ok: true, commands: [] }
      case 'command':
        if (payload.name === 'rename') state.title = String(payload.argument ?? '')
        return { type: 'result', id: payload.id, ok: true }
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
      if (cmd === 'plugin:event|listen') return nextEvent++
      if (cmd === 'plugin:event|unlisten') return null
      if (cmd === 'rpc_start') return { pid: 4242 }
      if (cmd === 'rpc_stop') return null
      if (cmd === 'plugin:dialog|open') return state.dialogSelection
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
}

function initialState(overrides: Partial<BridgeState> = {}): BridgeState {
  return {
    running: true,
    pending: [],
    calls: [],
    dialogSelection: null,
    title: '修复登录超时',
    ...overrides,
  }
}

async function boot(page: Page, state: BridgeState) {
  await page.addInitScript(installBridge, state)
  await page.goto('/')
  await page.getByRole('button', { name: '用当前目录启动' }).click()
  await expect(page.locator('[data-topbar-title]')).toHaveText(state.title)
}

async function setPending(page: Page, items: PendingItemMock[]) {
  await page.evaluate((pending) => {
    window.__ZEN_E2E__.pending = pending
  }, items)
}

test('the topbar shows the project and renames the current session inline', async ({ page }) => {
  await boot(page, initialState())
  await expect(page.locator('[data-topbar-project]')).toHaveText('p')
  await page.getByRole('button', { name: '重命名会话标题' }).click()
  const input = page.getByRole('textbox', { name: '会话标题' })
  await input.fill('新的标题')
  await input.press('Enter')
  await expect(page.locator('[data-topbar-title]')).toHaveText('新的标题')
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

test('the topbar menu runs undo and compact through the session op', async ({ page }) => {
  await boot(page, initialState({ running: false }))
  await page.locator('[data-topbar-menu]').click()
  await page.getByRole('menuitem', { name: '撤销上一轮' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'undo'),
      ),
    )
    .toBe(true)
  await page.locator('[data-topbar-menu]').click()
  await page.getByRole('menuitem', { name: '压缩上下文' }).click()
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.__ZEN_E2E__.calls.some((call) => call.type === 'session' && call.op === 'compact'),
      ),
    )
    .toBe(true)
})

test('the all scope groups projects and refuses cross-project opens', async ({ page }) => {
  await boot(page, initialState())
  await page.getByRole('button', { name: '全部' }).click()
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
  // A scope switch drops the arm: reopen and the label is plain delete again.
  await page.getByRole('button', { name: '全部' }).click()
  await page.getByRole('button', { name: '当前项目' }).click()
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
