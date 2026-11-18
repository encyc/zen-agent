/** Worktree recency ordering: newest branch tip first, unknown timestamps
 * last, branch-name tie-break so a refresh cannot reshuffle equal rows. */

import test from 'node:test'
import assert from 'node:assert/strict'
import { sortWorktrees } from '../src/ui/rightbar/worktrees.ts'
import type { WorktreeEntry } from '../src/rpc/types.ts'

function tree(branch: string, timestamp: number): WorktreeEntry {
  return {
    path: `/work/${branch}`,
    head: `${branch}000000`,
    branch,
    timestamp,
    bare: false,
    detached: false,
    current: branch === 'main',
  }
}

test('sortWorktrees puts the newest branch tip first', () => {
  const sorted = sortWorktrees([
    tree('stale', 1_700_000_000),
    tree('fresh', 1_763_200_000),
    tree('mid', 1_750_000_000),
  ])
  assert.deepEqual(
    sorted.map((entry) => entry.branch),
    ['fresh', 'mid', 'stale'],
  )
})

test('sortWorktrees keeps unknown times last and does not mutate the input', () => {
  const input = [tree('unknown', 0), tree('newest', 1_763_200_000), tree('older', 1_700_000_000)]
  const sorted = sortWorktrees(input)
  assert.deepEqual(
    sorted.map((entry) => entry.branch),
    ['newest', 'older', 'unknown'],
  )
  assert.deepEqual(
    input.map((entry) => entry.branch),
    ['unknown', 'newest', 'older'],
  )
})

test('sortWorktrees tie-breaks equal tips by branch name', () => {
  const sorted = sortWorktrees([tree('zeta', 10), tree('alpha', 10), tree('main', 10)])
  assert.deepEqual(
    sorted.map((entry) => entry.branch),
    ['alpha', 'main', 'zeta'],
  )
})
