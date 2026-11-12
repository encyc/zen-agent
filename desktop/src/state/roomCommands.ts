/** The room's `/` command table. Slash never hijacks a message: only an exact
 * command runs, anything else beginning with `/` posts as text. */

export type RoomCommand =
  | { kind: 'reload' }
  | { kind: 'stop' }
  | { kind: 'help' }
  | { kind: 'reset'; roleId: string }
  | { kind: 'unknown-reset'; roleId: string }

export interface RoomCommandEntry {
  text: string
  labelKey: string
  insert: string
}

export const ROOM_COMMANDS: readonly RoomCommandEntry[] = [
  { text: '/reload', labelKey: 'swarm.commandReload', insert: '/reload ' },
  { text: '/reset', labelKey: 'swarm.commandReset', insert: '/reset ' },
  { text: '/stop', labelKey: 'swarm.commandStop', insert: '/stop ' },
  { text: '/help', labelKey: 'swarm.commandHelp', insert: '/help ' },
]

export function roomCommandMenu(query: string): readonly RoomCommandEntry[] {
  if (!query.startsWith('/')) return []
  return ROOM_COMMANDS.filter((entry) => entry.text.startsWith(query.trim()))
}

export function parseRoomCommand(
  text: string,
  roleIds: readonly string[],
): RoomCommand | null {
  const trimmed = text.trim()
  if (trimmed === '/reload') return { kind: 'reload' }
  if (trimmed === '/stop') return { kind: 'stop' }
  if (trimmed === '/help') return { kind: 'help' }
  const reset = /^\/reset\s+(\S+)$/.exec(trimmed)
  if (reset !== null) {
    const roleId = reset[1]
    return roleIds.includes(roleId)
      ? { kind: 'reset', roleId }
      : { kind: 'unknown-reset', roleId }
  }
  return null
}
