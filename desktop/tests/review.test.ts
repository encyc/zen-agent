import assert from 'node:assert/strict'
import { test } from 'node:test'
import { collectFileChanges } from '../src/ui/rightbar/review.ts'
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
