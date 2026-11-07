/** The update check: when to look, where the answer is cached, and the thin
 * glue to `@tauri-apps/plugin-updater`. All policy lives in `update.ts`; this
 * module owns the clock, the cache and the installer. */

import { invoke } from '@tauri-apps/api/core'
import { create } from 'zustand'
import {
  cacheAfterFailure,
  cacheIsFresh,
  shouldOffer,
  type UpdateCache,
  type UpdatePhase,
} from './update.ts'
import { useSettingsStore } from './stores.ts'

const CACHE_KEY = 'zen.update.cache'

/** The updater plugin, behind a seam: tests install a fake `check` here
 * because the real one needs the Tauri runtime. */
type UpdaterPlugin = {
  check: () => Promise<{ version?: string; downloadAndInstall?: (onEvent?: unknown) => Promise<void> } | null>
}
let loadPlugin: () => Promise<UpdaterPlugin> = () =>
  import('@tauri-apps/plugin-updater') as Promise<UpdaterPlugin>

/** Test seam: swap the plugin module (call the returned undo, or leave it). */
export function _usePluginLoader(loader: () => Promise<UpdaterPlugin>): void {
  loadPlugin = loader
}
/** How long after launch the check runs, so startup never waits on the network. */
export const BOOT_DELAY_MS = 5_000

export function bootUpdateCheck(deps: {
  current: string
  settings: { updateChecks: boolean }
  envDisabled: boolean
  busy: boolean
  pendingApprovals: number
  cache?: UpdateCache | null
  now?: number
}): 'skip' | 'cache' | 'network' {
  if (deps.envDisabled || !deps.settings.updateChecks) return 'skip'
  const cache = deps.cache ?? null
  if (cache !== null && cacheIsFresh(cache, deps.now ?? Date.now())) return 'cache'
  return 'network'
}

function readCache(): UpdateCache | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (raw === null) return null
    const parsed = JSON.parse(raw) as Partial<UpdateCache>
    if (typeof parsed.latest !== 'string' || typeof parsed.checkedAt !== 'number') return null
    return {
      checkedAt: parsed.checkedAt,
      latest: parsed.latest,
      notified: typeof parsed.notified === 'string' ? parsed.notified : null,
    }
  } catch {
    return null
  }
}

function writeCache(cache: UpdateCache): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache))
  } catch {
    // storage unavailable — the check simply runs again next launch
  }
}

interface UpdateState {
  cache: UpdateCache | null
  phase: UpdatePhase
  message: string | null
  /** Deferred install handle from the last successful check. */
  pending: unknown
  /** False when this install cannot be replaced in place (a .deb). */
  selfUpdatable: boolean
  start(): Promise<void>
  /** `boot` keeps a failure silent (an unasked-for check must not nag every
   * launch) and still stamps the TTL; `manual` reports the failure. */
  checkNow(mode?: 'boot' | 'manual'): Promise<'latest' | 'available' | 'failed'>
  install(guard?: { busy?: boolean; pendingApprovals?: number }): Promise<void>
  dismiss(): void
}

async function selfUpdatable(): Promise<boolean> {
  try {
    return await invoke<boolean>('updates_self_updatable')
  } catch {
    return true
  }
}

async function envDisabled(): Promise<boolean> {
  try {
    return await invoke<boolean>('updates_disabled_by_env')
  } catch {
    return false
  }
}

export const useUpdateStore = create<UpdateState>((set, get) => ({
  cache: readCache(),
  phase: 'idle',
  message: null,
  pending: null,
  selfUpdatable: true,

  async start() {
    // A build without an updater public key cannot verify a release, so the
    // whole feature stays dark instead of failing loudly on every launch.
    if (!__UPDATE_ENABLED__) return
    const current = __APP_VERSION__
    const decision = bootUpdateCheck({
      current,
      settings: { updateChecks: useSettingsStore.getState().updateChecks },
      envDisabled: await envDisabled(),
      busy: false,
      pendingApprovals: 0,
      cache: readCache(),
    })
    if (decision === 'skip') return
    const cache = readCache()
    set({
      cache,
      phase: cache !== null && shouldOffer(cache, current) ? 'available' : 'idle',
    })
    set({ selfUpdatable: await selfUpdatable() })
    if (decision === 'network') {
      await new Promise((resolve) => setTimeout(resolve, BOOT_DELAY_MS))
      await get().checkNow('boot')
    }
  },

  async checkNow(mode: 'boot' | 'manual' = 'manual') {
    const current = __APP_VERSION__
    set({ phase: 'checking', message: null })
    try {
      const { check } = await loadPlugin()
      const update = await check()
      const cache: UpdateCache = {
        checkedAt: Date.now(),
        latest: update?.version ?? current,
        notified: readCache()?.notified ?? null,
      }
      writeCache(cache)
      set({
        cache,
        pending: update,
        phase: update === null ? 'idle' : 'available',
        message: null,
      })
      return update === null ? 'latest' : 'available'
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (mode === 'boot') {
        const cache = cacheAfterFailure(readCache(), Date.now(), current)
        writeCache(cache)
        set({ cache, phase: 'idle', message })
      } else {
        set({ phase: 'failed', message })
      }
      return 'failed'
    }
  },

  async install(guard: { busy?: boolean; pendingApprovals?: number } = {}) {
    // Restarting mid-turn drops the run and any pending approval; the banner
    // hides the button, and this keeps a stale click from slipping through.
    if (!get().selfUpdatable) return
    if (guard.busy === true || (guard.pendingApprovals ?? 0) > 0) return
    let pending = get().pending as
      | { downloadAndInstall(onEvent?: (event: unknown) => void): Promise<void> }
      | null
    if (pending === null || pending === undefined) {
      // A banner restored from the cache has no deferred handle: the check()
      // result lives only in the session that ran it. Re-check once so the
      // click cannot die silently (v0.9.0 shipped exactly this no-op).
      await get().checkNow('manual')
      pending = get().pending as typeof pending
      if (pending === null || pending === undefined) return
    }
    set({ phase: 'installing', message: null })
    try {
      await pending.downloadAndInstall(() => {})
      await invoke('relaunch_app')
    } catch (error) {
      set({ phase: 'failed', message: error instanceof Error ? error.message : String(error) })
    }
  },

  dismiss() {
    const cache = readCache()
    if (cache !== null) writeCache({ ...cache, notified: cache.latest })
    set({ phase: 'idle', cache: readCache() })
  },
}))
