/** The motion guard's matcher: a raw duration or a bare easing in a component
 * stylesheet is reported, token-driven declarations pass, and a looping
 * animation may keep its timing keyword while its duration must still be a
 * token. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
// @ts-expect-error -- plain-node guard script, no type declarations
import { scanMotionCss } from '../../scripts/check-style-motion.mjs'

const file = 'desktop/src/ui/a.module.css'

test('a raw duration or a bare easing keyword is a violation', () => {
  const css = '.a { transition: opacity 120ms ease; }'
  assert.deepEqual(
    scanMotionCss(css, { file }).violations.map((entry) => entry.value),
    ['opacity 120ms ease'],
  )
})

test('token-driven declarations pass', () => {
  const css = '.a { transition: opacity var(--ds-motion-fast) var(--ds-ease-out); }'
  assert.deepEqual(scanMotionCss(css, { file }).violations, [])
})

test('a looping animation may keep its easing keyword but not a raw duration', () => {
  const raw = '.a { animation: spin 1.1s linear infinite; }'
  const violations = scanMotionCss(raw, { file }).violations
  assert.deepEqual(violations.map((entry) => entry.property), ['animation'])
  assert.deepEqual(violations.map((entry) => entry.value), ['spin 1.1s linear infinite'])

  const fine = '.a { animation: spin var(--ds-motion-loop) linear infinite; }'
  assert.deepEqual(scanMotionCss(fine, { file }).violations, [])
})

test('the palette stylesheets are exempt and a zero duration is allowed', () => {
  assert.deepEqual(
    scanMotionCss('.a { transition: opacity 120ms ease; }', { file: 'desktop/src/styles/tokens.css' })
      .violations,
    [],
  )
  assert.deepEqual(scanMotionCss('.a { transition: opacity 0s; }', { file }).violations, [])
})

test('a multi-declaration block reports each bad line once, with its line number', () => {
  const css = ['.a {', '  transition: opacity 120ms ease;', '  color: red;', '  animation: spin 2.6s linear infinite;', '}'].join('\n')
  assert.deepEqual(
    scanMotionCss(css, { file }).violations.map((entry) => [entry.line, entry.property]),
    [
      [2, 'transition'],
      [4, 'animation'],
    ],
  )
})
