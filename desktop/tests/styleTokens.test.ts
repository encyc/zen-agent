/** The style-token guard's matcher: raw colour literals in component
 * stylesheets are reported, palette files are exempt, and a baseline entry
 * suppresses one known violation while staying visible in the report. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
// @ts-expect-error -- plain-node guard script, no type declarations
import { isExemptFile, scanCss } from '../../scripts/check-style-tokens.mjs'

const css = [
  '.a {',
  '  color: #ff453a;',
  '  background: rgba(255, 159, 10, 0.18);',
  '  border-color: hsl(10 90% 60%);',
  '}',
].join('\n')

test('raw colour literals are reported with their line and property', () => {
  const { violations } = scanCss(css, { file: 'desktop/src/ui/a.module.css' })
  assert.deepEqual(
    violations.map((entry) => [entry.line, entry.property]),
    [
      [2, 'color'],
      [3, 'background'],
      [4, 'border-color'],
    ],
  )
})

test('the token, theme and base stylesheets are exempt', () => {
  for (const file of [
    'desktop/src/styles/tokens.css',
    'desktop/src/styles/themes.css',
    'desktop/src/styles/base.css',
  ]) {
    assert.equal(isExemptFile(file), true)
    assert.deepEqual(scanCss(css, { file }).violations, [])
  }
  assert.equal(isExemptFile('desktop/src/ui/a.module.css'), false)
})

test('a baseline entry suppresses its violation and reports it as skipped', () => {
  const file = 'desktop/src/ui/a.module.css'
  const baseline = [{ file, line: 2, value: '#ff453a', reason: 'legacy danger red' }]
  const result = scanCss(css, { file, baseline })
  assert.deepEqual(
    result.violations.map((entry) => entry.line),
    [3, 4],
  )
  assert.deepEqual(
    result.skipped.map((entry) => [entry.line, entry.reason]),
    [[2, 'legacy danger red']],
  )
  assert.deepEqual(result.stale, [])
})

test('a baseline entry that matches nothing is reported as stale', () => {
  const file = 'desktop/src/ui/a.module.css'
  const entry = { file, line: 99, value: '#ff453a', reason: 'fixed long ago' }
  const result = scanCss(css, { file, baseline: [entry] })
  assert.deepEqual(result.stale, [entry])
})

test('token fallbacks, shadow alpha and mask stencils are not palette literals', () => {
  const exempt = [
    '.a { color: var(--dsw-alias-label-inverse, #fff); }',
    '.b { box-shadow: 0 8px 24px rgb(0 0 0 / 18%); }',
    '.c { box-shadow: 0 8px 24px color-mix(in oklab, #000 22%, transparent); }',
    '.d { -webkit-mask-image: linear-gradient(#000, transparent); }',
  ].join('\n')
  assert.deepEqual(scanCss(exempt, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})
