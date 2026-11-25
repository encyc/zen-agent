/** The motion ladder: one scale of durations and easings, and the pre-ladder
 * names aliased onto it from the same block that defines it. */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8')

/** The block that CONTAINS `marker`. tokens.css has five `body {` blocks, so
 * locating by selector would grab the first one, which carries none of these. */
function blockWith(marker: string): string {
  const at = css.indexOf(marker)
  assert.ok(at >= 0, `tokens.css declares ${marker}`)
  const start = css.lastIndexOf('{', at)
  return css.slice(start, css.indexOf('}', at))
}

test('the motion ladder is one scale with three rungs', () => {
  const ladder = blockWith('--ds-motion-fast:')
  for (const [token, value] of [
    ['--ds-motion-fast:', '140ms'],
    ['--ds-motion:', '200ms'],
    ['--ds-motion-slow:', '320ms'],
  ] as const) {
    assert.match(ladder, new RegExp(`${token}\\s*${value};`), token)
  }
  assert.match(ladder, /--ds-ease-out:\s*cubic-bezier\(0\.22, 1, 0\.36, 1\);/)
})

test('the legacy names alias the ladder in the SAME block that defines it', () => {
  // var(--ds-motion-fast) resolves to nothing on `:root` (the token is defined
  // on `body`), and an unresolvable var() in a transition invalidates the whole
  // declaration — silently. The alias has to live where its target is visible.
  const ladder = blockWith('--ds-motion-fast:')
  assert.match(ladder, /--ds-transition-duration-fast:\s*var\(--ds-motion-fast\);/)
  assert.match(ladder, /--ds-transition-duration:\s*var\(--ds-motion\);/)
  assert.match(ladder, /--ds-transition-duration-slow:\s*var\(--ds-motion-slow\);/)
  assert.match(ladder, /--ds-ease-in-out:\s*var\(--ds-ease-out\);/)
  const root = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')))
  assert.doesNotMatch(root, /--ds-transition-duration|--ds-ease-in-out/)
})

test('every alias resolves to a token that exists with a literal value', () => {
  for (const [, target] of css.matchAll(/--ds-transition-duration[\w-]*:\s*var\((--[\w-]+)\);/g)) {
    assert.match(css, new RegExp(`${target}:\\s*\\d`), `${target} is defined with a literal`)
  }
})

test('the loop rungs exist and the ladder has no fourth duration', () => {
  const ladder = blockWith('--ds-motion-fast:')
  // The spinners and shimmers run for seconds, not for a UI beat; they get
  // their own two rungs rather than stretching the interaction scale.
  assert.match(ladder, /--ds-motion-loop:\s*1100ms;/)
  assert.match(ladder, /--ds-motion-loop-slow:\s*2600ms;/)
  assert.deepEqual(
    [...ladder.matchAll(/--ds-motion[\w-]*:/g)].map((match) => match[0].slice(0, -1)).sort(),
    [
      '--ds-motion',
      '--ds-motion-fast',
      '--ds-motion-loop',
      '--ds-motion-loop-slow',
      '--ds-motion-delay-tooltip',
      '--ds-motion-slow',
    ].sort(),
  )
})
