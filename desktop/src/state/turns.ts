/** Turn grouping over the flat chat node stream: each user row opens a turn
 * and every other node belongs to the turn that precedes it. */

import type {
  AssistantNode,
  ChatNode,
  ToolNode,
  UsageNode,
  UserNode,
} from './chatStore'

export interface ChatTurn {
  id: string
  user: UserNode | null
  nodes: Exclude<ChatNode, UserNode>[]
  tools: ToolNode[]
  steps: number
  durationMs: number | null
  running: boolean
}

function turnDuration(nodes: Exclude<ChatNode, UserNode>[]): number | null {
  let toolMs = 0
  let usedToolTime = false
  for (const node of nodes) {
    if (node.kind !== 'tool') continue
    const duration = node.durationMs
    if (duration !== null && duration > 0) {
      toolMs += duration
      usedToolTime = true
    }
  }
  if (usedToolTime) return toolMs
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index]
    if (node.kind === 'usage' && (node as UsageNode).turnMs !== null) {
      return (node as UsageNode).turnMs
    }
  }
  return null
}

function turnRunning(nodes: Exclude<ChatNode, UserNode>[]): boolean {
  return nodes.some(
    (node) =>
      (node.kind === 'tool' && node.state === 'running') ||
      (node.kind === 'assistant' && (node as AssistantNode).streaming),
  )
}

export function groupTurns(nodes: ChatNode[], busy: boolean): ChatTurn[] {
  const turns: ChatTurn[] = []
  let current: ChatTurn | null = null

  const open = (user: UserNode | null): ChatTurn => {
    const turn: ChatTurn = {
      id: user === null ? `turn-${turns.length}` : user.id,
      user,
      nodes: [],
      tools: [],
      steps: 0,
      durationMs: null,
      running: false,
    }
    turns.push(turn)
    return turn
  }

  for (const node of nodes) {
    if (node.kind === 'user') {
      current = open(node)
      continue
    }
    if (current === null) current = open(null)
    current.nodes.push(node)
    if (node.kind === 'tool') current.tools.push(node)
  }

  const last = turns[turns.length - 1]
  for (const turn of turns) {
    turn.steps = turn.tools.length
    turn.durationMs = turnDuration(turn.nodes)
    turn.running = turnRunning(turn.nodes) || (busy && turn === last)
  }
  return turns
}
