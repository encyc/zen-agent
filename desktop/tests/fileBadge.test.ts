/** Tree badges: a file's extension picks a short label and a static tone.
 * Run with `node --test tests/fileBadge.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fileBadge } from '../src/ui/rightbar/fileBadge.ts'

test('source extensions map onto their badge and tone', () => {
  assert.deepEqual(fileBadge('src/a.ts'), { label: 'TS', tone: 'blue' })
  assert.deepEqual(fileBadge('a.tsx'), { label: 'TS', tone: 'blue' })
  assert.deepEqual(fileBadge('a.js'), { label: 'JS', tone: 'amber' })
  assert.deepEqual(fileBadge('a.py'), { label: 'PY', tone: 'blue' })
  assert.deepEqual(fileBadge('a.rs'), { label: 'RS', tone: 'amber' })
  assert.deepEqual(fileBadge('a.json'), { label: '{}', tone: 'amber' })
  assert.deepEqual(fileBadge('a.md'), { label: 'MD', tone: 'blue' })
})

test('known bare project files keep a badge', () => {
  assert.deepEqual(fileBadge('Dockerfile'), { label: 'DK', tone: 'blue' })
  assert.deepEqual(fileBadge('tools/Makefile'), { label: 'MK', tone: 'neutral' })
})

test('extension matching is case-insensitive', () => {
  assert.deepEqual(fileBadge('README.MD'), { label: 'MD', tone: 'blue' })
})

test('unknown or extensionless files fall back to the generic icon', () => {
  assert.equal(fileBadge('notes.unknown'), null)
  assert.equal(fileBadge('README'), null)
  assert.equal(fileBadge(''), null)
})
