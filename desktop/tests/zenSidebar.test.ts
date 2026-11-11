import test from 'node:test'
import assert from 'node:assert/strict'

import { splitRoster, zenStatusKey, zenStatusTone } from '../src/state/zenSidebar.ts'
import { translate } from '../src/i18n/messages.ts'
import type { RosterItem } from '../src/rpc/types.ts'

function row(overrides: Partial<RosterItem>): RosterItem {
  return {
    path: '/p/w1.jsonl',
    title: 'w1',
    focus: '',
    role: 'worker',
    status: 'idle',
    last_report: '',
    ...overrides,
  }
}

test('splitRoster separates the router from workers', () => {
  const router = row({ path: '/p/router.jsonl', role: 'router', status: 'idle' })
  const workers = [row(), row({ path: '/p/w2.jsonl', title: 'w2' })]
  const split = splitRoster([router, ...workers])
  assert.equal(split.router?.path, '/p/router.jsonl')
  assert.deepEqual(split.workers.map((w) => w.path), ['/p/w1.jsonl', '/p/w2.jsonl'])
})

test('splitRoster tolerates a roster without a router row', () => {
  const split = splitRoster([row()])
  assert.equal(split.router, null)
  assert.equal(split.workers.length, 1)
})

test('zenStatusKey maps every backend status to an i18n key', () => {
  for (const status of ['idle', 'queued', 'working', 'waiting', 'blocked', 'failed']) {
    assert.equal(zenStatusKey(status), `zen.status.${status}`)
  }
  assert.equal(zenStatusKey('mystery'), 'zen.status.idle')
})

test('zenStatusTone buckets statuses for the roster dot', () => {
  assert.equal(zenStatusTone('working'), 'active')
  assert.equal(zenStatusTone('queued'), 'active')
  assert.equal(zenStatusTone('blocked'), 'blocked')
  assert.equal(zenStatusTone('failed'), 'blocked')
  assert.equal(zenStatusTone('waiting'), 'settled')
  assert.equal(zenStatusTone('idle'), 'settled')
  assert.equal(zenStatusTone('???'), 'settled')
})

test('zen i18n keys exist in both locales with parity', () => {
  const keys = [
    'zen.toggle',
    'zen.toggleHint',
    'zen.router',
    'zen.workers',
    'zen.workerDrawer',
    'zen.drawerClose',
    'zen.focusLabel',
    'zen.status.idle',
    'zen.status.queued',
    'zen.status.working',
    'zen.status.waiting',
    'zen.status.blocked',
    'zen.status.failed',
  ]
  for (const key of keys) {
    const zh = translate('zh', key)
    const en = translate('en', key)
    assert.notEqual(zh, key, `zh missing ${key}`)
    assert.notEqual(en, key, `en missing ${key}`)
  }
})
