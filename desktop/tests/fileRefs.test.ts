/** File-reference detection for assistant markdown: a transcript token only
 * becomes a clickable file when it names a real path shape, so ordinary dotted
 * identifiers and URLs stay plain text. Run with
 * `node --test tests/fileRefs.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseFileRef } from '../src/ui/chat/fileRefs.ts'

test('relative and absolute paths with known extensions are file refs', () => {
  assert.equal(parseFileRef('src/a.ts'), 'src/a.ts')
  assert.equal(parseFileRef('./zenagent/cli/rpc/host.py'), './zenagent/cli/rpc/host.py')
  assert.equal(parseFileRef('@desktop/package.json'), 'desktop/package.json')
  assert.equal(parseFileRef('/Users/mac/zen-agent/README.md'), '/Users/mac/zen-agent/README.md')
})

test('a line suffix selects the same file', () => {
  assert.equal(parseFileRef('src/a.ts:12'), 'src/a.ts')
  assert.equal(parseFileRef('src/a.ts:12:4'), 'src/a.ts')
  assert.equal(parseFileRef('src/a.ts#L12-L20'), 'src/a.ts')
})

test('dotted identifiers, URLs and extensionless tokens stay plain text', () => {
  assert.equal(parseFileRef('store.messages'), null)
  assert.equal(parseFileRef('https://example.com/a.ts'), null)
  assert.equal(parseFileRef('mailto:dev@example.com'), null)
  assert.equal(parseFileRef('src/a'), null)
  assert.equal(parseFileRef(''), null)
})

test('bare names need a known extension or a known project file name', () => {
  assert.equal(parseFileRef('README.md'), 'README.md')
  assert.equal(parseFileRef('Makefile'), 'Makefile')
  assert.equal(parseFileRef('helper'), null)
})

test('a trailing slash marks a folder without needing an extension', () => {
  assert.equal(parseFileRef('zenagent/agent/'), 'zenagent/agent')
  assert.equal(parseFileRef('/Users/mac/zen-agent/zenagent/agent/'), '/Users/mac/zen-agent/zenagent/agent')
  assert.equal(parseFileRef('desktop/src\\'), 'desktop/src')
  // Without the folder marker, prose with slashes stays plain text.
  assert.equal(parseFileRef('and/or'), null)
  assert.equal(parseFileRef('/'), null)
  assert.equal(parseFileRef('./'), null)
  assert.equal(parseFileRef('../'), null)
})
