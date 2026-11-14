/** External model-markdown links must never navigate the app window (audit P2).
 * Run with `node --test tests/markdownLinks.test.ts` */

import test from 'node:test'
import assert from 'node:assert/strict'
import { externalLinkAction } from '../src/ui/chat/markdownLinks.ts'

test('http(s) links open in the system browser', () => {
  assert.equal(externalLinkAction('https://example.com/a'), 'browser')
  assert.equal(externalLinkAction('http://example.com'), 'browser')
  assert.equal(externalLinkAction('  https://example.com  '), 'browser')
})

test('everything else is intercepted, never navigated', () => {
  for (const href of [
    'javascript:alert(1)',
    'data:text/html,<h1>x</h1>',
    'file:///etc/passwd',
    'relative/page.html',
    'vbscript:x',
  ]) {
    assert.equal(externalLinkAction(href), 'ignore', href)
  }
})

test('in-page anchors and empty hrefs keep default behavior', () => {
  assert.equal(externalLinkAction('#section'), null)
  assert.equal(externalLinkAction(''), null)
  assert.equal(externalLinkAction(null), null)
})
