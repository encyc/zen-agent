/** RPC client: id-correlated requests over the Tauri bridge, plus an event
 * fan-out for the streamed `rpc:frame` frames. */

import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type { ServerFrame } from './types.ts'
import { PROTOCOL_VERSION } from './types.ts'
import { startFailureOf, type RuntimeProbe } from '../state/startFailure.ts'

export interface StartOptions {
  bin?: string
  cwd?: string
  args?: string[]
}

/** The Tauri side of the client, behind a seam: node tests install a fake
 * because the real bridge only exists inside a webview. */
export interface Bridge {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>
  listen<T>(event: string, handler: (event: { payload: T }) => void): Promise<UnlistenFn>
  /** Restart the app in place (after an installed update). */
  relaunch(): Promise<void>
}

let bridge: Bridge = {
  invoke: (command, args) => invoke(command, args),
  listen: (event, handler) => listen(event, handler),
  relaunch: () => invoke('relaunch_app'),
}

/** Restart the app after an installed update, through the same bridge seam as
 * every other shell command. */
export function relaunchApp(): Promise<void> {
  return bridge.relaunch()
}

/** Test seam: swap the Tauri bridge (the real one needs a webview). */
export function _useBridge(next: Bridge): void {
  bridge = next
}

/** The shell's answer to `get_initial_state`: both boot-time policy flags in
 * one round-trip (camelCase keys come straight from Rust). */
export interface InitialState {
  updatesDisabled: boolean
  selfUpdatable: boolean
}

/** Where the shell looks for the runtime, and what it reports there.
 *
 * The command is `Result<RuntimeProbe, String>`: a resolve is the Ok answer, a
 * rejection is the shell's error string (the diagnostics card shows "unknown"
 * instead of a probe it never got). */
export function runtimeProbe(): Promise<RuntimeProbe> {
  return bridge.invoke<RuntimeProbe>('runtime_probe')
}

/** The two boot flags every launch reads: env-disabled updates and whether
 * this install can replace itself. One invoke, not two. */
export function initialState(): Promise<InitialState> {
  return bridge.invoke<InitialState>('get_initial_state')
}

type EventHandler = (frame: ServerFrame) => void

export class RpcClient {
  private nextId = 0
  private handlers = new Set<EventHandler>()
  private stderrHandlers = new Set<(line: string) => void>()
  private exitHandlers = new Set<(code: number | null, pid?: number) => void>()
  /** The in-flight listener registration: concurrent starts await one setup
   * instead of each adding its own copy of every handler. */
  private wiring: Promise<void> | null = null

  async start(options: StartOptions = {}): Promise<number> {
    if (this.wiring === null) {
      // A failed registration must not poison every later start: drop the
      // cached promise so a retry registers again.
      this.wiring = this.listenAll().catch((error: unknown) => {
        this.wiring = null
        throw error
      })
    }
    await this.wiring
    try {
      const report = await bridge.invoke<{ pid: number }>('rpc_start', {
        options: {
          bin: options.bin ?? null,
          cwd: options.cwd ?? null,
          args: options.args ?? [],
        },
      })
      return report.pid
    } catch (error) {
      // `rpc_start` rejects with a structured reason when the runtime is gone;
      // anything else keeps travelling as the string it always was.
      const failure = startFailureOf(error)
      throw failure ?? error
    }
  }

  /** Register the three stream listeners once; the promise is what makes a
   * concurrent `start()` join the same registration.
   *
   * A partial failure releases what it already attached: the retry must not
   * leave the first attempt's `rpc:frame` listener alive, or every frame would
   * reach the handlers twice. */
  private async listenAll(): Promise<void> {
    const attached: UnlistenFn[] = []
    try {
      attached.push(
        await bridge.listen<{ frame: ServerFrame }>('rpc:frame', (event) => {
          for (const handler of this.handlers) handler(event.payload.frame)
        }),
      )
      attached.push(
        await bridge.listen<{ line: string }>('rpc:stderr', (event) => {
          for (const handler of this.stderrHandlers) handler(event.payload.line)
        }),
      )
      attached.push(
        await bridge.listen<{ code: number | null; pid?: number }>('rpc:exit', (event) => {
          for (const handler of this.exitHandlers) handler(event.payload.code, event.payload.pid)
        }),
      )
    } catch (error) {
      for (const unlisten of attached) {
        try {
          unlisten()
        } catch {
          // A listener that cannot be released must not mask the registration
          // error that sent us here.
        }
      }
      throw error
    }
  }

  async stop(): Promise<void> {
    await bridge.invoke('rpc_stop').catch(() => {})
  }

  /** Verify the wire contract before any domain request.
   *
   * Rejects with a structured failure (the same shape `rpc_start` rejects
   * with) so the app's diagnostics card can render a version mismatch. */
  async handshake(): Promise<void> {
    const frame = await this.send<{ protocol_version?: unknown }>({ type: 'hello' })
    if (frame.protocol_version !== PROTOCOL_VERSION) {
      throw {
        code: 'protocol_mismatch',
        bin: 'zen',
        osError: `client speaks protocol ${PROTOCOL_VERSION}, server answered ${String(frame.protocol_version)}`,
      }
    }
  }

  /** Send one request and resolve with its result frame, typed `T` by the
   * caller from `rpc/types.ts`.
   *
   * This is the one place an untyped bridge answer becomes a domain frame:
   * callers still gate on `ok` (and `type`) before reading `T`'s fields, and a
   * transport failure still rejects. */
  send<T = Record<string, unknown>>(payload: Record<string, unknown>, timeoutMs?: number): Promise<T> {
    const id = `r${(this.nextId += 1)}`
    return bridge.invoke<T>('rpc_send', {
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
