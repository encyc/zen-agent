/** Approvals and asks carry the project their slot belongs to. The host routes
 * answers by the id prefix, so a background project's card stays queued and
 * reappears when that project is active again. Untagged entries (a v2 server)
 * always render. */
export function pendingForProject<T extends { project?: string | null }>(
  queue: T[],
  project: string | null,
): T[] {
  return queue.filter(
    (entry) =>
      entry.project == null ||
      entry.project === '' ||
      project === null ||
      entry.project === project,
  )
}
