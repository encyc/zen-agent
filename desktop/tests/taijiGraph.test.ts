import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  attachSessions,
  graphEdges,
  laneColor,
  pickChips,
  relativeTime,
  statusMeta,
} from '../src/ui/rightbar/taijiGraph.ts'
import type { TaijiCommit, TaijiGraph, TaijiSessionMap } from '../src/rpc/types.ts'

const commit = (sha: string, parents: string[], lane: number, subject = sha): TaijiCommit => ({
  sha,
  short: sha.slice(0, 6),
  parents,
  subject,
  author: 'a',
  ts: 1,
  lane,
})

test('laneColor is stable per lane and cycles through the palette', () => {
  assert.equal(laneColor(0), laneColor(0))
  assert.notEqual(laneColor(0), laneColor(1))
  assert.notEqual(laneColor(1), laneColor(2))
  assert.notEqual(laneColor(2), laneColor(3))
  assert.equal(laneColor(0), laneColor(4))
  assert.equal(laneColor(7), laneColor(3))
})

test('graphEdges connect parent lanes to commit lanes and mark merges', () => {
  // newest first: merge(m) of a(main) and t(topic), both from b
  const commits = [
    commit('m', ['a', 't'], 0, 'merge'),
    commit('t', ['b'], 1, 'topic'),
    commit('a', ['b'], 0, 'main work'),
    commit('b', [], 0, 'init'),
  ]

  const edges = graphEdges(commits)

  assert.deepEqual(edges, [
    { fromLane: 0, toLane: 0, merge: true },
    { fromLane: 1, toLane: 0, merge: true },
    { fromLane: 0, toLane: 1, merge: false },
    { fromLane: 0, toLane: 0, merge: false },
  ])
})

test('attachSessions binds sessions to branch tips and flags the current one', () => {
  const graph: TaijiGraph = {
    root: '/p',
    is_repo: true,
    commits: [
      commit('m', ['a'], 0, 'merge'),
      commit('t', ['b'], 1, 'topic'),
      commit('a', ['b'], 0, 'main work'),
      commit('b', [], 0, 'init'),
    ],
    branches: [
      { name: 'main', sha: 'm', is_head: true },
      { name: 'task/fix', sha: 't', is_head: false },
    ],
    max_lane: 1,
  }
  const sessions: TaijiSessionMap = {
    repo_root: '/p',
    projects: [
      {
        key: 'k1',
        root: '/p',
        branch: 'main',
        worktree: false,
        sessions: [{ name: 'router', title: 'Router talk', updated_at: 5, messages: 1 }],
      },
      {
        key: 'k2',
        root: '/p-wt',
        branch: 'task/fix',
        worktree: true,
        sessions: [
          { name: 'w2', title: 'second worker', updated_at: 9, messages: 2 },
          { name: 'w1', title: 'first worker', updated_at: 7, messages: 1 },
        ],
      },
      {
        key: 'k3',
        root: '/p-det',
        branch: null,
        worktree: true,
        sessions: [{ name: 'lost', title: 'lost', updated_at: 1, messages: 1 }],
      },
    ],
  }

  const rows = attachSessions(graph, sessions, '/home/u/.zenagent/projects/k2/sessions/w1.jsonl')

  const bySha = new Map(rows.map((row) => [row.commit.sha, row]))
  assert.deepEqual([...bySha.get('m')!.sessions.map((s) => s.name)], ['router'])
  assert.equal(bySha.get('m')!.branch?.name, 'main')
  assert.deepEqual([...bySha.get('t')!.sessions.map((s) => s.title)], ['second worker', 'first worker'])
  assert.equal(bySha.get('t')!.isHere, true)
  assert.equal(bySha.get('m')!.isHere, false)
  assert.equal(bySha.get('a')!.sessions.length, 0)
  assert.equal(bySha.get('b')!.sessions.length, 0)
})

