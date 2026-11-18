/** Directory tree over changed files: nested folders, folder-first sorting,
 * collapse flattening and recursive counts. */

import test from 'node:test'
import assert from 'node:assert/strict'
import { buildFileTree, dirPaths, flattenTree } from '../src/ui/rightbar/fileTree.ts'

const files = [
  { path: 'src/zeta.ts', id: 'zeta' },
  { path: 'src/nested/beta.ts', id: 'beta' },
  { path: 'src/nested/deep/gamma.ts', id: 'gamma' },
  { path: 'README.md', id: 'readme' },
  { path: 'src/alpha.ts', id: 'alpha' },
]

test('buildFileTree nests folders and counts files recursively', () => {
  const root = buildFileTree(files)
  assert.deepEqual(
    root.children.map((child) => child.name),
    ['src', 'README.md'],
  )
  const src = root.children[0]
  assert.equal(src.kind, 'dir')
  if (src.kind !== 'dir') return
  assert.equal(src.files, 4)
  assert.deepEqual(
    src.children.map((child) => child.name),
    ['nested', 'alpha.ts', 'zeta.ts'],
  )
})

test('flattenTree honors collapsed folders', () => {
  const root = buildFileTree(files)
  const open = flattenTree(root, new Set())
  assert.deepEqual(
    open.map((row) => row.node.name),
    ['src', 'nested', 'deep', 'gamma.ts', 'beta.ts', 'alpha.ts', 'zeta.ts', 'README.md'],
  )
  const collapsed = flattenTree(root, new Set(['src/nested']))
  assert.deepEqual(
    collapsed.map((row) => row.node.name),
    ['src', 'nested', 'alpha.ts', 'zeta.ts', 'README.md'],
  )
  const top = flattenTree(root, new Set(['src']))
  assert.deepEqual(
    top.map((row) => row.node.name),
    ['src', 'README.md'],
  )
})

test('dirPaths lists every folder for collapse-all', () => {
  assert.deepEqual(dirPaths(buildFileTree(files)), ['src', 'src/nested', 'src/nested/deep'])
})

test('buildFileTree tolerates root-level files and empty paths', () => {
  const root = buildFileTree([{ path: 'a.ts' }, { path: '' }])
  assert.deepEqual(
    root.children.map((child) => child.path),
    ['a.ts'],
  )
  assert.equal(root.files, 1)
})
