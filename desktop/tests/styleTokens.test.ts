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
    '.d { box-shadow: 0 3px 7.5px #0000000a; }',
    '.e { -webkit-mask-image: linear-gradient(#000, transparent); }',
    '.f { box-shadow: 0 1px 2px black; }',
  ].join('\n')
  assert.deepEqual(scanCss(exempt, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

/** One declaration per colour family the matcher must catch. */
const COLOUR_FAMILIES: Array<[string, string]> = [
  ['hex', 'color: #ff453a'],
  ['rgb', 'color: rgb(255 159 10 / 18%)'],
  ['hsl', 'color: hsl(10 90% 60%)'],
  ['hwb', 'color: hwb(30 10% 20%)'],
  ['lab', 'color: lab(50% 40 20)'],
  ['lch', 'color: lch(50% 40 20)'],
  ['oklab', 'color: oklab(0.5 0.1 0.1)'],
  ['oklch', 'color: oklch(0.5 0.1 30)'],
  ['white', 'color: white'],
  ['black', 'background: black'],
]

test('every raw colour family is reported', () => {
  for (const [family, declaration] of COLOUR_FAMILIES) {
    const { violations } = scanCss(`.a { ${declaration}; }`, {
      file: 'desktop/src/ui/a.module.css',
    })
    assert.equal(violations.length, 1, `${family} should be a violation`)
    assert.equal(violations[0].property, declaration.split(':')[0], family)
  }
})

test('token-only declarations pass', () => {
  const tokens = [
    '.a { color: var(--dsw-alias-label-primary); }',
    '.b { background: color-mix(in oklab, var(--dsw-alias-state-error-primary) 12%, transparent); }',
    '.c { border-color: var(--dsw-alias-line-outline, #fff); }',
  ].join('\n')
  assert.deepEqual(scanCss(tokens, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

test('a nested token fallback is stripped through its balanced parentheses', () => {
  const nested = [
    '.a { background: var(--dsw-alias-bg-scrim, rgb(0 0 0 / 40%)); }',
    '.b { box-shadow: var(--dsw-shadow-lg, 0 8px 24px #0000); }',
    '.c { color: var(--x, var(--y, #fff)); }',
  ].join('\n')
  assert.deepEqual(scanCss(nested, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

test('4-digit neutral hex in shadows and masks is not a palette literal', () => {
  const neutral = [
    '.a { box-shadow: 0 8px 24px #0000; }',
    '.b { box-shadow: 0 8px 24px #fff8; }',
    '.c { filter: drop-shadow(0 2px 4px #000c); }',
    '.d { -webkit-mask-image: linear-gradient(#fff0, #000c); }',
  ].join('\n')
  assert.deepEqual(scanCss(neutral, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

test('an SVG url fragment is not a hex literal', () => {
  const refs = [
    '.a { mask: url(#abc); }',
    '.b { -webkit-mask-image: url("#f00"); }',
    '.c { filter: url(#000); }',
  ].join('\n')
  assert.deepEqual(scanCss(refs, { file: 'desktop/src/ui/a.module.css' }).violations, [])
})

test('a baseline entry pointing at an exempt stylesheet is stale, not ignored', () => {
  const file = 'desktop/src/styles/tokens.css'
  const entry = { file, line: 2, value: '#ffffff', reason: 'palette file' }
  const result = scanCss('.a { color: #ffffff; }', { file, baseline: [entry] })
  assert.deepEqual(result.violations, [])
  assert.deepEqual(result.stale, [entry])
})
