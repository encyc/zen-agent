/** gitStore contract: changes polling, per-file diff caching keyed by
 * path+stage, failure retention, and the 500ms transcript-growth refresh. */

import { beforeEach, mock, test } from 'node:test'
import assert from 'node:assert/strict'
import { diffKey, useGitStore } from '../src/state/gitStore.ts'
import { useAppStore } from '../src/state/appStore.ts'
import { useChatStore } from '../src/state/chatStore.ts'
import { useWorkPanelStore } from '../src/state/stores.ts'
import type { RpcClient } from '../src/rpc/client.ts'
import type { ChatNode } from '../src/state/chatStore.ts'
import type { GitChangesResult, GitDiffResult } from '../src/rpc/types.ts'

const changesFrame: GitChangesResult = {
  type: 'result',
  id: '',
  ok: true,
  changes: {
    is_repo: true,
    branch: 'main',
    files: [
      { path: 'a.txt', state: 'staged', plus: 2, minus: 0 },
      { path: 'b.txt', state: 'unstaged', plus: 1, minus: 0 },
      { path: 'new.txt', state: 'untracked', plus: null, minus: null },
    ],
    untracked_more: 0,
  },
}

const diffFrame: GitDiffResult = {
  type: 'result',
  id: '',
  ok: true,
  diff: { text: '@@ -0,0 +1,1 @@\n+hello\n', binary: false, truncated: false, missing: false },
}

/** Swap the appStore's client factory for a scripted one; returns the call log. */
function stubClient(frames: Record<string, unknown>) {
  const calls: string[] = []
  const client = {
    async send(payload: { type: string }) {
      calls.push(payload.type)
      const frame = frames[payload.type]
      if (frame === undefined) throw new Error(`unexpected request: ${payload.type}`)
      return frame
    },
  }
  useAppStore.setState({ client: () => client as unknown as RpcClient })
  return calls
}

beforeEach(() => {
  useWorkPanelStore.setState({ open: false })
  useChatStore.getState().reset()
  useGitStore.getState().clear()
})

test('openFile caches the diff by path and stage key', async () => {
  const calls = stubClient({ git_changes: changesFrame, git_diff: diffFrame })

  await useGitStore.getState().loadChanges()
  assert.equal(useGitStore.getState().changes?.files.length, 3)

  await useGitStore.getState().openFile('a.txt', false)
  await useGitStore.getState().openFile('a.txt', false)
  assert.equal(calls.filter((kind) => kind === 'git_diff').length, 1)

  const selected = useGitStore.getState().selected
  assert.deepEqual(selected, { kind: 'file', path: 'a.txt', staged: false })
  assert.deepEqual(useGitStore.getState().diffFor(diffKey(null, 'a.txt', false)), diffFrame.diff)
  assert.equal(useGitStore.getState().diffFor(diffKey(null, 'a.txt', true)), null)
})

test('a failed refresh keeps the previous payload and flags failure', async () => {
  stubClient({ git_changes: changesFrame })
  await useGitStore.getState().loadChanges()

  stubClient({ git_changes: { type: 'result', id: '', ok: false } })
  await useGitStore.getState().loadChanges()

  assert.equal(useGitStore.getState().failed, true)
  assert.equal(useGitStore.getState().changes?.branch, 'main')
})

test('transcript growth refreshes the panel once, debounced 500ms', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const calls = stubClient({ git_changes: changesFrame })
    useWorkPanelStore.setState({ open: true })

    await useGitStore.getState().loadChanges()
    assert.equal(calls.filter((kind) => kind === 'git_changes').length, 1)

    const node: ChatNode = { kind: 'user', id: 'n1', text: 'go', timestamp: 1, steering: false }
    useChatStore.setState({ nodes: [node] })
    useChatStore.setState({ nodes: [node, { ...node, id: 'n2' }] })

    mock.timers.tick(500)
    await Promise.resolve()
    await Promise.resolve()

    assert.equal(calls.filter((kind) => kind === 'git_changes').length, 2)
  } finally {
    mock.timers.reset()
  }
})

test('growth while the panel is closed does not refresh', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const calls = stubClient({ git_changes: changesFrame })
    await useGitStore.getState().loadChanges()

    const node: ChatNode = { kind: 'user', id: 'n1', text: 'go', timestamp: 1, steering: false }
    useChatStore.setState({ nodes: [node] })
    mock.timers.tick(500)
    await Promise.resolve()

    assert.equal(calls.filter((kind) => kind === 'git_changes').length, 1)
  } finally {
    mock.timers.reset()
  }
})


