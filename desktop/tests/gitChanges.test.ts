/** gitChanges pure functions: three-group partition of the changes list and
 * session-touched path matching (absolute transcript paths normalized to the
 * git-relative form). */

import test from 'node:test'
import assert from 'node:assert/strict'
import { groupChanges, isSessionFile, sessionPaths } from '../src/ui/rightbar/gitChanges.ts'
import type { GitFileChange } from '../src/rpc/types.ts'
import type { ChatNode } from '../src/state/chatStore.ts'

function change(path: string, state: GitFileChange['state']): GitFileChange {
  return { path, state, plus: 1, minus: 0 }
}

function toolNode(path: string): ChatNode {
  return {
    kind: 'tool',
    id: path,
    callId: 'c1',
    name: 'write',
    argsPreview: '',
    args: { path },
    state: 'ok',
    progress: '',
    resultText: '',
    isError: false,
    durationMs: 1,
    diff: '@@ -1 +1 @@\n-a\n+b\n',
    details: {},
  }
}

test('groupChanges partitions by state and preserves order', () => {
  const grouped = groupChanges([
    change('b.ts', 'unstaged'),
    change('a.ts', 'staged'),
    change('new.ts', 'untracked'),
    change('c.ts', 'unstaged'),
  ])
  assert.deepEqual(grouped.staged.map((f) => f.path), ['a.ts'])
  assert.deepEqual(grouped.unstaged.map((f) => f.path), ['b.ts', 'c.ts'])
  assert.deepEqual(grouped.untracked.map((f) => f.path), ['new.ts'])
})

test('sessionPaths folds absolute transcript paths onto git-relative ones', () => {
  const session = sessionPaths(
    [toolNode('/work/p/src/a.ts'), toolNode('/elsewhere/other.ts'), toolNode('/work/p/b.ts')],
    '/work/p',
  )
  assert.deepEqual([...session].sort(), ['b.ts', 'src/a.ts'])
  assert.equal(isSessionFile('src/a.ts', session), true)
  assert.equal(isSessionFile('zz.ts', session), false)
})

test('sessionPaths matches relative transcript paths as-is', () => {
  const session = sessionPaths([toolNode('src/a.ts')], '/work/p')
  assert.equal(isSessionFile('src/a.ts', session), true)
})
