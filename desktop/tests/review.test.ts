import assert from 'node:assert/strict'
import { test } from 'node:test'
import { collectFileChanges, parseDiffHunks } from '../src/ui/rightbar/review.ts'
import type { ChatNode, ToolNode } from '../src/state/chatStore.ts'

function tool(id: string, path: string, diff: string | null): ChatNode {
  const node: ToolNode = {
    kind: 'tool',
    id,
    callId: id,
    name: 'edit_file',
    argsPreview: '',
    args: { path },
    state: 'ok',
    progress: '',
    resultText: '',
    isError: false,
    durationMs: null,
    diff,
    details: {},
  }
  return node
}

const user: ChatNode = {
  kind: 'user',
  id: 'u1',
  text: '改两个文件',
  steering: false,
  timestamp: 1,
}

test('review groups diffs per file and counts changed lines', () => {
  const changes = collectFileChanges([
    tool('t1', '/src/a.ts', ['@@ -1,2 +1,3 @@', ' keep', '-old', '+new', '+extra'].join('\n')),
    user,
    tool('t2', '/src/b.ts', ['@@ -1 +1 @@', '-gone', '+fresh'].join('\n')),
    tool('t3', '/src/a.ts', ['@@ -9 +9 @@', '+again'].join('\n')),
  ])
  assert.deepEqual(
    changes.map((change) => [change.path, change.additions, change.removals]),
    [
      ['/src/a.ts', 3, 1],
      ['/src/b.ts', 1, 1],
    ],
  )
  assert.equal(changes[0].diffs.length, 2)
})

test('review ignores tools without a diff and falls back to details.path', () => {
  const fallback = tool('t1', '', ['@@ -1 +1 @@', '+x'].join('\n'))
  fallback.args = {}
  ;(fallback as ToolNode).details = { path: '/src/c.ts' }
  const changes = collectFileChanges([tool('t2', '/src/skip.ts', null), fallback])
  assert.deepEqual(
    changes.map((change) => [change.path, change.additions]),
    [['/src/c.ts', 1]],
  )
})

test('review parses unified hunks and tracks both line numbers', () => {
  const hunks = parseDiffHunks(
    [
      '--- a/src/a.ts',
      '+++ b/src/a.ts',
      '@@ -3,3 +3,4 @@ export function run',
      ' keep',
      '-old',
      '+new',
      '+extra',
      ' tail',
    ].join('\n'),
  )
  assert.equal(hunks.length, 1)
  assert.equal(hunks[0].header, '@@ -3,3 +3,4 @@ export function run')
  assert.deepEqual(
    hunks[0].lines.map((line) => [line.kind, line.text, line.oldNo, line.newNo]),
    [
      ['ctx', 'keep', 3, 3],
      ['del', 'old', 4, null],
      ['add', 'new', null, 4],
      ['add', 'extra', null, 5],
      ['ctx', 'tail', 5, 6],
    ],
  )
})

test('review splits multiple hunks and keeps a hunkless diff readable', () => {
  const hunks = parseDiffHunks(
    ['@@ -1 +1 @@', '-a', '+b', '@@ -9,2 +9,2 @@', '-c', '+d'].join('\n'),
  )
  assert.equal(hunks.length, 2)
  assert.equal(hunks[1].header, '@@ -9,2 +9,2 @@')

  const loose = parseDiffHunks(['-gone', '+fresh'].join('\n'))
  assert.equal(loose.length, 1)
  assert.deepEqual(
    loose[0].lines.map((line) => [line.kind, line.oldNo, line.newNo]),
    [
      ['del', null, null],
      ['add', null, null],
    ],
  )
})