test('attachSessions without a current path marks nothing here', () => {
  const graph: TaijiGraph = {
    root: '/p',
    is_repo: true,
    commits: [commit('m', [], 0)],
    branches: [{ name: 'main', sha: 'm', is_head: true }],
    max_lane: 0,
  }
  const sessions: TaijiSessionMap = {
    repo_root: '/p',
    projects: [
      {
        key: 'k1',
        root: '/p',
        branch: 'main',
        worktree: false,
        sessions: [{ name: 's1', title: 'T', updated_at: 1, messages: 1 }],
      },
    ],
  }

  const rows = attachSessions(graph, sessions, undefined)

  assert.equal(rows[0]!.isHere, false)
  assert.equal(rows[0]!.sessions.length, 1)
})

test('statusMeta maps registry statuses to tone, pulse and an i18n key', () => {
  assert.deepEqual(statusMeta('running'), {
    key: 'panel.taiji.status.running',
    tone: 'run',
    pulse: true,
  })
  assert.deepEqual(statusMeta('ok'), { key: 'panel.taiji.status.ok', tone: 'wait', pulse: false })
  assert.deepEqual(statusMeta('error'), { key: 'panel.taiji.status.error', tone: 'error', pulse: false })
  assert.equal(statusMeta('merged')?.tone, 'done')
  assert.equal(statusMeta('interrupted')?.tone, 'done')
  assert.equal(statusMeta('killed')?.tone, 'done')
  assert.equal(statusMeta(undefined), null)
  assert.equal(statusMeta('nonsense'), null)
})

test('relativeTime renders compact, locale-neutral spans', () => {
  const now = 100_000_000
  assert.equal(relativeTime(now - 30, now), 'now')
  assert.equal(relativeTime(now - 720, now), '12m')
  assert.equal(relativeTime(now - 3 * 3600, now), '3h')
  assert.equal(relativeTime(now - 2 * 86400, now), '2d')
  assert.equal(relativeTime(now - 40 * 86400, now), '40d')
  assert.equal(relativeTime(undefined, now), '')
})

test('attachSessions marks the one current session, not the whole row', () => {
  const graph: TaijiGraph = {
    root: '/p',
    is_repo: true,
    commits: [commit('m', [], 0)],
    branches: [{ name: 'main', sha: 'm', is_head: true }],
    max_lane: 0,
  }
  const sessions: TaijiSessionMap = {
    repo_root: '/p',
    projects: [
      {
        key: 'k1',
        root: '/p',
        branch: 'main',
        worktree: false,
        sessions: [
          { name: 'current', title: 'Current one', updated_at: 9, messages: 1 },
          { name: 'past', title: 'Past work', updated_at: 8, messages: 1 },
        ],
      },
    ],
  }

  const rows = attachSessions(graph, sessions, '/home/u/projects/k1/sessions/current.jsonl')

  const byName = new Map(rows[0]!.sessions.map((s) => [s.name, s]))
  assert.equal(byName.get('current')!.here, true)
  assert.equal(byName.get('past')!.here, false)
  assert.equal(rows[0]!.isHere, true)
})

test('pickChips puts the current session first, workers next, caps and overflows', () => {
  const mk = (name: string, updated: number, status?: string) => ({
    name,
    title: name,
    updated_at: updated,
    messages: 1,
    ...(status ? { status } : {}),
  })
  const sessions = [
    { ...mk('old-plain', 1), here: false },
    { ...mk('waiting', 5, 'ok'), here: false },
    { ...mk('current', 9), here: true },
    { ...mk('running', 7, 'running'), here: false },
    { ...mk('broken', 6, 'error'), here: false },
  ]

  const picked = pickChips(sessions, 3)

  assert.deepEqual(picked.chips.map((s) => s.name), ['current', 'running', 'waiting'])
  assert.equal(picked.overflow, 1)
})

test('pickChips keeps plain sessions only when nothing better exists', () => {
  const sessions = [
    { name: 'current', title: 'C', updated_at: 9, messages: 1, here: true },
    { name: 'plain', title: 'P', updated_at: 8, messages: 1, here: false },
  ]
  const picked = pickChips(sessions, 3)
  assert.deepEqual(picked.chips.map((s) => s.name), ['current'])
  assert.equal(picked.overflow, 0)
})

test('queued is a known done-tone status', () => {
  assert.deepEqual(statusMeta('queued'), {
    key: 'panel.taiji.status.queued',
    tone: 'done',
    pulse: false,
  })
})
