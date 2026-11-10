/** Language selection for the work-panel viewer: a file path maps onto a
 * lazily loaded Shiki grammar, and unknown extensions stay plain text. */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { langForPath, resolveLang, THEMES } from '../src/ui/rightbar/highlight.ts'

test('source files map onto their shiki language', () => {
  assert.equal(langForPath('a.py'), 'python')
  assert.equal(langForPath('desktop/src/a.ts'), 'typescript')
  assert.equal(langForPath('desktop/src/a.tsx'), 'tsx')
  assert.equal(langForPath('a.js'), 'javascript')
  assert.equal(langForPath('a.mjs'), 'javascript')
  assert.equal(langForPath('src/a.rs'), 'rust')
  assert.equal(langForPath('src/a.yaml'), 'yaml')
  assert.equal(langForPath('a.json'), 'json')
})

test('a bare project file keeps its alias language', () => {
  assert.equal(langForPath('Makefile'), 'make')
  assert.equal(langForPath('Dockerfile'), 'docker')
})

test('an unknown or plain extension stays unhighlighted', () => {
  assert.equal(langForPath('notes.unknown'), null)
  assert.equal(langForPath('README'), null)
  assert.equal(resolveLang('text'), null)
  assert.equal(resolveLang(''), null)
})

test('language aliases and case resolve to the grammar id', () => {
  assert.equal(resolveLang('py'), 'python')
  assert.equal(resolveLang('PY'), 'python')
  assert.equal(resolveLang('ts'), 'typescript')
  assert.equal(resolveLang('rs'), 'rust')
})

test('the editor themes are the one-light / one-dark-pro pair', () => {
  assert.deepEqual(THEMES, { light: 'one-light', dark: 'one-dark-pro' })
})
