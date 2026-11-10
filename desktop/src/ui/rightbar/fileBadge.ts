/** File-type accents for the work-panel tree, VSCode-ish: a short badge with
 * a static palette tone. Static tones are deliberate — a file type keeps its
 * colour across light and dark, like an editor icon theme. Unknown types fall
 * back to the generic file icon (null). */

export type BadgeTone = 'blue' | 'amber' | 'green' | 'red' | 'neutral'

export interface FileBadge {
  label: string
  tone: BadgeTone
}

const BY_EXT: Record<string, FileBadge> = {
  ts: { label: 'TS', tone: 'blue' },
  tsx: { label: 'TS', tone: 'blue' },
  js: { label: 'JS', tone: 'amber' },
  jsx: { label: 'JS', tone: 'amber' },
  mjs: { label: 'JS', tone: 'amber' },
  cjs: { label: 'JS', tone: 'amber' },
  json: { label: '{}', tone: 'amber' },
  jsonc: { label: '{}', tone: 'amber' },
  jsonl: { label: '{}', tone: 'amber' },
  py: { label: 'PY', tone: 'blue' },
  pyi: { label: 'PY', tone: 'blue' },
  rs: { label: 'RS', tone: 'amber' },
  go: { label: 'GO', tone: 'blue' },
  rb: { label: 'RB', tone: 'red' },
  java: { label: 'JV', tone: 'red' },
  kt: { label: 'KT', tone: 'red' },
  kts: { label: 'KT', tone: 'red' },
  swift: { label: 'SW', tone: 'amber' },
  c: { label: 'C', tone: 'blue' },
  h: { label: 'H', tone: 'blue' },
  cpp: { label: 'C+', tone: 'blue' },
  hpp: { label: 'C+', tone: 'blue' },
  cs: { label: 'C#', tone: 'green' },
  php: { label: 'PH', tone: 'red' },
  css: { label: '#', tone: 'blue' },
  scss: { label: '#', tone: 'blue' },
  less: { label: '#', tone: 'blue' },
  html: { label: '<>', tone: 'red' },
  htm: { label: '<>', tone: 'red' },
  vue: { label: 'V', tone: 'green' },
  svelte: { label: 'S', tone: 'red' },
  md: { label: 'MD', tone: 'blue' },
  mdx: { label: 'MD', tone: 'blue' },
  txt: { label: 'TX', tone: 'neutral' },
  log: { label: 'TX', tone: 'neutral' },
  sh: { label: '>_', tone: 'green' },
  zsh: { label: '>_', tone: 'green' },
  bash: { label: '>_', tone: 'green' },
  yml: { label: 'YL', tone: 'red' },
  yaml: { label: 'YL', tone: 'red' },
  toml: { label: 'TM', tone: 'neutral' },
  ini: { label: 'CF', tone: 'neutral' },
  cfg: { label: 'CF', tone: 'neutral' },
  conf: { label: 'CF', tone: 'neutral' },
  env: { label: 'EN', tone: 'amber' },
  lock: { label: 'LK', tone: 'neutral' },
  sql: { label: 'SQ', tone: 'amber' },
  xml: { label: '<>', tone: 'blue' },
  svg: { label: 'SV', tone: 'green' },
  png: { label: 'IM', tone: 'green' },
  jpg: { label: 'IM', tone: 'green' },
  jpeg: { label: 'IM', tone: 'green' },
  gif: { label: 'IM', tone: 'green' },
  webp: { label: 'IM', tone: 'green' },
  ico: { label: 'IM', tone: 'green' },
  pdf: { label: 'PD', tone: 'red' },
  csv: { label: 'CS', tone: 'green' },
  tsv: { label: 'CS', tone: 'green' },
  ipynb: { label: 'IP', tone: 'amber' },
}

const BY_NAME: Record<string, FileBadge> = {
  Dockerfile: { label: 'DK', tone: 'blue' },
  Makefile: { label: 'MK', tone: 'neutral' },
}

/** The badge a file path earns, or null for the generic file icon. */
export function fileBadge(path: string): FileBadge | null {
  const base = path.replaceAll('\\', '/').split('/').pop() ?? ''
  const named = BY_NAME[base]
  if (named !== undefined) return named
  const dot = base.lastIndexOf('.')
  if (dot <= 0 || dot === base.length - 1) return null
  return BY_EXT[base.slice(dot + 1).toLowerCase()] ?? null
}
