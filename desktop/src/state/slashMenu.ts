/** Entries for the composer's `/` menu: the registered commands plus the
 * enabled skills, mirroring the TUI's line-start completion. */

export interface SlashCommand {
  name: string
  description: string
}

export interface SlashSkill {
  name: string
  description: string
  enabled: boolean
}

export interface SlashEntry {
  /** Name without the leading slash; skills carry the `skill:` prefix. */
  name: string
  description: string
  kind: 'command' | 'skill'
}

const SKILL_PREFIX = 'skill:'

export function slashEntries(
  commands: SlashCommand[],
  skills: SlashSkill[],
  draft: string,
): SlashEntry[] {
  if (!draft.startsWith('/') || /\s/.test(draft)) return []
  const token = draft.toLowerCase()
  const needle = token.slice(1)

  const commandHits = commands.filter((command) =>
    command.name.toLowerCase().startsWith(needle),
  )

  const enabled = skills.filter((skill) => skill.enabled)
  let skillHits: SlashSkill[]
  if (token.startsWith(`/${SKILL_PREFIX}`)) {
    const nameNeedle = needle.slice(SKILL_PREFIX.length)
    skillHits = enabled.filter((skill) =>
      skill.name.toLowerCase().startsWith(nameNeedle),
    )
  } else {
    skillHits = enabled.filter((skill) => skill.name.toLowerCase().startsWith(needle))
    if (skillHits.length === 0) {
      skillHits = enabled.filter((skill) =>
        `${SKILL_PREFIX}${skill.name}`.toLowerCase().startsWith(needle),
      )
    }
  }

  return [
    ...commandHits.map(
      (command): SlashEntry => ({
        name: command.name,
        description: command.description,
        kind: 'command',
      }),
    ),
    ...skillHits.map(
      (skill): SlashEntry => ({
        name: `${SKILL_PREFIX}${skill.name}`,
        description: skill.description,
        kind: 'skill',
      }),
    ),
  ].sort((a, b) => a.name.localeCompare(b.name))
}
