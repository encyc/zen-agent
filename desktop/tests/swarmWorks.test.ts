/** The work item panel: ordering/round slicing, badges, and its two writes. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { translate } from '../src/i18n/messages.ts'
import type { SwarmTask, SwarmWork } from '../src/rpc/types.ts'
import { _useSwarmSender, useSwarmStore } from '../src/state/swarmStore.ts'
import {
  isScorable,
  roundsOf,
  scoreText,
  sortWorks,
  workBadgeKey,
} from '../src/state/swarmWorks.ts'

function work(
  id: string,
  state: SwarmWork['state'] = 'open',
  openedAt = 1,
): SwarmWork {
  return {
    id,
    title: id,
    state,
    rootMessageId: `m_${id}`,
    openedBy: { kind: 'user', id: 'you' },
    openedAt,
    closedAt: null,
    score: null,
    scoreNote: null,
    mergedInto: null,
    continuedFrom: null,
  }
}

function task(id: string, workItemId: string | null, createdAt = 1): SwarmTask {
  return {
    id,
    assignee: 'research',
    state: 'done',
    sourceMessageId: 'm_0',
    createdBy: { kind: 'user', id: 'you' },
    chainId: 'c_1',
    chainDepth: 1,
    chainCost: 0,
    createdAt,
    updatedAt: createdAt,
    resultMessageId: null,
    reason: null,
    derivedFrom: null,
    workItemId,
    runCost: 0,
  }
}

function reset(): void {
  useSwarmStore.setState({
    messages: [],
    tasks: [],
    agents: [],
    works: [],
    adhoc: [],
    selectedWork: null,
    roles: [],
    warnings: [],
    loaded: false,
    selectedTask: null,
    loadedProject: null,
    runRows: {},
  })
}

test('sortWorks puts active jobs first and the archive last', () => {
  const ordered = sortWorks([
    work('closed', 'closed', 9),
    work('review', 'review', 5),
    work('old-open', 'open', 1),
    work('new-open', 'open', 7),
  ])
  assert.deepEqual(
    ordered.map((row) => row.id),
    ['new-open', 'old-open', 'review', 'closed'],
  )
})

test('roundsOf keeps one item and orders the rounds by creation', () => {
  const rounds = roundsOf(
    [task('t2', 'w1', 2), task('t0', null, 0), task('t1', 'w1', 1)],
    'w1',
  )
  assert.deepEqual(rounds.map((row) => row.id), ['t1', 't2'])
})

test('scores and states map to their labels', () => {
  assert.equal(scoreText(work('w')), null)
  assert.equal(scoreText({ ...work('w'), score: 4 }), '4')
  assert.equal(isScorable(work('w')), true)
  assert.equal(isScorable({ ...work('w'), mergedInto: 'w2' }), false)
  for (const key of ['open', 'review', 'closed'] as const) {
    const i18nKey = workBadgeKey(key)
    assert.notEqual(translate('zh', i18nKey), i18nKey)
    assert.notEqual(translate('en', i18nKey), i18nKey)
  }
})

test('loadWorks asks for works/list and stores works plus ad-hoc rounds', async () => {
  reset()
  const sent: Record<string, unknown>[] = []
  _useSwarmSender({
    async send(request) {
      sent.push(request)
      return {
        ok: true,
        works: [work('w1')],
        adhoc: [task('t0', null)],
      }
    },
  })
  await useSwarmStore.getState().loadWorks('/p')
  assert.deepEqual(sent[0], {
    type: 'swarm',
    op: 'works',
    action: 'list',
    project: '/p',
  })
  assert.equal(useSwarmStore.getState().works.length, 1)
  assert.equal(useSwarmStore.getState().adhoc.length, 1)
})

test('scoreWork sends the human score and applies the returned record', async () => {
  reset()
  useSwarmStore.setState({ works: [work('w1')] })
  const sent: Record<string, unknown>[] = []
  _useSwarmSender({
    async send(request) {
      sent.push(request)
      return { ok: true, work: { ...work('w1'), score: 4 } }
    },
  })
  await useSwarmStore.getState().scoreWork('w1', 4, 'nice')
  assert.deepEqual(sent[0], {
    type: 'swarm',
    op: 'works',
    action: 'score',
    workId: 'w1',
    score: 4,
    note: 'nice',
  })
  assert.equal(useSwarmStore.getState().works[0].score, 4)
})

test('closeWork sends close and reopenWork sends reopen', async () => {
  reset()
  useSwarmStore.setState({ works: [work('w1')] })
  const sent: Record<string, unknown>[] = []
  _useSwarmSender({
    async send(request) {
      sent.push(request)
      return { ok: true, work: { ...work('w1'), state: 'closed' } }
    },
  })
  await useSwarmStore.getState().closeWork('w1', 5, null)
  await useSwarmStore.getState().reopenWork('w1')
  assert.deepEqual(sent.map((row) => row.action), ['close', 'reopen'])
  assert.equal(sent[0].score, 5)
})
