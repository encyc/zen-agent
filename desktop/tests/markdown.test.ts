/** Rendering contract for assistant markdown: fenced code becomes a copyable
 * block, inline code stays inline, and the localized labels survive escaping.
 * Run with `node --test tests/markdown.test.ts`. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { markdownToHtml } from '../src/ui/chat/markdown.ts'

const labels = { copy: 'Copy', copied: 'Copied' }

test('fenced code becomes a copyable block with escaped source', () => {
  const html = markdownToHtml('```ts\nconst ok = 1 < 2 && 3 > 2\n```', labels)

  assert.match(html, /class="md-code-block"/)
  assert.match(html, /class="md-code-lang">ts</)
  assert.match(html, /data-copy-code/)
  assert.match(html, /data-copy-label="Copy"/)
  assert.match(html, /data-copied-label="Copied"/)
  assert.match(html, /1 &lt; 2 &amp;&amp; 3 &gt; 2/)
})

test('a fence without a language falls back to a text label', () => {
  const html = markdownToHtml('```\nplain\n```', labels)
  assert.match(html, /class="md-code-lang">text</)
})

test('inline code is not wrapped in a copy block', () => {
  const html = markdownToHtml('run `npm test` first', labels)

  assert.doesNotMatch(html, /md-code-block/)
  assert.match(html, /<code>npm test<\/code>/)
})

test('copy labels are attribute-escaped', () => {
  const html = markdownToHtml('```\nx\n```', {
    copy: 'A "quote" & more',
    copied: "it's done",
  })

  assert.match(html, /data-copy-label="A &quot;quote&quot; &amp; more"/)
  assert.match(html, /data-copied-label="it&#39;s done"/)
})

test('file paths in inline code become file links when enabled', () => {
  const html = markdownToHtml('see `src/a.ts` now', labels, { fileLinks: true })

  assert.match(
    html,
    /<code class="md-file-ref" data-file-path="src\/a.ts" role="link" tabindex="0">src\/a\.ts<\/code>/,
  )
})

test('file links are opt-in', () => {
  const html = markdownToHtml('see `src/a.ts` now', labels)
  assert.match(html, /<code>src\/a\.ts<\/code>/)
})

test('ordinary inline code is never a file link', () => {
  const html = markdownToHtml('run `npm test` first', labels, { fileLinks: true })
  assert.match(html, /<code>npm test<\/code>/)
})

test('a markdown link naming a file becomes a file link', () => {
  const html = markdownToHtml(
    '[host](zenagent/cli/rpc/host.py)',
    labels,
    { fileLinks: true },
  )

  assert.match(html, /class="md-file-ref" data-file-path="zenagent\/cli\/rpc\/host\.py"/)
})

test('a markdown link to a folder is an explicit file link too', () => {
  // An explicit destination is a reference even without an extension: the
  // Files panel decides whether the path is a file or a folder.
  const html = markdownToHtml('[rpc](zenagent/cli/rpc)', labels, { fileLinks: true })

  assert.match(
    html,
    /<code class="md-file-ref" data-file-path="zenagent\/cli\/rpc" role="link" tabindex="0">rpc<\/code>/,
  )
})

test('a link destination is decoded and dot-relative paths are normalized', () => {
  const html = markdownToHtml('[a](./src/my%20file.ts)', labels, { fileLinks: true })

  assert.match(html, /data-file-path="src\/my file\.ts"/)
})

test('without file links a local destination keeps only its label', () => {
  // The md viewer paints markdown but wires no panel handler; a native anchor
  // there would navigate the shell, so the link wrapper is dropped.
  const html = markdownToHtml('see [rpc](zenagent/cli/rpc) now', labels)

  assert.doesNotMatch(html, /<a /)
  assert.match(html, /see rpc now/)
})

test('http links stay ordinary anchors and never take the shell away', () => {
  const html = markdownToHtml('[docs](https://example.com/a.ts)', labels, { fileLinks: true })
  assert.match(
    html,
    /<a target="_blank" rel="noreferrer" href="https:\/\/example\.com\/a\.ts">docs<\/a>/,
  )
})

test('a fragment link keeps the page and needs no new window', () => {
  const html = markdownToHtml('[top](#top)', labels, { fileLinks: true })
  assert.match(html, /<a href="#top">top<\/a>/)
})

