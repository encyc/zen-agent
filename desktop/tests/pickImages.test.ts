import assert from 'node:assert/strict'
import { test } from 'node:test'
import { imagePathsFromSelection } from '../src/ui/composer/pickImages.ts'

test('keeps image paths and drops everything else', () => {
  assert.deepEqual(
    imagePathsFromSelection(['/tmp/a.png', '/tmp/b.txt', '/tmp/c.JPEG']),
    ['/tmp/a.png', '/tmp/c.JPEG'],
  )
})

test('a single selection and a cancelled dialog are both handled', () => {
  assert.deepEqual(imagePathsFromSelection('/tmp/a.webp'), ['/tmp/a.webp'])
  assert.deepEqual(imagePathsFromSelection(null), [])
})

test('drops bmp because the backend cannot read it', () => {
  assert.deepEqual(imagePathsFromSelection(['/tmp/a.bmp', '/tmp/b.png']), ['/tmp/b.png'])
})
