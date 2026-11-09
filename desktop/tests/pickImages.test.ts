import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  IMAGE_EXTENSIONS,
  imagePathsFromSelection,
  pickImagePaths,
} from '../src/ui/composer/pickImages.ts'
import type { DialogOptions, DialogSelection } from '../src/ui/sessions/fileDialog.ts'

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

test('the image picker offers the image filter and keeps only image paths', async () => {
  const calls: DialogOptions[] = []
  const paths = await pickImagePaths(async (options): Promise<DialogSelection> => {
    calls.push(options)
    return ['/tmp/a.png', '/tmp/b.txt', '/tmp/c.webp']
  })

  assert.deepEqual(paths, ['/tmp/a.png', '/tmp/c.webp'])
  assert.deepEqual(calls, [
    {
      multiple: true,
      directory: false,
      title: 'ZenAgent',
      filters: [{ name: 'Images', extensions: IMAGE_EXTENSIONS }],
    },
  ])
})

test('a cancelled image pick is empty', async () => {
  assert.deepEqual(await pickImagePaths(async () => null), [])
})

test('a dialog failure still rejects for the composer to swallow', async () => {
  await assert.rejects(
    pickImagePaths(async () => {
      throw new Error('no dialog')
    }),
    /no dialog/,
  )
})
