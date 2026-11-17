/** The light label ladder must stay readable on the canvas it paints on.
 *
 * The palette is ported from the dsh platform, where the two quietest tiers sat
 * at 3.7:1 and 2.1:1 — below WCAG AA for the 11px CJK metadata that uses them,
 * and invisible at a glance because the dark side of the same tokens passes.
 * This test resolves the tokens the way the browser does and holds every tier
 * to the 4.5:1 floor, so a future palette edit cannot quietly undo it.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8')

const LIGHT_START = '/* ---- alias layer: light'
const DARK_START = 'body[data-ds-dark-theme]'

function lightSection(): string {
  const start = css.indexOf(LIGHT_START)
  const end = css.indexOf(DARK_START)
  assert.ok(start >= 0 && end > start, 'tokens.css keeps a light alias block')
  return css.slice(start, end)
}

/** Every `--name: value;` declaration in a slice, last one wins. */
function declarations(source: string): Map<string, string> {
  const map = new Map<string, string>()
  for (const match of source.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    map.set(match[1], match[2].trim())
  }
  return map
}

type Rgb = [number, number, number]

function resolve(name: string, light: Map<string, string>, all: Map<string, string>): string {
  let value = light.get(name) ?? all.get(name)
  assert.ok(value !== undefined, `token ${name} exists`)
  for (let hop = 0; hop < 6; hop += 1) {
    const reference = /^var\((--[\w-]+)\)$/.exec(value.trim())
    if (reference === null) return value.trim()
    const next = light.get(reference[1]) ?? all.get(reference[1])
    assert.ok(next !== undefined, `token ${reference[1]} resolves`)
    value = next
  }
  assert.fail(`token ${name} resolves within six hops`)
}

function rgb(value: string): Rgb {
  const parts = /^rgb\(\s*(\d+),\s*(\d+),\s*(\d+)\s*\)$/.exec(value)
  assert.ok(parts !== null, `expected an opaque rgb() colour, got ${value}`)
  return [Number(parts[1]), Number(parts[2]), Number(parts[3])]
}

function luminance([r, g, b]: Rgb): number {
  const channel = (raw: number): number => {
    const v = raw / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(foreground: Rgb, background: Rgb): number {
  const a = luminance(foreground)
  const b = luminance(background)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

test('every light label tier clears WCAG AA on the canvas', () => {
  const all = declarations(css)
  const light = declarations(lightSection())
  const canvas = rgb(resolve('--dsw-alias-bg-base', light, all))
  const tiers = [
    '--dsw-alias-label-primary',
    '--dsw-alias-label-secondary',
    '--dsw-alias-label-tertiary',
    '--dsw-alias-label-caption',
  ]
  const measured = tiers.map((tier) => {
    const value = resolve(tier, light, all)
    return { tier, value, ratio: contrast(rgb(value), canvas) }
  })
  for (const { tier, ratio } of measured) {
    assert.ok(ratio >= 4.5, `${tier} is ${ratio.toFixed(2)}:1, below the 4.5:1 floor`)
  }
  // The ladder stays monotone: a quieter tier may never read louder than the
  // one above it, or the hierarchy inverts for the reader.
  for (let index = 1; index < measured.length; index += 1) {
    assert.ok(
      measured[index].ratio < measured[index - 1].ratio,
      `${measured[index].tier} must sit below ${measured[index - 1].tier}`,
    )
  }
})

test('the quiet tiers stay quiet instead of collapsing into body text', () => {
  const all = declarations(css)
  const light = declarations(lightSection())
  const canvas = rgb(resolve('--dsw-alias-bg-base', light, all))
  const primary = contrast(rgb(resolve('--dsw-alias-label-primary', light, all)), canvas)
  const caption = contrast(rgb(resolve('--dsw-alias-label-caption', light, all)), canvas)
  // Room for hierarchy: the caption is quieter than the body but still legible.
  assert.ok(primary / caption >= 2.5, `primary/caption ratio is only ${(primary / caption).toFixed(2)}`)
})
