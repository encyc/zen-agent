/** The automations panel: every project's scheduled runs on one page.
 *
 * Read-mostly on purpose. The daemon owns when things run; this surface edits
 * definitions and asks for a run now. Rows are grouped by project because one
 * daemon serves one project, so the group *is* the thing that has to be up.
 */

import { useEffect, useMemo, useState } from 'react'

import { formatNextDue, groupByProject } from '../state/automations.ts'
import { startAutomationsPolling, useAutomationsStore } from '../state/automationsStore.ts'
import { useProjectStore } from '../state/stores.ts'
import css from './AutomationsPanel.module.css'

function projectName(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean)
  return parts.at(-1) ?? path
}

function statusGlyph(status: string): string {
  if (status === 'done') return '✓'
  if (status === 'failed' || status === 'timeout') return '✗'
  if (status === 'skipped') return '−'
  return '·'
}

export function AutomationsPanel() {
  const rows = useAutomationsStore((state) => state.rows)
  const warnings = useAutomationsStore((state) => state.warnings)
  const failed = useAutomationsStore((state) => state.failed)
  const { runNow, setEnabled, remove, add } = useAutomationsStore()
  const projects = useProjectStore((state) => state.projects)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState({
    project: '',
    name: '',
    prompt: '',
    schedule: 'daily 09:00',
  })

  useEffect(() => startAutomationsPolling(), [])
  useEffect(() => {
    // The relative "next due" cells need a clock tick of their own: a poll
    // every two seconds would re-render the whole table for nothing.
    const timer = setInterval(() => setNow(Date.now() / 1000), 15000)
    return () => clearInterval(timer)
  }, [])

  const groups = useMemo(() => groupByProject(rows), [rows])
  const targets =
    projects.length > 0 ? projects.map((entry) => entry.path) : [...groups.keys()]

  async function submit() {
    if (draft.project === '' || draft.name === '' || draft.prompt === '') return
    await add(draft)
    setCreating(false)
    setDraft({ project: '', name: '', prompt: '', schedule: 'daily 09:00' })
  }

  return (
    <section className={css.panel} aria-label="automations">
      <header className={css.header}>
        <h2 className={css.title}>Automations</h2>
        <button type="button" className={css.action} onClick={() => setCreating((on) => !on)}>
          {creating ? 'Cancel' : 'New'}
        </button>
      </header>

      {failed && <p className={css.warning}>Could not reach the scheduler.</p>}
      {warnings.map((warning) => (
        <p key={warning} className={css.warning}>
          {warning}
        </p>
      ))}

      {creating && (
        <form
          className={css.form}
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <label className={css.field}>
            <span>Project</span>
            <select
              value={draft.project}
              onChange={(event) => setDraft({ ...draft, project: event.target.value })}
            >
              <option value="">Choose…</option>
              {targets.map((path: string) => (
                <option key={path} value={path}>
                  {projectName(path)}
                </option>
              ))}
            </select>
          </label>
          <label className={css.field}>
            <span>Name</span>
            <input
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            />
          </label>
          <label className={css.field}>
            <span>When</span>
            <input
              value={draft.schedule}
              placeholder="daily 09:00 · every 15m"
              onChange={(event) => setDraft({ ...draft, schedule: event.target.value })}
            />
          </label>
          <label className={css.field}>
            <span>Instruction</span>
            <textarea
              rows={4}
              value={draft.prompt}
              onChange={(event) => setDraft({ ...draft, prompt: event.target.value })}
            />
          </label>
          <button type="submit" className={css.primary}>
            Create
          </button>
        </form>
      )}

      {groups.size === 0 && !creating && (
        <p className={css.empty}>
          No automations yet. Create one and it runs on its schedule, in this project&apos;s
          own session.
        </p>
      )}

      {[...groups.entries()].map(([project, bucket]) => (
        <div key={project} className={css.group}>
          <h3 className={css.groupTitle}>{projectName(project)}</h3>
          <ul className={css.list}>
            {bucket.map((row) => (
              <li key={`${project}:${row.name}`} className={css.row}>
                <span
                  className={css.status}
                  data-status={row.last_skip !== '' ? 'skipped' : row.last_status}
                  title={row.last_error || row.last_skip || row.last_status || 'never run'}
                >
                  {statusGlyph(row.last_skip !== '' ? 'skipped' : row.last_status)}
                </span>
                <span className={css.name}>{row.name}</span>
                <span className={css.schedule}>{row.schedule}</span>
                <span className={css.due}>
                  {row.enabled ? formatNextDue(row.next_due, now) : 'paused'}
                </span>
                <span className={css.cost}>{row.cost > 0 ? row.cost.toFixed(4) : '—'}</span>
                <button
                  type="button"
                  className={css.action}
                  onClick={() => void runNow(project, row.name)}
                  disabled={!row.enabled}
                >
                  Run
                </button>
                <button
                  type="button"
                  className={css.action}
                  onClick={() => void setEnabled(project, row.name, !row.enabled)}
                >
                  {row.enabled ? 'Pause' : 'Resume'}
                </button>
                <button
                  type="button"
                  className={css.action}
                  onClick={() => void remove(project, row.name)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}
