import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseRoomCommand, roomCommandMenu } from '../src/state/roomCommands.ts'

test('exact room commands parse', () => {
  assert.deepEqual(parseRoomCommand('/reload', ['research']), { kind: 'reload' })
  assert.deepEqual(parseRoomCommand('/stop', ['research']), { kind: 'stop' })
  assert.deepEqual(parseRoomCommand('/help', ['research']), { kind: 'help' })
  assert.deepEqual(parseRoomCommand('/reset research', ['research']), {
    kind: 'reset',
    roleId: 'research',
  })
})

test('an unknown reset target is reported, not posted', () => {
  assert.deepEqual(parseRoomCommand('/reset ghost', ['research']), {
    kind: 'unknown-reset',
    roleId: 'ghost',
  })
})

test('any other slash text is an ordinary message', () => {
  assert.equal(parseRoomCommand('/usr/bin 看看', ['research']), null)
  assert.equal(parseRoomCommand('/research hi', ['research']), null)
  assert.equal(parseRoomCommand('/stop now', ['research']), null)
})

test('the menu filters by prefix and insert mode appends a space', () => {
  assert.deepEqual(
    roomCommandMenu('/re').map((entry) => entry.insert),
    ['/reload ', '/reset '],
  )
  assert.deepEqual(roomCommandMenu('/sto').map((entry) => entry.insert), ['/stop '])
  assert.deepEqual(roomCommandMenu('hello'), [])
})
