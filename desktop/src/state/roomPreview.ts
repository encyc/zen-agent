/** What a room post will do, derived before it is sent: the number of tasks a
 * message spawns, and the ways it will surprise the sender — a busy role's
 * task queues, and a non-vision role cannot take the attached images. */

import type { SwarmAgentState, SwarmRole } from '../rpc/types'

const MENTION_RE = /(?<![\w@])@([a-z0-9][a-z0-9._-]*)/g

export function roleMentions(text: string, roleIds: readonly string[]): string[] {
  const known = new Set(roleIds)
  const found: string[] = []
  for (const match of text.matchAll(MENTION_RE)) {
    const id = match[1]
    if (known.has(id) && !found.includes(id)) found.push(id)
  }
  return found
}

export interface RoomPreview {
  tasks: number
  images: number
  queued: string[]
  noVision: string[]
}

/** Attachments reach the room only through a delegation: without a mention
 * there is no task to carry them. */
export function imagesNeedMention(imageCount: number, mentions: readonly string[]): boolean {
  return imageCount > 0 && mentions.length === 0
}

export function roomSendPreview(
  mentions: readonly string[],
  roles: readonly SwarmRole[],
  agents: readonly SwarmAgentState[],
  imageCount: number,
): RoomPreview {
  const running = new Set(agents.filter((a) => a.state === 'running').map((a) => a.agent))
  const byId = new Map(roles.map((role) => [role.id, role]))
  return {
    tasks: mentions.length,
    images: imageCount,
    queued: mentions.filter((id) => running.has(id)),
    noVision:
      imageCount === 0
        ? []
        : mentions.filter((id) => byId.get(id)?.supportsVision === false),
  }
}
