import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clampInputHeight, inputHeightLimits } from '../src/ui/composer/inputHeight.ts'

test('the composer drag floor is two text lines plus padding', () => {
  const limits = inputHeightLimits(24, 4, 1000)
  assert.equal(limits.min, 52)
  assert.equal(limits.max, 600)
  assert.equal(limits.step, 24)
  assert.equal(clampInputHeight(20, limits), 52)
  assert.equal(clampInputHeight(401.6, limits), 402)
  assert.equal(clampInputHeight(9999, limits), 600)
})

test('a short viewport never sets the ceiling below the floor', () => {
  const limits = inputHeightLimits(24, 4, 60)
  assert.equal(limits.max, 52)
  assert.equal(clampInputHeight(500, limits), 52)
})

test('missing metrics fall back to the default row height', () => {
  assert.deepEqual(inputHeightLimits(Number.NaN, Number.NaN, 720), {
    min: 48,
    max: 432,
    step: 24,
  })
})
