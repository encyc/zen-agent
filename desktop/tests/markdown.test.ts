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
