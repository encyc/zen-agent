import test from 'node:test'
import assert from 'node:assert/strict'
import { zh, en, translate } from '../src/i18n/messages.ts'
import { localizeError } from '../src/i18n/errors.ts'

test('the two dictionaries carry the same key set', () => {
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
})

test('no dictionary value is empty', () => {
  for (const [key, value] of Object.entries(zh)) assert.ok(String(value).length > 0, `zh ${key}`)
  for (const [key, value] of Object.entries(en)) assert.ok(String(value).length > 0, `en ${key}`)
})

/** Placeholder names in a template, sorted — `{name}` → 'name'. */
function fields(text: string): string {
  return [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]!).sort().join(',')
}

test('both locales carry the same placeholders for every key', () => {
  // Parity of keys is not parity of templates: translate() leaves an unknown
  // `{name}` on screen verbatim, so a zh string that renames a placeholder
  // ships a literal brace to a user. The TUI catalog is pinned the same way
  // (tests/test_i18n.py).
  const mismatched = Object.keys(en).filter((key) => fields(en[key]!) !== fields(zh[key]!))
  assert.deepEqual(
    mismatched,
    [],
    mismatched.map((k) => `${k}: en={${fields(en[k]!)}} zh={${fields(zh[k]!)}}`).join('\n'),
  )
})

test('translate interpolates and leaves an unknown placeholder visible', () => {
  assert.equal(translate('zh', 'sidebar.projectGroup', { name: 'p' }), '项目 p')
  // An unknown name must not silently blank the label — the brace is the signal.
  assert.equal(translate('en', 'sidebar.projectGroup', {}), 'Project {name}')
})

test('localizeError prefers the code and interpolates params', () => {
  const text = localizeError({
    message: 'op read failed on w1',
    code: 'fs_error',
    params: { op: 'read', file: 'w1' },
  })
  assert.equal(text, 'read failed: w1')
})

test('localizeError falls back to the raw message without a code', () => {
  assert.equal(localizeError({ message: 'boom' }), 'boom')
})

test('an unknown code still falls back to the server message', () => {
  assert.equal(
    localizeError({ message: 'a future refusal', code: 'future_code' }, 'zh'),
    'a future refusal',
  )
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
    'session_busy',
    'session_invalid',
    'limits_failed',
    'config_failed',
    'mcp_failed',
    'mode_failed',
    'project_failed',
    'project_busy',
    'project_invalid',
    'command_failed',
    'image_read_failed',
  ]) {
    const en = localizeError({ message: 'server text', code }, 'en')
    const zh = localizeError({ message: 'server text', code }, 'zh')
    assert.notEqual(en, 'server text', `${code} has no en copy`)
    assert.notEqual(zh, 'server text', `${code} has no zh copy`)
    assert.notEqual(en, zh, `${code} is not translated`)
  }
})

test('authored busy and invalid refusals render actionable copy', () => {
  const cases: Array<[string, string, string]> = [
    ['session_busy', 'cancel the current run', '取消当前运行'],
    ['project_busy', 'switch to another project', '切换到其他项目'],
    ['session_invalid', 'incomplete or unsupported', '不完整或不受支持'],
    ['project_invalid', 'incomplete or unsupported', '不完整或不受支持'],
  ]
  for (const [code, enNeedle, zhNeedle] of cases) {
    const en = localizeError({ message: 'server english text', code }, 'en')
    const zh = localizeError({ message: '服务端英文', code }, 'zh')
    assert.ok(en.includes(enNeedle), `${code} en copy lacks ${enNeedle}: ${en}`)
    assert.ok(zh.includes(zhNeedle), `${code} zh copy lacks ${zhNeedle}: ${zh}`)
    assert.ok(!en.includes('server english text'), `${code} fell back to the server message`)
    assert.ok(!zh.includes('服务端英文'), `${code} fell back to the server message`)
  }
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
