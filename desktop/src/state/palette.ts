import type { CommandEntry, SessionSummary } from '../rpc/types'

// Same ordering as sessionGroups.compareSessions: updated desc, path tie-break.
// Kept inline because the native Node TS test runner cannot resolve a runtime
// import between source modules (extensionless) and tsconfig forbids `.ts`.
function newerFirst(a: SessionSummary, b: SessionSummary): number {
  const byUpdated = (b.updated_at ?? 0) - (a.updated_at ?? 0)
  return byUpdated !== 0 ? byUpdated : a.path.localeCompare(b.path)
}

export type PaletteCommandMode = 'run' | 'insert'

/** Session hits rendered at once: a long history must not mount a list of
 * every session before the user narrows the query. */
export const PALETTE_SESSION_LIMIT = 50

/** Zero-argument safe commands run; argument commands insert their prefix. */
export const COMMAND_POLICY: Record<string, PaletteCommandMode> = {
  compact: 'run',
  undo: 'run',
  retry: 'run',
  new: 'run',
  fork: 'run',
  context: 'run',
  tools: 'run',
  doctor: 'run',
  jobs: 'run',
  dirs: 'run',
  hotkeys: 'run',
  rename: 'insert',
  pin: 'insert',
  export: 'insert',
  image: 'insert',
  search: 'insert',
  open: 'insert',
  urls: 'insert',
  copy: 'insert',
  btw: 'insert',
  'add-dir': 'insert',
  'remove-dir': 'insert',
  handoff: 'insert',
  trace: 'insert',
}

export interface PaletteSessionHit {
  session: SessionSummary
  disabled: boolean
}

export interface PaletteCommandHit {
  name: string
  description: string
  mode: PaletteCommandMode
}

export interface PaletteResults {
  sessions: PaletteSessionHit[]
  commands: PaletteCommandHit[]
}

export function paletteResults(
  sessions: SessionSummary[],
  commands: CommandEntry[],
  query: string,
  currentPath: string | null,
): PaletteResults {
  const needle = query.trim().toLowerCase()
  const sessionHits = sessions
    .filter((session) =>
      [session.title, session.name, session.project].some((value) =>
        (value ?? '').toLowerCase().includes(needle),
      ),
    )
    .sort(newerFirst)
    .map((session) => ({
      session,
      // A recorded project root can be opened by restarting the runtime
      // there; sessions without one stay discovery-only.
      disabled: session.path !== currentPath && (session.project_path ?? '') === '',
    }))
    // The sort is newest-first, so the cap keeps the most recent sessions.
    .slice(0, PALETTE_SESSION_LIMIT)
  const commandHits = commands
    .filter((command) => COMMAND_POLICY[command.name] !== undefined)
    .filter(
      (command) =>
        command.name.toLowerCase().includes(needle) ||
        (command.description ?? '').toLowerCase().includes(needle),
    )
    .map((command) => ({
      name: command.name,
      description: command.description,
      mode: COMMAND_POLICY[command.name],
    }))
  return { sessions: sessionHits, commands: commandHits }
}

export function nextPaletteIndex(
  options: { disabled: boolean }[],
  from: number,
  delta: 1 | -1,
): number {
  if (options.length === 0) return -1
  let index = from < 0 ? (delta === 1 ? options.length - 1 : 0) : from
  for (let step = 0; step < options.length; step += 1) {
    index = (index + delta + options.length) % options.length
    if (!options[index].disabled) return index
  }
  return -1
}
