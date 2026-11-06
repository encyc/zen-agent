import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  canDequeuePending,
  canPromotePending,
  nextDraft,
  queuedNewestFirst,
} from '../src/state/pending.ts'
import type { PendingMessage } from '../src/rpc/types.ts'

const queued = (id: number, images = 0): PendingMessage => ({
  id,
  lane: 'queue',
  text: `queued ${id}`,
  images,
  created_at: 1,
})

test('only a running, image-free queued item can be promoted', () => {
  assert.equal(canPromotePending(queued(1), true), true)
  assert.equal(canPromotePending(queued(1), false), false)
  assert.equal(canPromotePending(queued(1, 2), true), false)
  assert.equal(canPromotePending({ ...queued(1), lane: 'steer' }, true), false)
})

test('dequeue is offered for the latest queued item only when it has no images', () => {
  assert.equal(canDequeuePending([queued(1)]), true)
  assert.equal(canDequeuePending([queued(1), queued(2, 1)]), false)
  assert.equal(canDequeuePending([{ ...queued(1), lane: 'steer' }]), false)
  assert.equal(canDequeuePending([]), false)
})

test('a taken-back message appends to the draft instead of replacing it', () => {
  assert.equal(nextDraft('', 'taken'), 'taken')
  assert.equal(nextDraft('draft', 'taken'), 'draft\ntaken')
})

test('the queue headline is the newest entry, matching dequeue', () => {
  const items = [queued(1), { ...queued(2), lane: 'steer' } as PendingMessage, queued(3)]
  assert.deepEqual(
    queuedNewestFirst(items).map((item) => item.id),
    [3, 1],
  )
  assert.deepEqual(
    items.map((item) => item.id),
    [1, 2, 3],
    'the pending snapshot must not be reversed in place',
  )
})
