/** The call-site key guard, GUI side: every `t('key')` / `translate(locale,
 * 'key')` literal in src must exist in the catalog. `t()` falls back to the
 * key itself, so a renamed or typo'd key passes every other test and still
 * ships the raw key string to a user — this is the one gate that reads the
 * call sites. The TUI catalog is pinned the same way (tests/test_i18n.py's
 * call-site guard). Dynamic keys (variables, template literals) are
 * invisible to the scan by design; the orphan report
 * (scripts/check_i18n.py) tracks what they can't see. */

import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { zh } from '../src/i18n/messages.ts'

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')

const CALL_SITE = /\bt\(\s*(['"])([a-zA-Z][\w.]*)\1/g
const TRANSLATE = /translate\(\s*\w+\s*,\s*(['"])([a-zA-Z][\w.]*)\1/g

function literalCallSites(): { keys: Map<string, string>; dynamic: number } {
  const keys = new Map<string, string>()
  let dynamic = 0
  const scan = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        scan(p)
        continue
      }
      if (!/\.(ts|tsx)$/.test(entry.name) || entry.name.endsWith('.gen.ts')) continue
      const text = fs.readFileSync(p, 'utf8')
      for (const m of text.matchAll(CALL_SITE)) {
        if (!keys.has(m[2])) keys.set(m[2], p)
      }
      for (const m of text.matchAll(TRANSLATE)) {
        if (!keys.has(m[2])) keys.set(m[2], p)
      }
      dynamic += [...text.matchAll(/\bt\(\s*[^'"`]/g)].length
    }
  }
  scan(SRC)
  return { keys, dynamic }
}

test('every literal call-site key exists in the catalog', () => {
  const { keys } = literalCallSites()
  const missing = [...keys.entries()].filter(([key]) => !(key in zh))
  assert.deepEqual(
    missing.map(([key, file]) => `${key} (${file.replace(SRC + path.sep, '')})`),
    [],
  )
})

test('the scan sees a real surface', () => {
  // A guard that scans nothing is a cap nobody enforces. If this floor
  // breaks, either the frontend shrank massively (adjust it) or the scan
  // broke (fix the scan).
  const { keys, dynamic } = literalCallSites()
  assert.ok(keys.size >= 200, `only ${keys.size} literal keys scanned`)
  assert.ok(dynamic >= 10, `only ${dynamic} dynamic call sites scanned`)
})