test('setScope previews another worktree and keys its diff separately', async () => {
  const calls: { type: string; worktree?: string; path?: string; staged?: boolean }[] = []
  const client = {
    async send(payload: { type: string; worktree?: string; path?: string; staged?: boolean }) {
      calls.push(payload)
      if (payload.type === 'git_changes') {
        return {
          ...changesFrame,
          changes: { ...changesFrame.changes, branch: 'feature' },
        }
      }
      return payload.worktree
        ? { ...diffFrame, diff: { ...diffFrame.diff, text: 'scoped' } }
        : diffFrame
    },
  }
  useAppStore.setState({ client: () => client as unknown as RpcClient })

  await useGitStore.getState().openFile('a.txt', false)
  const beforeScope = calls.length
  useGitStore.getState().setScope({ path: '/work/p-feature', branch: 'feature' })
  await new Promise((resolve) => setTimeout(resolve, 0))

  const scoped = { path: '/work/p-feature', branch: 'feature' }
  const scopedCalls = calls.slice(beforeScope)
  assert.equal(useGitStore.getState().scope?.branch, 'feature')
  assert.equal(useGitStore.getState().selected, null)
  assert.deepEqual(
    scopedCalls.filter((call) => call.type === 'git_changes').map((call) => call.worktree),
    ['/work/p-feature'],
  )

  await useGitStore.getState().openFile('a.txt', false)
  assert.deepEqual(
    calls
      .slice(beforeScope)
      .filter((call) => call.type === 'git_diff')
      .map((call) => call.worktree),
    ['/work/p-feature'],
  )
  // The same path in two worktrees must not share a cache entry.
  assert.equal(useGitStore.getState().diffFor(diffKey(scoped, 'a.txt', false))?.text, 'scoped')
  assert.equal(useGitStore.getState().diffFor(diffKey(null, 'a.txt', false))?.text, diffFrame.diff.text)
})

// ---- graph: log paging and keyed refresh -------------------------------------

const logPage1: Record<string, unknown> = {
  type: 'result', id: '', ok: true,
  log: {
    commits: [
      { hash: 'c', short: 'c123456', parents: ['b'], subject: 'third', author: 'a', timestamp: 3, refs: ['main'] },
      { hash: 'b', short: 'b123456', parents: ['a'], subject: 'second', author: 'a', timestamp: 2, refs: [] },
    ],
  },
}
const logPage2: Record<string, unknown> = {
  type: 'result', id: '', ok: true,
  log: {
    commits: [
      { hash: 'a', short: 'a123456', parents: [], subject: 'first', author: 'a', timestamp: 1, refs: [] },
    ],
  },
}
const logHead: Record<string, unknown> = {
  type: 'result', id: '', ok: true,
  log: {
    commits: [
      { hash: 'd', short: 'd123456', parents: ['c'], subject: 'new head', author: 'a', timestamp: 4, refs: ['main'] },
      { hash: 'c', short: 'c123456', parents: ['b'], subject: 'third', author: 'a', timestamp: 3, refs: [] },
      { hash: 'b', short: 'b123456', parents: ['a'], subject: 'second', author: 'a', timestamp: 2, refs: [] },
    ],
  },
}

test('loadLog pages and loadMoreLog appends until a short page', async () => {
  const calls = stubClient({ git_log: logPage1 })
  await useGitStore.getState().loadLog()
  assert.equal(useGitStore.getState().log?.length, 2)
  assert.equal(useGitStore.getState().hasMoreLog, true)

  stubClient({ git_log: logPage2 })
  await useGitStore.getState().loadMoreLog()
  assert.deepEqual(useGitStore.getState().log?.map((c) => c.hash), ['c', 'b', 'a'])
  // A page shorter than the limit ends the history.
  assert.equal(useGitStore.getState().hasMoreLog, false)
  assert.ok(calls.every((kind) => kind === 'git_log'))
})


test('concurrent tail loads fetch a single page', async () => {
  let release: (value: unknown) => void = () => {}
  const skipped: number[] = []
  const client = {
    async send(payload: { type: string; skip?: number }) {
      skipped.push(Number(payload.skip ?? 0))
      if (payload.skip === 2) {
        return new Promise((resolve) => {
          release = resolve
        })
      }
      return logPage1
    },
  }
  useAppStore.setState({ client: () => client as unknown as RpcClient })
  await useGitStore.getState().loadLog()

  const first = useGitStore.getState().loadMoreLog()
  const second = useGitStore.getState().loadMoreLog()
  release(logPage2)
  await Promise.all([first, second])

  // The second call is suppressed; the cursor moved exactly once.
  assert.deepEqual(skipped, [0, 2])
  assert.deepEqual(useGitStore.getState().log?.map((commit) => commit.hash), ['c', 'b', 'a'])
})

test('a log refresh prepends new commits and keeps the loaded tail', async () => {
  stubClient({ git_log: logPage1 })
  await useGitStore.getState().loadLog()
  stubClient({ git_log: logPage2 })
  await useGitStore.getState().loadMoreLog()

  stubClient({ git_log: logHead })
  await useGitStore.getState().loadLog()
  assert.deepEqual(useGitStore.getState().log?.map((c) => c.hash), ['d', 'c', 'b', 'a'])
})

test('openCommit caches the show payload by hash', async () => {
  const calls = stubClient({
    git_log: logPage1,
    git_show: {
      type: 'result', id: '', ok: true,
      show: { found: true, short: 'c123456', subject: 'third', files: [] },
    },
  })
  await useGitStore.getState().loadLog()
  await useGitStore.getState().openCommit('c')
  await useGitStore.getState().openCommit('c')
  assert.equal(calls.filter((kind) => kind === 'git_show').length, 1)
  const selected = useGitStore.getState().selected
  assert.deepEqual(selected, { kind: 'commit', hash: 'c' })
  assert.equal(useGitStore.getState().showFor('c')?.subject, 'third')
})
