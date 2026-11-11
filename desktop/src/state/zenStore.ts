/** The zen fleet view model: one router, many focused workers.

The roster mirrors `zenagent/app/zen/fleet.py::ZenFleet.roster()`; live updates
arrive as server-initiated `zen_fleet` frames, snapshots as `zen_fleet` results.
*/

import { create } from 'zustand'

import type { RosterItem } from '../rpc/types.ts'

interface ZenState {
  /** Zen mode is on for the active project (a fleet owns the session). */
  enabled: boolean
  /** The user explicitly stopped the fleet; auto-enable stays off until they
   * re-enter zen. Fresh on boot, so a persisted zen mode still auto-arms. */
  stoppedByUser: boolean
  /** Router row first, then workers, as the backend sorts them. */
  roster: RosterItem[]
  /** The worker whose read-only transcript drawer is open, if any. */
  workerPath: string | null
  applyRoster(enabled: boolean, roster: RosterItem[]): void
  markStopped(): void
  openWorker(path: string): void
  closeWorker(): void
  reset(): void
}

export const useZenStore = create<ZenState>((set) => ({
  enabled: false,
  stoppedByUser: false,
  roster: [],
  workerPath: null,
  applyRoster: (enabled, roster) =>
    set(enabled ? { enabled, roster, stoppedByUser: false } : { enabled, roster }),
  markStopped: () => set({ stoppedByUser: true }),
  openWorker: (workerPath) => set({ workerPath }),
  closeWorker: () => set({ workerPath: null }),
  reset: () => set({ enabled: false, stoppedByUser: false, roster: [] }),
}))
