/** Lane layout: greedy first-fit columns over a newest-first commit list, and
 * the keyed merge that lets a refresh prepend new commits without disturbing
 * the loaded tail. */

import test from 'node:test'
import assert from 'node:assert/strict'
import { assignLanes, mergeCommits } from '../src/ui/rightbar/lanes.ts'
import type { GitCommit } from '../src/rpc/types.ts'

function commit(hash: string, parents: string[] = []): GitCommit {
  return {
    hash,
    short: hash.slice(0, 7),
    parents,
    subject: `subject ${hash}`,
    author: 'a',
    timestamp: 1,
    refs: [],
  }
}

test('a linear history keeps one lane', () => {
  const rows = assignLanes([commit('c', ['b']), commit('b', ['a']), commit('a', [])])
  assert.deepEqual(rows.map((row) => row.lane), [0, 0, 0])
  assert.deepEqual(rows[0].parents, [{ hash: 'b', lane: 0 }])
  // The dot's own lane is the arrival/continuation, never a pass-through.
  assert.deepEqual(rows[1].incoming, [0])
  assert.deepEqual(rows[1].through, [])
})

test('a side branch takes its own lane and merges back', () => {
  // m is a merge of b (first parent) and s (second). Newest first.
  const rows = assignLanes([
    commit('m', ['b', 's']),
    commit('s', ['a']),
    commit('b', ['a']),
    commit('a', []),
  ])
  assert.equal(rows[0].lane, 0)
  // First parent continues lane 0; the second parent gets a fresh lane.
  assert.deepEqual(rows[0].parents, [
    { hash: 'b', lane: 0 },
    { hash: 's', lane: 1 },
  ])
  assert.equal(rows[1].lane, 1) // s
  assert.equal(rows[2].lane, 0) // b
  // Both converge on a, which keeps the earliest expected lane.
  assert.equal(rows[3].lane, 0)
  // Row s: lane 0 is not this commit's lane but stays live below, so it is a
  // straight through-line. Row b: the side-branch lane (1) still awaits a.
  assert.deepEqual(rows[1].through, [0])
  assert.deepEqual(rows[2].through, [1])
  // Row a: lane 1 was also waiting for a, so it bends into the dot instead of
  // dangling past the last row.
  assert.deepEqual(rows[3].converge, [1])
  assert.deepEqual(rows[3].through, [])
})

test('a merge whose second parent is not loaded yet still places the edge', () => {
  const rows = assignLanes([commit('m', ['b', 'missing']), commit('b', [])])
  assert.deepEqual(rows[0].parents, [
    { hash: 'b', lane: 0 },
    { hash: 'missing', lane: 1 },
  ])
})

test('mergeCommits prepends new commits and keeps the loaded tail', () => {
  const loaded = [commit('b', ['a']), commit('a', [])]
  const fresh = [commit('d', ['c']), commit('c', ['b']), commit('b', ['a'])]
  const merged = mergeCommits(loaded, fresh)
  assert.deepEqual(merged.map((c) => c.hash), ['d', 'c', 'b', 'a'])
})

test('mergeCommits is a no-op when the head is unchanged', () => {
  const loaded = [commit('b', ['a']), commit('a', [])]
  assert.deepEqual(mergeCommits(loaded, [commit('b', ['a'])]).map((c) => c.hash), ['b', 'a'])
})
