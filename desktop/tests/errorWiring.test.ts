/** Error frames cross the wire with a `code` + `params` (server-locale
 * `message` stays as the fallback). These tests pin the refusal reader and
 * the notice entry that localizes it, plus the two call sites that used to
 * print the raw server message. */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  frameError,
  frameErrorDetail,
  frameErrorNotice,
} from '../src/rpc/frames.ts'
import { localizeError } from '../src/i18n/errors.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(here, '..', 'src', rel), 'utf8')

test('an error frame keeps its code and params for localization', () => {
  const frame = {
    type: 'error',
    message: 'read failed: a.txt',
    code: 'fs_error',
    params: { op: 'read', file: 'a.txt' },
  }

  assert.deepEqual(frameErrorDetail(frame), {
    message: 'read failed: a.txt',
    code: 'fs_error',
    params: { op: 'read', file: 'a.txt' },
  })
  assert.equal(frameError(frame), 'read failed: a.txt')
})

test('a refused result carries a reason and a code too', () => {
  assert.deepEqual(frameErrorDetail({ type: 'result', ok: false, reason: 'no', code: 'project_invalid' }), {
    message: 'no',
    code: 'project_invalid',
  })
})

test('a refusal without text keeps the fallback', () => {
  assert.deepEqual(frameErrorDetail({ type: 'error' }), { message: 'request failed' })
  assert.deepEqual(frameErrorDetail({ type: 'error' }, 'custom'), { message: 'custom' })
  assert.equal(frameError({ type: 'result', ok: false }, 'custom'), 'custom')
})

test('non-refusal frames have no detail', () => {
  assert.equal(frameErrorDetail({ type: 'result', ok: true }), null)
  assert.equal(frameErrorDetail({ type: 'event' }), null)
})

test('frameErrorNotice localizes a known code and falls back to the message', () => {
  assert.equal(
    frameErrorNotice(
      {
        type: 'error',
        message: 'op read failed on w1',
        code: 'fs_error',
        params: { op: 'read', file: 'w1' },
      },
      'en',
    ),
    'read failed: w1',
  )
  assert.equal(
    frameErrorNotice({ type: 'error', message: 'boom', code: 'brand.new' }, 'zh'),
    'boom',
  )
  assert.equal(frameErrorNotice({ type: 'error' }, 'zh'), 'request failed')
  assert.equal(frameErrorNotice({ type: 'result', ok: true }, 'zh'), null)
})

test('localizeError alone still falls back to the raw message', () => {
  assert.equal(localizeError({ message: 'raw', code: 'unheard.of' }, 'en'), 'raw')
  assert.equal(localizeError({ code: 'session_failed' }, 'zh'), '会话操作失败，详情见服务端日志')
})

test('the action-error entry renders through the mapper', () => {
  assert.match(read('state/appStore.ts'), /frameErrorNotice/)
})

test('a failed prompt and a failed file read reach the localized notice', () => {
  // A prompt error frame used to be dropped wholesale (`if (type === 'error')
  // return`), so `prompt_failed` never rendered.
  const appStore = read('state/appStore.ts')
  const handleResult = appStore.match(
    /function handleResultFrame\([\s\S]*?\n\}/,
  )?.[0]
  assert.ok(handleResult, 'appStore lost handleResultFrame')
  assert.doesNotMatch(handleResult, /frame\.type === 'error'\) return/)
  assert.match(handleResult, /showFrameError\(frame\)/)

  // The files panel used to discard the sanitized code and print its own
  // local line, so `fs_error` could never reach the banner.
  const files = read('ui/rightbar/FilesPanel.tsx')
  assert.match(files, /frameErrorNotice/)
  assert.match(files, /useNoticeStore/)
})

test('a failed command shows one notice, not two', () => {
  // `runCommand` prints the refusal on its command card. Routing the same
  // frame through `handleResultFrame` (whose error branch raises the banner)
  // shows that one failure twice, so the failure branch must own the reply.
  const appStore = read('state/appStore.ts')
  const runCommand = appStore.match(/async runCommand\([\s\S]*?\n  \},/)?.[0]
  assert.ok(runCommand, 'appStore lost runCommand')
  const refused = runCommand.match(
    /if \(frame\.type !== 'result' \|\| !frame\.ok\) \{[\s\S]*?\n    \}/,
  )?.[0]
  assert.ok(refused, 'runCommand lost its refusal branch')
  assert.match(refused, /pushCommand/)
  assert.match(refused, /refreshStatus/)
  assert.match(refused, /return/)
  // No second notice: neither the banner helper nor the shared result
  // handler (whose error branch calls it) runs for a refused command.
  assert.doesNotMatch(refused, /handleResultFrame|showFrameError/)
})
