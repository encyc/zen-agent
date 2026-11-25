#!/usr/bin/env node
/**
 * Motion-token guard for the desktop renderer.
 *
 * The style-token guard forbids raw colours in a component stylesheet because a
 * literal hard-codes one palette into a surface. The same argument holds for
 * time: `transition: opacity 120ms ease` hard-codes one beat, so a change to the
 * motion ladder cannot reach it, and every component invents its own feel. This
 * scans the `transition` / `animation` declarations in the module stylesheets
 * and reports a raw duration or a bare easing.
 *
 * Deliberately not violations:
 *  - a token inside `var(...)` — that is the point of the guard;
 *  - a zero duration (`0s` / `0ms`) — an explicitly instant change is a
 *    decision, not a drift;
 *  - the timing keyword of an `animation` — a looping spinner's rhythm is
 *    expressed lexically (`linear`, `ease-in-out`, `steps(...)`) and has no
 *    counterpart on the interaction ladder. Its DURATION is still checked.
 *  - the palette stylesheets (`styles/tokens.css`, `themes.css`, `base.css`),
 *    which own the ladder itself and must write literals.
 *
 * Known violations go into `BASELINE` pinned to file/line/value with a reason,
 * printed as skipped on every run; an entry that stops matching fails the run so
 * the list cannot rot — the same contract as the colour guard.
 *
 * Usage: `pnpm lint:motion` (from desktop/) or `node scripts/check-style-motion.mjs`.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const REPO_ROOT = resolve(here, '..')
export const SCAN_ROOT = join(REPO_ROOT, 'desktop', 'src')

/** The stylesheets that define the ladder; literals are the point there. */
export const EXEMPT_FILES = new Set(['tokens.css', 'themes.css', 'base.css'])

/** Known violations accepted for now, each with the decision it answers to. */
export const BASELINE = []

/** A duration literal: `120ms`, `1.1s`, `.5s`. */
const RAW_TIME = /(?:^|[\s,(])(?:\d*\.)?\d+m?s\b/i

/** An easing function written inline: `ease`, `ease-in-out`, `cubic-bezier(…)`,
 * `steps(…)`. `linear` is matched here too and then exempted for `animation`. */
const RAW_EASING = /\b(?:ease(?:-in-out|-in|-out)?|linear|cubic-bezier|steps)\b/i

const ZERO_TIME = /(?:^|[\s,(])0(?:\.0+)?m?s\b/gi

/** `transition`, `transition-*`, `animation`, `animation-duration`. */
const MOTION_PROPERTY = /^(?:transition|animation)(?:-[\w-]+)?$/

/** A colon-free rule body, then the declarations inside it. */
const RULE = /([^{}]+)\{([^{}]*)\}/g
const DECLARATION = /([-\w]+)\s*:\s*([^;{}]+)/g

/** Drop every `fn(…)` occurrence, so a var() reference and a steps() call stop
 * contributing their inner text to the raw-value scan. */
function stripCalls(value, name) {
  let rest = value
  for (;;) {
    const start = rest.toLowerCase().indexOf(`${name.toLowerCase()}(`)
    if (start < 0) return rest
    let index = start + name.length + 1
    let depth = 1
    for (; index < rest.length; index += 1) {
      const char = rest[index]
      if (char === '(') {
        depth += 1
      } else if (char === ')') {
        depth -= 1
        if (depth === 0) {
          index += 1
          break
        }
      }
    }
    rest = rest.slice(0, start) + ' ' + rest.slice(index)
  }
}

export function isExemptFile(file) {
  const base = file.split('/').pop() ?? file
  return file.split(/[\\/]/).includes('styles') && EXEMPT_FILES.has(base)
}

function normalizePath(file) {
  return file.split(sep).join('/')
}

/** True when a declaration value drifts from the ladder. */
export function hasRawMotion(property, value) {
  const withoutVars = stripCalls(value, 'var').replace(ZERO_TIME, ' ')
  if (RAW_TIME.test(withoutVars)) return true
  // A looping animation's timing keyword is its rhythm, not a UI beat. Its
  // duration was already checked above.
  if (property.startsWith('animation')) return false
  const withoutSteps = stripCalls(withoutVars, 'steps')
  return RAW_EASING.test(withoutSteps)
}

/**
 * Report raw motion literals in one stylesheet.
 *
 * @param {string} source css text
 * @param {{file?: string, baseline?: {file: string, line: number, value: string, reason: string}[]}} options
 * @returns {{violations: {line: number, property: string, value: string}[],
 *   skipped: {line: number, property: string, value: string, reason: string}[],
 *   stale: {file: string, line: number, value: string, reason: string}[]}}
 */
export function scanMotionCss(source, { file = '', baseline = [] } = {}) {
  const result = { violations: [], skipped: [], stale: [] }
  const wanted = normalizePath(file)
  const entries = baseline.filter((entry) => normalizePath(entry.file) === wanted)
  if (isExemptFile(file)) {
    // An exempt stylesheet can never carry a violation, so an entry pointing at
    // one is stale: report it instead of ignoring it behind the return.
    result.stale = entries
    return result
  }

  const matched = new Set()
  // Keep newlines so diagnostics carry the source line number.
  const css = source.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))

  for (const rule of css.matchAll(RULE)) {
    for (const declaration of rule[2].matchAll(DECLARATION)) {
      const [, property, raw] = declaration
      if (!MOTION_PROPERTY.test(property)) continue
      const value = raw.trim().replace(/\s+/g, ' ')
      if (!hasRawMotion(property, value)) continue
      const offset = rule.index + rule[1].length + 1 + declaration.index
      const line = css.slice(0, offset).split('\n').length
      const known = entries.find(
        (entry) => entry.line === line && entry.value === value && !matched.has(entry),
      )
      if (known !== undefined) {
        matched.add(known)
        result.skipped.push({ line, property, value, reason: known.reason })
      } else {
        result.violations.push({ line, property, value })
      }
    }
  }
  result.stale = entries.filter((entry) => !matched.has(entry))
  return result
}

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .flatMap((entry) => {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) return walk(path)
      return entry.name.endsWith('.module.css') ? [path] : []
    })
    .sort()
}

