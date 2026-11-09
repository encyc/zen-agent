/** The one place the desktop opens the Tauri dialog plugin: the three
 * pickers share `openDialog` but keep their own options (file / images /
 * directory). */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  DIALOG_OPEN,
  openDialog,
  pickProjectFolder,
  pickSessionFileToOpen,
  type DialogOptions,
  type DialogSelection,
} from '../src/ui/sessions/fileDialog.ts'

const here = dirname(fileURLToPath(import.meta.url))

function recording(selection: DialogSelection | Error) {
  const calls: DialogOptions[] = []
  return {
    calls,
    run: async (options: DialogOptions): Promise<DialogSelection> => {
      calls.push(options)
      if (selection instanceof Error) throw selection
      return selection
    },
  }
}

test('openDialog goes through the plugin open command', async () => {
  const calls: Array<[string, unknown]> = []
  const selection = await openDialog({ multiple: true }, async (command, args) => {
    calls.push([command, args])
    return ['/tmp/a.png']
  })

  assert.equal(DIALOG_OPEN, 'plugin:dialog|open')
  assert.deepEqual(calls, [[DIALOG_OPEN, { options: { multiple: true } }]])
  assert.deepEqual(selection, ['/tmp/a.png'])
})

test('the session picker asks for one jsonl file', async () => {
  const rec = recording('/tmp/s.jsonl')
  assert.equal(await pickSessionFileToOpen(rec.run), '/tmp/s.jsonl')
  assert.deepEqual(rec.calls, [
    {
      multiple: false,
      directory: false,
      title: 'ZenAgent',
      filters: [{ name: 'Sessions', extensions: ['jsonl'] }],
    },
  ])
})

test('a cancelled or unexpected session selection is null', async () => {
  assert.equal(await pickSessionFileToOpen(recording(null).run), null)
  assert.equal(await pickSessionFileToOpen(recording(['/tmp/a.jsonl']).run), null)
})

test('a failing session dialog is null', async () => {
  assert.equal(await pickSessionFileToOpen(recording(new Error('no dialog')).run), null)
})

test('the project picker asks for one directory', async () => {
  const rec = recording('/tmp/project')
  assert.equal(await pickProjectFolder(rec.run), '/tmp/project')
  assert.deepEqual(rec.calls, [{ directory: true, multiple: false, title: 'ZenAgent' }])
})

test('a cancelled or failing folder pick is null', async () => {
  assert.equal(await pickProjectFolder(recording(null).run), null)
  assert.equal(await pickProjectFolder(recording(['/tmp/project']).run), null)
  assert.equal(await pickProjectFolder(recording(new Error('no dialog')).run), null)
})

test('only the dialog service speaks to the plugin command', () => {
  const offenders: string[] = []
  // The dev/e2e bridge answers the command instead of shipping it, so it has
  // to name it too; it is not renderer code.
  const devBridgeDir = join(here, '..', 'src', 'dev')
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) {
        if (path !== devBridgeDir) walk(path)
      } else if (entry.name !== 'fileDialog.ts' && readFileSync(path, 'utf8').includes(DIALOG_OPEN)) {
        offenders.push(path)
      }
    }
  }
  walk(join(here, '..', 'src'))
  assert.deepEqual(offenders, [])
})
