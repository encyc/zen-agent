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
    message: '合并未执行: dirty',
    code: 'taiji.merge_blocked',
    params: { reason: 'dirty' },
  }

  assert.deepEqual(frameErrorDetail(frame), {
    message: '合并未执行: dirty',
    code: 'taiji.merge_blocked',
    params: { reason: 'dirty' },
  })
  assert.equal(frameError(frame), '合并未执行: dirty')
})

test('a refused result carries a reason and a code too', () => {
  assert.deepEqual(frameErrorDetail({ type: 'result', ok: false, reason: 'no', code: 'taiji.inactive' }), {
    message: 'no',
    code: 'taiji.inactive',
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
        message: 'worker 不存在: w1',
        code: 'taiji.worker_missing',
        params: { name: 'w1' },
      },
      'en',
    ),
    'No such worker: w1',
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
  assert.equal(localizeError({ code: 'taiji.inactive' }, 'zh'), 'taiji 未在此宿主启用')
})

test('the action-error entry and the taiji panel render through the mapper', () => {
  assert.match(read('state/appStore.ts'), /frameErrorNotice/)
  assert.match(read('ui/rightbar/TaijiPanel.tsx'), /localizeError/)
})
