#!/usr/bin/env node
/**
 * Style-token guard for the desktop renderer.
 *
 * A component stylesheet (`desktop/src/**\/*.module.css`) must paint with
 * tokens: a raw colour literal hard-codes one palette into a surface, so a
 * contributed theme cannot move it. This scans the module stylesheets for
 * hex, rgb()/rgba() and hsl()/hsla() literals. The palette files
 * (`styles/tokens.css`, `styles/themes.css`, `styles/base.css`) are exactly
 * where literals belong and are exempt.
 *
 * Deliberately not violations (the value carries no palette):
 *  - a literal inside `var(...)` — it is only the fallback of a token;
 *  - black/white inside `box-shadow` / `text-shadow` / `filter` — a shadow
 *    only offsets darkness;
 *  - black/white inside a mask — a mask uses the stencil's alpha, not its
 *    colour.
 *
 * Known existing violations go into `BASELINE`, each pinned to file, line and
 * value with the reason. They are printed as skipped on every run — the guard
 * never passes silently — and an entry that no longer matches fails the run so
 * the list cannot rot.
 *
 * Usage: `pnpm lint:tokens` (from desktop/) or `node scripts/check-style-tokens.mjs`.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const REPO_ROOT = resolve(here, '..')
export const SCAN_ROOT = join(REPO_ROOT, 'desktop', 'src')

/** Stylesheets that own the palette itself; literals are the point there. */
export const EXEMPT_FILES = new Set(['tokens.css', 'themes.css', 'base.css'])

/**
 * Known violations accepted for now, each with the decision it answers to.
 * Keyed by `file` (repo-relative), `line` and the declaration `value`.
 * Empty: the baselined batch was fixed with existing tokens instead.
 * @type {{file: string, line: number, value: string, reason: string}[]}
 */
export const BASELINE = []

/** Raw colour literals the guard forbids. */
const RAW_COLOR = /#[\da-f]{3,8}\b|\b(?:rgba?|hsla?)\s*\(/i

/** A colon-free rule body, then the declarations inside it. */
const RULE = /([^{}]+)\{([^{}]*)\}/g
const DECLARATION = /([-\w]+)\s*:\s*([^;{}]+)/g

const SHADOW_PROPERTIES = /^(?:box-shadow|text-shadow|filter)$/
const MASK_PROPERTIES = /^(?:mask|mask-image|-webkit-mask|-webkit-mask-image)$/

/** Black or white, with or without alpha: no hue, so no palette. */
const PURE_NEUTRAL =
  /#(?:0{3}|f{3}|0{6}|f{6}|0{6}[\da-f]{2}|f{6}[\da-f]{2})\b|\b(?:rgba?|hsla?)\(\s*(?:0\s*[, ]\s*0\s*[, ]\s*0|255\s*[, ]\s*255\s*[, ]\s*255)\b[^)]*\)|\b(?:black|white)\b/gi

function normalizePath(file) {
  return file.split(sep).join('/')
}

/** True for the palette stylesheets (by basename, wherever they are). */
export function isExemptFile(file) {
  const base = normalizePath(file).split('/').at(-1)
  return base !== undefined && EXEMPT_FILES.has(base)
}

/** Drop `var(...)` wrappers, including a token's fallback literal. */
function stripVars(value) {
  let out = value
  let previous
  do {
    previous = out
    out = out.replace(/var\([^()]*\)/g, ' ')
  } while (out !== previous)
  return out
}

function hasRawColor(property, value) {
  let rest = stripVars(value)
  if (SHADOW_PROPERTIES.test(property) || MASK_PROPERTIES.test(property)) {
    rest = rest.replace(PURE_NEUTRAL, ' ')
  }
  return RAW_COLOR.test(rest)
}

/**
 * Report raw colour literals in one stylesheet.
 *
 * @param {string} source css text
 * @param {{file?: string, baseline?: {file: string, line: number, value: string, reason: string}[]}} options
 * @returns {{violations: {line: number, property: string, value: string}[],
 *   skipped: {line: number, property: string, value: string, reason: string}[],
 *   stale: {file: string, line: number, value: string, reason: string}[]}}
 */
export function scanCss(source, { file = '', baseline = [] } = {}) {
  const result = { violations: [], skipped: [], stale: [] }
  if (isExemptFile(file)) return result

  const wanted = normalizePath(file)
  const entries = baseline.filter((entry) => normalizePath(entry.file) === wanted)
  const matched = new Set()
  // Keep newlines so diagnostics carry the source line number.
  const css = source.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))

  for (const rule of css.matchAll(RULE)) {
    for (const declaration of rule[2].matchAll(DECLARATION)) {
      const [, property, raw] = declaration
      const value = raw.trim().replace(/\s+/g, ' ')
      if (!hasRawColor(property, value)) continue
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
    const result = scanCss(readFileSync(absolute, 'utf8'), { file, baseline: BASELINE })
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
    console.error('\nRemove or re-point these entries in scripts/check-style-tokens.mjs BASELINE.')
    process.exit(1)
  }
  if (violations.length > 0) {
    console.error('Style token violations:')
    for (const entry of violations) {
      console.error(
        `  ${entry.file}:${entry.line} raw ${entry.property} value "${entry.value}" — use a colour token`,
      )
    }
    console.error(`\n${violations.length} violation(s). Paint with the tokens in desktop/src/styles/tokens.css.`)
    process.exit(1)
  }
  console.log(
    `style tokens OK — ${files.length} module stylesheets, ${skipped.length} baselined, 0 violations`,
  )
}

const invokedDirectly =
  process.argv[1] !== undefined && pathToFileURL(resolve(process.argv[1])).href === import.meta.url
if (invokedDirectly) main()
