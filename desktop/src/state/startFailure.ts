/** The shell reports a failed `rpc_start` as a structured value, not a string:
 * when the bundled runtime is missing the UI must say so instead of printing a
 * spawn error at the user. Plain-string rejections keep the old exit screen. */

export interface StartFailure {
  code: string
  bin: string
  osError: string
}

export interface RuntimeProbe {
  resolved: string | null
  exists: boolean
  version: string | null
}

/** The structured error `rpc_start` rejects with, or null for anything else. */
export function startFailureOf(error: unknown): StartFailure | null {
  if (typeof error !== 'object' || error === null) return null
  const candidate = error as Partial<StartFailure>
  if (typeof candidate.code !== 'string' || typeof candidate.bin !== 'string') return null
  return {
    code: candidate.code,
    bin: candidate.bin,
    osError: typeof candidate.osError === 'string' ? candidate.osError : '',
  }
}

export function isRuntimeMissing(failure: StartFailure | null): boolean {
  return failure?.code === 'runtime_missing'
}

/** The line a diagnostics block shows: the path, but only if something is there. */
export function probeHint(probe: Pick<RuntimeProbe, 'resolved' | 'exists'>): string {
  if (!probe.exists || probe.resolved === null) return ''
  return probe.resolved
}
