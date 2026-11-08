/** The user's session choices that must outlive the runtime process.
 *
 * The runtime is cwd-bound: switching projects replaces the `zen rpc` child,
 * and a fresh child comes up with fresh defaults (ask mode, catalog model, no
 * thinking level). These three choices belong to the person, not the folder,
 * so they live in localStorage and are replayed onto every new child. Nothing
 * here talks to the wire — `appStore` owns that — which keeps it testable
 * without Tauri. */

import type { Mode } from '../rpc/types.ts'

export const MODE_KEY = 'zen.pref.mode'
export const MODEL_KEY = 'zen.pref.model'
export const THINKING_KEY = 'zen.pref.thinking'

/** THINKING_LEVELS in `zenagent/app/commands.py`. */
export const THINKING_LEVELS = ['off', 'low', 'medium', 'high', 'max'] as const

export interface SessionPrefs {
  mode: Mode | null
  model: string | null
  thinking: string | null
}

/** What a freshly spawned child answers `status` with, before any replay. */
export interface LiveChoices {
  mode?: string | null
  model?: string | null
  thinking?: string | null
}

const MODES: readonly string[] = ['plan', 'ask', 'zen']

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

function read(key: string): string | null {
  try {
    const value = storage()?.getItem(key)
    return value === undefined || value === null || value === '' ? null : value
  } catch {
    return null
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null || value === '') storage()?.removeItem(key)
    else storage()?.setItem(key, value)
  } catch {
    // A full or disabled storage costs the replay, not the session.
  }
}

export function loadPrefs(): SessionPrefs {
  const mode = read(MODE_KEY)
  const thinking = read(THINKING_KEY)
  return {
    mode: mode !== null && MODES.includes(mode) ? (mode as Mode) : null,
    model: read(MODEL_KEY),
    thinking:
      thinking !== null && (THINKING_LEVELS as readonly string[]).includes(thinking)
        ? thinking
        : null,
  }
}

export function savePref(field: keyof SessionPrefs, value: string | null): void {
  const key = field === 'mode' ? MODE_KEY : field === 'model' ? MODEL_KEY : THINKING_KEY
  write(key, value)
}

/** The flags every child gets.
 *
 * `--mode ask` is the released-binary-compatible default (old zen binaries
 * crash without an explicit mode); the user's own mode travels as a replay
 * frame once the child is up, so a stale pref can never fail a spawn. */
export function spawnArgs(): string[] {
  return ['--mode', 'ask', '--no-mcp']
}

/** The frames that bring a fresh child onto the user's last choices.
 *
 * Order matters: a model switch may reset the thinking level, so thinking is
 * replayed last. Mode is a pure runtime flag and goes first. */
export function replayFrames(
  prefs: SessionPrefs,
  live: LiveChoices,
): Record<string, unknown>[] {
  const frames: Record<string, unknown>[] = []
  if (prefs.mode !== null && prefs.mode !== live.mode) {
    frames.push({ type: 'mode', mode: prefs.mode })
  }
  if (prefs.model !== null && prefs.model !== live.model) {
    frames.push({ type: 'model', model_id: prefs.model })
  }
  if (prefs.thinking !== null && prefs.thinking !== (live.thinking ?? 'off')) {
    frames.push({ type: 'command', name: 'thinking', argument: prefs.thinking })
  }
  return frames
}
