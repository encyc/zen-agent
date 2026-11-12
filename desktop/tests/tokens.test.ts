import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8')

test('the parity layer carries the pi font stack, ramp, radii and the 760px band', () => {
  assert.match(
    css,
    /--ds-font-family-sans:\s*-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;/,
  )
  assert.match(css, /--ds-font-scale:\s*1;/)
  for (const size of ['10-5', '11', '11-5', '12', '12-5', '13', '13-5', '14', '15', '16', '18', '20', '28']) {
    assert.match(css, new RegExp(`--ds-font-${size}:\\s*calc\\(`))
  }
  for (const radius of ['4', '6', '8', '10', '12', '14', '16', '18', '20', '24']) {
    assert.match(css, new RegExp(`--ds-radius-${radius}:\\s*${radius}px;`))
  }
  assert.match(css, /--ds-radius-full:\s*999px;/)
  assert.match(css, /--ds-chat-content-width:\s*760px;/)
  assert.match(css, /--ds-toolbar-height:\s*46px;/)
  assert.match(css, /--ds-topbar-height:\s*var\(--ds-toolbar-height\);/)
  assert.match(css, /--ds-sidebar-footer-height:\s*40px;/)
  assert.match(css, /--ds-chat-composer-width:\s*768px;/)
  assert.match(css, /--ds-composer-radius:\s*20px;/)
  assert.match(css, /--ds-shadow-composer:/)
  assert.match(css, /--dsh-chat-content-width:\s*var\(--ds-chat-content-width\);/)
  assert.match(css, /--dsh-composer-card-max-width:\s*var\(--ds-chat-content-width\);/)
  assert.match(css, /--dsw-radius-xl:\s*var\(--ds-radius-20\);/)
})

test('the existing text stack is re-pointed at the parity stack', () => {
  assert.match(css, /--dsh-font-family-text-default:\s*var\(--ds-font-family-sans\);/)
})

test('accent aliases stay monochrome and primary buttons invert safely', () => {
  assert.match(css, /--ds-accent:\s*var\(--dsw-static-neutral-bluish-1000\);/)
  assert.match(css, /--ds-accent-hover:\s*var\(--dsw-static-neutral-bluish-800\);/)
  assert.match(css, /--dsw-alias-state-business-primary:\s*var\(--ds-accent\);/)
  assert.match(css, /--dsw-alias-button-info-fill:\s*var\(--ds-accent\);/)
  assert.match(css, /--dsw-alias-button-info-hover:\s*var\(--ds-accent-hover\);/)
  // The primary send button belongs to the shared plate, so every mode
  // inherits the same inverted foreground.
  const plate = readFileSync(
    new URL('../src/ui/composer/ComposerPlate.module.css', import.meta.url),
    'utf8',
  )
  const boundary = readFileSync(new URL('../src/ErrorBoundary.tsx', import.meta.url), 'utf8')
  assert.match(plate, /\.primary\s*\{[^}]*color:\s*var\(--dsw-alias-label-primary-foreground\)/)
  assert.match(boundary, /color:\s*'var\(--dsw-alias-label-primary-foreground\)'/)
})
