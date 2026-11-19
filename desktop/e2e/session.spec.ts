/** Session and project switching: the progress strip, the cached
 * transcript shown before revalidation, warm and palette-driven
 * resumes, sibling-frame isolation and restoring the last
 * mode/model/thinking onto a fresh runtime. */

import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { boot, initialState, paletteKey } from './harness.ts'

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
