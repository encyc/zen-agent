/** The collapsed reasoning row shows one plain line: the model's markdown is
 * rendered only once the body is open, so the summary must not echo markers.
 * Run with `node --test tests/thinkingPreview.test.ts` */

import test from 'node:test'
import assert from 'node:assert/strict'
import { plainLine, thinkingPreview } from '../src/ui/chat/thinkingPreview.ts'

test('inline markers are stripped from the summary line', () => {
  assert.equal(plainLine('**Step 1**: read `views.py`'), 'Step 1: read views.py')
  assert.equal(plainLine('~~old~~ __new__'), 'old new')
  assert.equal(plainLine('a *word* and 2 * 3'), 'a word and 2 * 3')
})

test('block markers are stripped too', () => {
  assert.equal(plainLine('## Plan'), 'Plan')
  assert.equal(plainLine('- first bullet'), 'first bullet')
  assert.equal(plainLine('2. second step'), 'second step')
  assert.equal(plainLine('> quoted reason'), 'quoted reason')
})

test('a link keeps its label, not its destination', () => {
  assert.equal(plainLine('see [the guide](https://example.com/a)'), 'see the guide')
})

test('only the first line is summarised', () => {
  assert.equal(thinkingPreview('first line\nsecond line'), 'first line')
  assert.equal(thinkingPreview('\nlead'), '')
})

test('a long line is clipped to the budget with an ellipsis', () => {
  const preview = thinkingPreview('x'.repeat(200))
  assert.ok(preview.length <= 96)
  assert.ok(preview.endsWith('…'))
  assert.equal(thinkingPreview('x'.repeat(96)).length, 96)
})
