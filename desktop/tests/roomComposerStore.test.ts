import assert from 'node:assert/strict'
import { test } from 'node:test'
import { roomComposerState, useComposerStore } from '../src/state/stores.ts'

test('room drafts, quotes and images never leak across projects', () => {
  const store = useComposerStore.getState()
  store.setRoomDraft('/a', '@research hi')
  store.setRoomQuote('/a', { id: 'm1', author: { kind: 'user', id: 'you' }, text: 'q' })
  store.addRoomImages('/a', ['/tmp/a.png'])

  assert.equal(roomComposerState('/a').draft, '@research hi')
  assert.equal(roomComposerState('/b').draft, '')
  assert.equal(roomComposerState('/b').quote, null)
  assert.deepEqual(roomComposerState('/b').images, [])
})

test('images are added and removed per project', () => {
  const store = useComposerStore.getState()
  store.clearRoomImages('/a')
  store.addRoomImages('/a', ['/tmp/a.png', '/tmp/b.png'])
  const [first] = roomComposerState('/a').images

  store.removeRoomImage('/a', first.id)

  assert.deepEqual(
    roomComposerState('/a').images.map((image) => image.name),
    ['b.png'],
  )
  store.clearRoomImages('/a')
  assert.deepEqual(roomComposerState('/a').images, [])
})
