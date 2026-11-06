import assert from 'node:assert/strict'
import { test } from 'node:test'
import { armDelete, deleteArmed } from '../src/state/armedDelete.ts'

test('the first click arms a row and a repeated click disarms it', () => {
  assert.equal(armDelete(null, '/a'), '/a')
  assert.equal(armDelete('/a', '/a'), null)
  assert.equal(deleteArmed('/a', '/a'), true)
  assert.equal(deleteArmed('/a', '/b'), false)
  assert.equal(deleteArmed(null, '/b'), false)
})

test('arming a different row moves the arm instead of deleting the old one', () => {
  const armed = armDelete('/a', '/b')
  assert.equal(armed, '/b')
  assert.equal(deleteArmed(armed, '/a'), false)
})
