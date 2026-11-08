import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  diffSummary,
  groupWorkers,
  statusTone,
  taskAge,
} from '../src/ui/rightbar/taijiBoard.ts'
import type { TaijiWorker } from '../src/rpc/types.ts'

const worker = (name: string, status: string, updated: number, extra: Partial<TaijiWorker> = {}): TaijiWorker => ({
  name,
  branch: `task/${name}`,
  base: 'main',
  status,
  summary: `${name} summary`,
  session: `/s/${name}.jsonl`,
  created_at: updated - 60,
  updated_at: updated,
  attention: null,
  diffstat: null,
  ...extra,
})

test('groupWorkers sorts every status into its intervention band', () => {
  const workers = [
    worker('newest-ok', 'ok', 9),
    worker('asking', 'ok', 8, { attention: { question: 'A?', options: [] } }),
    worker('merged', 'merged', 7),
    worker('queued-one', 'queued', 6),
    worker('running-one', 'running', 5),
    worker('broken', 'error', 4),
    worker('old-ok', 'ok', 3),
    worker('killed', 'killed', 2),
    worker('running-two', 'running', 1),
    worker('interrupted', 'interrupted', 0),
  ]

  const groups = groupWorkers(workers)

  assert.deepEqual(groups.attention.map((w) => w.name), ['asking'])
  assert.deepEqual(groups.acceptance.map((w) => w.name), ['newest-ok', 'old-ok'])
  assert.deepEqual(groups.running.map((w) => w.name), ['running-one', 'running-two'])
  assert.deepEqual(groups.queued.map((w) => w.name), ['queued-one'])
  assert.deepEqual(groups.history.map((w) => w.name), ['merged', 'broken', 'killed', 'interrupted'])
})

test('groupWorkers keeps every group ordered most-recent-first', () => {
  const workers = [
    worker('late', 'running', 20),
    worker('early', 'running', 10),
  ]

  const groups = groupWorkers(workers)

  assert.deepEqual(groups.running.map((w) => w.name), ['late', 'early'])
})

test('statusTone maps every registry status and falls back to plain', () => {
  assert.equal(statusTone('running'), 'run')
  assert.equal(statusTone('queued'), 'wait')
  assert.equal(statusTone('ok'), 'done')
  assert.equal(statusTone('error'), 'fail')
  assert.equal(statusTone('interrupted'), 'fail')
  assert.equal(statusTone('merged'), 'plain')
  assert.equal(statusTone('killed'), 'plain')
  assert.equal(statusTone('mystery'), 'plain')
})

test('taskAge ticks minutes under an hour, then coarse hours and days', () => {
  const now = 100_000
  assert.deepEqual(taskAge(now - 40, now), { kind: 'tick', text: '00:40' })
  assert.deepEqual(taskAge(now - 3600, now), { kind: 'hours', hours: 1, minutes: 0 })
  assert.deepEqual(taskAge(now - 7200 - 300, now), { kind: 'hours', hours: 2, minutes: 5 })
  assert.deepEqual(taskAge(now - 3 * 86400, now), { kind: 'days', days: 3 })
})

test('diffSummary tallies plus/minus and a file count, null when unknowable', () => {
  assert.equal(diffSummary(null), null)
  assert.equal(diffSummary([]), null)
  assert.deepEqual(
    diffSummary([
      { path: 'a.ts', adds: 12, dels: 3 },
      { path: 'b.ts', adds: 0, dels: 5 },
    ]),
    { adds: 12, dels: 8, count: 2 },
  )
})
