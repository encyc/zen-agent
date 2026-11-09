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

test('an fs refusal names the sanitized op and file', () => {
  assert.equal(
    localizeError({ code: 'fs_error', params: { op: 'read', file: 'notes.txt' } }, 'en'),
    'read failed: notes.txt',
  )
  assert.equal(
    localizeError({ code: 'fs_error', params: { op: 'list', file: 'src' } }, 'zh'),
    'list 失败：src',
  )
})

test('an fs refusal without a file keeps a readable fallback', () => {
  assert.equal(
    localizeError({ code: 'fs_error', params: { op: 'index' } }, 'en'),
    'index failed',
  )
  assert.equal(localizeError({ code: 'fs_error' }, 'zh'), '文件操作失败')
})

test('every sanitized RPC code renders localized copy in both locales', () => {
  for (const code of [
    'session_failed',
    'session_import_missing',
    'limits_failed',
    'config_failed',
    'mcp_failed',
    'mode_failed',
    'project_failed',
    'command_failed',
    'image_read_failed',
    'taiji.message_failed',
  ]) {
    const en = localizeError({ message: 'server text', code }, 'en')
    const zh = localizeError({ message: 'server text', code }, 'zh')
    assert.notEqual(en, 'server text', `${code} has no en copy`)
    assert.notEqual(zh, 'server text', `${code} has no zh copy`)
    assert.notEqual(en, zh, `${code} is not translated`)
  }
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

test('the sanitized server error codes render localized copy', () => {
  // The server now sends codes + a short English fallback (I3); the dictionary
  // is what the person actually reads.
  assert.equal(
    localizeError({ message: 'prompt failed', code: 'prompt_failed' }, 'zh'),
    '本轮运行失败，详情见服务端日志',
  )
  assert.equal(
    localizeError({ message: 'read failed', code: 'fs_error', params: { op: 'read', file: 'a.txt' } }, 'en'),
    'read failed: a.txt',
  )
  assert.equal(
    localizeError({ message: 'read failed', code: 'fs_error' }, 'zh'),
    '文件操作失败',
  )
})
