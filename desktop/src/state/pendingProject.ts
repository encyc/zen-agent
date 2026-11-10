/** Approvals and asks carry the project and session their slot belongs to. The
 * host routes answers by the id prefix, so a background card stays queued and
 * reappears when its session is active again. Untagged entries (a v2 server)
 * always render. */
export function pendingForProject<
  T extends { project?: string | null; session?: string | null },
>(queue: T[], project: string | null, session: string | null = null): T[] {
  return queue.filter(
    (entry) =>
      (entry.project == null ||
        entry.project === '' ||
        project === null ||
        entry.project === project) &&
      (entry.session == null ||
        entry.session === '' ||
        session === null ||
        entry.session === session),
  )
}
