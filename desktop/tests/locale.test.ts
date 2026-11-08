import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { zh, en } from '../src/i18n/messages.ts'
import { localizeError } from '../src/i18n/errors.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(here, '..', 'src', rel), 'utf8')

test('the two dictionaries carry the same key set', () => {
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
})

test('no dictionary value is empty', () => {
  for (const [key, value] of Object.entries(zh)) assert.ok(String(value).length > 0, `zh ${key}`)
  for (const [key, value] of Object.entries(en)) assert.ok(String(value).length > 0, `en ${key}`)
})

test('localizeError prefers the code and interpolates params', () => {
  const text = localizeError({
    message: 'worker 不存在: w1',
    code: 'taiji.worker_missing',
    params: { name: 'w1' },
  })
  assert.ok(!text.includes('不存在'), 'raw Chinese leaked through')
  assert.ok(text.includes('w1'))
})

test('localizeError falls back to the raw message without a code', () => {
  assert.equal(localizeError({ message: 'boom' }), 'boom')
})

test('the taiji panel composes no Chinese protocol strings of its own', () => {
  const panel = read('ui/rightbar/TaijiPanel.tsx')
  const cjkLines = panel.split('\n').filter((line) => {
    const trimmed = line.trim()
    if (trimmed.startsWith('*') || trimmed.startsWith('//') || trimmed.startsWith('/*')) return false
    return [...line].some((c) => c >= '\u4e00' && c <= '\u9fff')
  })
  assert.deepEqual(cjkLines, [], `CJK literals crept back:\n${cjkLines.join('\n')}`)
})
