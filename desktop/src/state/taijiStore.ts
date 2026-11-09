/** The taiji dispatch board's data layer: the projected board state and the
 * five board ops, all through the appStore's RpcClient. The panel renders;
 * this store owns the wire. Op replies stay raw (`ok`/`message`/`queued`) so
 * the caller keeps its own notice wording. */

import { create } from 'zustand'
import type { TaijiBoard } from '../rpc/types'

const OP_TIMEOUT_MS = 15000

/** The client source, behind a seam: tests install a fake because the real
 * client needs the app store and its Tauri bridge (loaded lazily for the
 * same reason — node --test must not pull the bridge in). */
type RpcLike = {
  send<T = Record<string, unknown>>(payload: Record<string, unknown>, timeoutMs?: number): Promise<T>
}
let clientSource: () => Promise<RpcLike> = async () => {
  const { useAppStore } = await import('./appStore.ts')
  return useAppStore.getState().client()
}

/** Test seam: install a fake client source (call with the real one to undo). */
export function _useClient(loader: () => Promise<RpcLike>): void {
  clientSource = loader
}

/** One taiji wire reply: `ok` false carries the server's refusal reason. */
export type TaijiReply = {
  ok?: boolean
  message?: string
  queued?: boolean
  board?: TaijiBoard
  items?: { role: string; text: string }[]
} & Record<string, unknown>

interface TaijiState {
  board: TaijiBoard | null
  failed: boolean
  refresh(): Promise<void>
  send(name: string, text: string): Promise<TaijiReply>
  merge(name: string): Promise<TaijiReply>
  kill(name: string): Promise<TaijiReply>
  peek(name: string, limit: number): Promise<TaijiReply>
}

/** A slow poll must not stack requests. */
let loading = false

async function op(payload: Record<string, unknown>): Promise<TaijiReply> {
  const client = await clientSource()
  return client.send<TaijiReply>({ type: 'taiji', ...payload }, OP_TIMEOUT_MS)
}

export const useTaijiStore = create<TaijiState>((set) => ({
  board: null,
  failed: false,

  async refresh() {
    if (loading) return
    loading = true
    try {
      const reply = await op({ op: 'board' })
      if (reply.ok !== true) throw new Error(String(reply.message ?? 'taiji failed'))
      set({ board: reply.board as TaijiBoard, failed: false })
    } catch {
      set({ failed: true })
    } finally {
      loading = false
    }
  },

  send(name, text) {
    return op({ op: 'message', name, text })
  },

  merge(name) {
    return op({ op: 'merge', name })
  },

  kill(name) {
    return op({ op: 'kill', name })
  },

  peek(name, limit) {
    return op({ op: 'peek', name, limit })
  },
}))
