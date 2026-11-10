/** A chat reference may carry the real absolute path; the Files tree is keyed
 * by project-relative paths, so opening a reference folds it back first. Run
 * with `node --test tests/workspacePath.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { toWorkspacePath } from '../src/ui/rightbar/workspacePath.ts'

test('an absolute path inside the project folds back to the tree form', () => {
  assert.equal(toWorkspacePath('/work/zen/src/a.ts', '/work/zen'), 'src/a.ts')
  assert.equal(toWorkspacePath('/work/zen/zenagent/agent', '/work/zen'), 'zenagent/agent')
  assert.equal(toWorkspacePath('/work/zen/src/a.ts/', '/work/zen'), 'src/a.ts')
  assert.equal(toWorkspacePath('/work/zen', '/work/zen'), '')
})

test('backslashes and trailing slashes normalize', () => {
  assert.equal(toWorkspacePath('src\\agent\\', '/work/zen'), 'src/agent')
})

test('relative and outside paths pass through unchanged', () => {
  assert.equal(toWorkspacePath('src/a.ts', '/work/zen'), 'src/a.ts')
  assert.equal(toWorkspacePath('/etc/hosts', '/work/zen'), '/etc/hosts')
  // A sibling whose name only starts with the root is not inside it.
  assert.equal(toWorkspacePath('/work/zenish/a.ts', '/work/zen'), '/work/zenish/a.ts')
  assert.equal(toWorkspacePath('/work/zen/src', ''), '/work/zen/src')
  assert.equal(toWorkspacePath('/work/zen/src', null), '/work/zen/src')
})

test('windows drive roots fold case-insensitively', () => {
  assert.equal(toWorkspacePath('c:/Work/Zen/src/a.ts', 'C:/Work/Zen'), 'src/a.ts')
  assert.equal(toWorkspacePath('C:\\Work\\Zen\\src\\a.ts', 'C:/Work/Zen'), 'src/a.ts')
})
