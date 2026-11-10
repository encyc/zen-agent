/** Lazy Shiki highlighter for the work-panel file viewer, mirroring the
 * transcript highlighter's structure: core + JavaScript regex engine (no wasm
 * asset), grammars imported per sighting, and a subscription so the viewer
 * repaints from its plain-text fallback once a grammar is ready. A static file
 * is tokenized in one pass; the source is only highlighted within the same
 * bounds the transcript uses, so a huge file cannot stall the paint. */

import { useSyncExternalStore } from 'react'
import type { HighlighterCore, LanguageInput, ThemedToken } from 'shiki/core'
import { createHighlighterCore } from 'shiki/core'
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript'

export type { ThemedToken } from 'shiki/core'

export const THEMES = { light: 'one-light', dark: 'one-dark-pro' } as const
export type ThemeMode = keyof typeof THEMES

export const MAX_HIGHLIGHT_CODE_UNITS = 100_000
export const MAX_HIGHLIGHT_LINES = 800
export const MAX_HIGHLIGHT_LINE_CODE_UNITS = 2_000

type LanguageDefinition = {
  aliases?: readonly string[]
  load: () => LanguageInput
}

// Explicit imports keep Vite from emitting Shiki's entire grammar catalog.
const LANGUAGE_DEFINITIONS = {
  astro: { load: () => import('shiki/langs/astro.mjs') },
  bat: { aliases: ['batch'], load: () => import('shiki/langs/bat.mjs') },
  c: { load: () => import('shiki/langs/c.mjs') },
  cpp: { aliases: ['c++'], load: () => import('shiki/langs/cpp.mjs') },
  csharp: { aliases: ['c#', 'cs'], load: () => import('shiki/langs/csharp.mjs') },
  css: { load: () => import('shiki/langs/css.mjs') },
  dart: { load: () => import('shiki/langs/dart.mjs') },
  diff: { load: () => import('shiki/langs/diff.mjs') },
  docker: { aliases: ['dockerfile'], load: () => import('shiki/langs/docker.mjs') },
  dotenv: { load: () => import('shiki/langs/dotenv.mjs') },
  go: { load: () => import('shiki/langs/go.mjs') },
  graphql: { aliases: ['gql'], load: () => import('shiki/langs/graphql.mjs') },
  groovy: { load: () => import('shiki/langs/groovy.mjs') },
  hcl: { load: () => import('shiki/langs/hcl.mjs') },
  html: { load: () => import('shiki/langs/html.mjs') },
  ini: { aliases: ['properties'], load: () => import('shiki/langs/ini.mjs') },
  java: { load: () => import('shiki/langs/java.mjs') },
  javascript: {
    aliases: ['js', 'cjs', 'mjs'],
    load: () => import('shiki/langs/javascript.mjs'),
  },
  json: { load: () => import('shiki/langs/json.mjs') },
  jsonc: { load: () => import('shiki/langs/jsonc.mjs') },
  jsonl: { load: () => import('shiki/langs/jsonl.mjs') },
  jsx: { load: () => import('shiki/langs/jsx.mjs') },
  kotlin: { aliases: ['kt', 'kts'], load: () => import('shiki/langs/kotlin.mjs') },
  lua: { load: () => import('shiki/langs/lua.mjs') },
  make: { aliases: ['makefile'], load: () => import('shiki/langs/make.mjs') },
  markdown: { aliases: ['md'], load: () => import('shiki/langs/markdown.mjs') },
  mdx: { load: () => import('shiki/langs/mdx.mjs') },
  mermaid: { aliases: ['mmd'], load: () => import('shiki/langs/mermaid.mjs') },
  nginx: { load: () => import('shiki/langs/nginx.mjs') },
  php: { load: () => import('shiki/langs/php.mjs') },
  powershell: { aliases: ['ps', 'ps1'], load: () => import('shiki/langs/powershell.mjs') },
  prisma: { load: () => import('shiki/langs/prisma.mjs') },
  proto: { aliases: ['protobuf'], load: () => import('shiki/langs/proto.mjs') },
  python: { aliases: ['py'], load: () => import('shiki/langs/python.mjs') },
  ruby: { aliases: ['rb'], load: () => import('shiki/langs/ruby.mjs') },
  rust: { aliases: ['rs'], load: () => import('shiki/langs/rust.mjs') },
  scala: { load: () => import('shiki/langs/scala.mjs') },
  shellscript: {
    aliases: ['bash', 'sh', 'shell', 'zsh'],
    load: () => import('shiki/langs/shellscript.mjs'),
  },
  sql: { load: () => import('shiki/langs/sql.mjs') },
  svelte: { load: () => import('shiki/langs/svelte.mjs') },
  swift: { load: () => import('shiki/langs/swift.mjs') },
  terraform: { aliases: ['tf', 'tfvars'], load: () => import('shiki/langs/terraform.mjs') },
  toml: { load: () => import('shiki/langs/toml.mjs') },
  tsx: { load: () => import('shiki/langs/tsx.mjs') },
  typescript: {
    aliases: ['ts', 'cts', 'mts'],
    load: () => import('shiki/langs/typescript.mjs'),
  },
  vue: { load: () => import('shiki/langs/vue.mjs') },
  xml: { load: () => import('shiki/langs/xml.mjs') },
  yaml: { aliases: ['yml'], load: () => import('shiki/langs/yaml.mjs') },
} as const satisfies Record<string, LanguageDefinition>

