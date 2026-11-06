/** RPC client: id-correlated requests over the Tauri bridge, plus an event
 * fan-out for the streamed `rpc:frame` frames. */

import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type { ServerFrame } from './types'

export interface StartOptions {
  bin?: string
  cwd?: string
  args?: string[]
}

type EventHandler = (frame: ServerFrame) => void

export class RpcClient {
  private nextId = 0
  private handlers = new Set<EventHandler>()
  private unlisten: UnlistenFn | null = null
  private stderrHandlers = new Set<(line: string) => void>()
  private exitHandlers = new Set<(code: number | null, pid?: number) => void>()
  private unlistenStderr: UnlistenFn | null = null
  private unlistenExit: UnlistenFn | null = null

  async start(options: StartOptions = {}): Promise<number> {
    if (this.unlisten === null) {
      this.unlisten = await listen<{ frame: ServerFrame }>('rpc:frame', (event) => {
        for (const handler of this.handlers) handler(event.payload.frame)
      })
    }
    if (this.unlistenStderr === null) {
      this.unlistenStderr = await listen<{ line: string }>('rpc:stderr', (event) => {
        for (const handler of this.stderrHandlers) handler(event.payload.line)
      })
    }
    if (this.unlistenExit === null) {
      this.unlistenExit = await listen<{ code: number | null; pid?: number }>('rpc:exit', (event) => {
        for (const handler of this.exitHandlers) handler(event.payload.code, event.payload.pid)
      })
    }
    const report = await invoke<{ pid: number }>('rpc_start', {
      options: {
        bin: options.bin ?? null,
        cwd: options.cwd ?? null,
        args: options.args ?? [],
      },
    })
    return report.pid
  }

  async stop(): Promise<void> {
    await invoke('rpc_stop').catch(() => {})
  }

  /** Send one request and resolve with its result/error frame. */
  send(payload: Record<string, unknown>, timeoutMs?: number): Promise<Record<string, unknown>> {
    const id = `r${(this.nextId += 1)}`
    return invoke<Record<string, unknown>>('rpc_send', {
      payload: { ...payload, id },
      timeoutMs: timeoutMs ?? null,
    })
  }

  onFrame(handler: EventHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  onStderr(handler: (line: string) => void): () => void {
    this.stderrHandlers.add(handler)
    return () => this.stderrHandlers.delete(handler)
  }

  onExit(handler: (code: number | null, pid?: number) => void): () => void {
    this.exitHandlers.add(handler)
    return () => this.exitHandlers.delete(handler)
  }
}