function main() {
  const files = walk(SCAN_ROOT)
  const violations = []
  const skipped = []
  const stale = []
  for (const absolute of files) {
    const file = normalizePath(relative(REPO_ROOT, absolute))
    const result = scanMotionCss(readFileSync(absolute, 'utf8'), { file, baseline: BASELINE })
    violations.push(...result.violations.map((entry) => ({ file, ...entry })))
    skipped.push(...result.skipped.map((entry) => ({ file, ...entry })))
    stale.push(...result.stale)
  }

  if (skipped.length > 0) {
    console.log('Baseline violations skipped:')
    for (const entry of skipped) {
      console.log(`  ${entry.file}:${entry.line} ${entry.property}: ${entry.value} — ${entry.reason}`)
    }
  }
  if (stale.length > 0) {
    console.error('Stale baseline entries (the violation is fixed or the line moved):')
    for (const entry of stale) {
      console.error(`  ${entry.file}:${entry.line} ${entry.value} — ${entry.reason}`)
    }
    console.error('\nRemove or re-point these entries in scripts/check-style-motion.mjs BASELINE.')
    process.exit(1)
  }
  if (violations.length > 0) {
    console.error('Motion token violations:')
    for (const entry of violations) {
      console.error(
        `  ${entry.file}:${entry.line} raw ${entry.property} value "${entry.value}" — use a duration/easing token`,
      )
    }
    console.error(
      `\n${violations.length} violation(s). Use the ladder in desktop/src/styles/tokens.css (--ds-motion*, --ds-ease-*).`,
    )
    process.exit(1)
  }
  console.log(
    `style motion OK — ${files.length} module stylesheets, ${skipped.length} baselined, 0 violations`,
  )
}

const invokedDirectly =
  process.argv[1] !== undefined && pathToFileURL(resolve(process.argv[1])).href === import.meta.url
if (invokedDirectly) main()