type SupportedLanguage = keyof typeof LANGUAGE_DEFINITIONS

const languageIds = new Map<string, SupportedLanguage>()
for (const id of Object.keys(LANGUAGE_DEFINITIONS) as SupportedLanguage[]) {
  languageIds.set(id, id)
  const { aliases } = LANGUAGE_DEFINITIONS[id] as LanguageDefinition
  for (const alias of aliases ?? []) {
    languageIds.set(alias, id)
  }
}

const EXT_LANG: Record<string, string> = {
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  mjs: 'javascript',
  cjs: 'javascript',
  json: 'json',
  css: 'css',
  html: 'html',
  md: 'markdown',
  rs: 'rust',
  py: 'python',
  go: 'go',
  sh: 'shellscript',
  zsh: 'shellscript',
  bash: 'shellscript',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'toml',
  sql: 'sql',
  swift: 'swift',
  kt: 'kotlin',
  java: 'java',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  hpp: 'cpp',
}

const PLAIN_LANGS = new Set(['', 'text', 'txt', 'plain', 'plaintext', 'ansi'])

/** The grammar a fence tag or language id names, or null for plain text. */
export function resolveLang(lang: string): SupportedLanguage | null {
  const id = lang.trim().toLowerCase()
  if (PLAIN_LANGS.has(id)) return null
  return languageIds.get(id) ?? null
}

/** The grammar a file path selects: extension first, then a bare alias such
 * as `Makefile`. Unknown extensions stay plain text. */
export function langForPath(path: string): SupportedLanguage | null {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return resolveLang(EXT_LANG[ext] ?? ext)
}

let highlighter: HighlighterCore | null = null
let creating: Promise<HighlighterCore> | null = null
let version = 0

const readyLangs = new Set<SupportedLanguage>()
const pendingLangs = new Set<SupportedLanguage>()
const failedLangs = new Set<SupportedLanguage>()
const listeners = new Set<() => void>()

function notify(): void {
  version += 1
  for (const listener of listeners) listener()
}

export function subscribeHighlighter(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getHighlightVersion(): number {
  return version
}

/** Repaint on grammar readiness — snapshot for useSyncExternalStore. */
export function useHighlightVersion(): number {
  return useSyncExternalStore(subscribeHighlighter, getHighlightVersion, getHighlightVersion)
}

function getHighlighterInstance(): Promise<HighlighterCore> {
  creating ??= createHighlighterCore({
    themes: [import('shiki/themes/one-light.mjs'), import('shiki/themes/one-dark-pro.mjs')],
    langs: [],
    engine: createJavaScriptRegexEngine({ forgiving: true }),
  }).then((instance) => {
    highlighter = instance
    return instance
  })
  return creating
}

/** Kick off lazy loading for a grammar; safe to call every render. */
export function ensureLang(lang: string): void {
  const resolved = resolveLang(lang)
  if (
    !resolved ||
    readyLangs.has(resolved) ||
    pendingLangs.has(resolved) ||
    failedLangs.has(resolved)
  ) {
    return
  }
  pendingLangs.add(resolved)
  void getHighlighterInstance()
    .then((instance) => instance.loadLanguage(LANGUAGE_DEFINITIONS[resolved].load()))
    .then(() => {
      readyLangs.add(resolved)
    })
    .catch(() => {
      // The grammar failed to load: settle on the plain-text fallback.
      failedLangs.add(resolved)
    })
    .finally(() => {
      pendingLangs.delete(resolved)
      notify()
    })
}

/** Cheap raw-source guard; a huge file stays plain instead of stalling. */
function withinHighlightLimits(source: string): boolean {
  if (source.length > MAX_HIGHLIGHT_CODE_UNITS) return false
  let lineCount = 1
  let lineLength = 0
  for (let index = 0; index < source.length; index += 1) {
    if (source.charCodeAt(index) === 0x0a) {
      lineCount += 1
      if (lineCount > MAX_HIGHLIGHT_LINES) return false
      lineLength = 0
    } else {
      lineLength += 1
      if (lineLength > MAX_HIGHLIGHT_LINE_CODE_UNITS) return false
    }
  }
  return true
}

/** One token row per source line, or null while the grammar is not ready. */
export function highlightLines(
  code: string,
  lang: SupportedLanguage,
  mode: ThemeMode,
): ThemedToken[][] | null {
  if (highlighter === null || !readyLangs.has(lang) || !withinHighlightLimits(code)) {
    return null
  }
  try {
    return highlighter.codeToTokensBase(code, { lang, theme: THEMES[mode] })
  } catch {
    return null
  }
}

/** The shell writes the palette onto body attributes; observe them so mode,
 * palette and system flips all repaint alike (the terminal's contract). */
export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(
    (listener) => {
      const observer = new MutationObserver(listener)
      observer.observe(document.body, {
        attributes: true,
        attributeFilter: ['data-ds-dark-theme', 'data-ds-theme'],
      })
      return () => observer.disconnect()
    },
    () => (document.body.hasAttribute('data-ds-dark-theme') ? 'dark' : 'light'),
    () => 'light',
  )
}
