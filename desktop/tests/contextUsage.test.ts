import assert from 'node:assert/strict'
import { test } from 'node:test'
import { contextMeterView } from '../src/ui/composer/contextUsage.ts'

test('the context ring leads with remaining capacity by default', () => {
  const view = contextMeterView(0.8, 'remaining')
  assert.equal(view.percent, 20)
  assert.ok(Math.abs(view.ratio - 0.2) < 1e-9)
})

test('the context ring can lead with the used share', () => {
  const view = contextMeterView(0.8, 'used')
  assert.equal(view.percent, 80)
  assert.ok(Math.abs(view.ratio - 0.8) < 1e-9)
})
