/** Right dock: the session inspector — usage, context, todos, jobs and
 * subagents (what the zen TUI sidebar renders, as a dsh-style right rail). */

import { useStatusStore } from '../../state/stores'
import { useAppStore } from '../../state/appStore'
import { useT } from '../../i18n/locale'
import { StateDot } from '../primitives'
import css from './RightPanel.module.css'

export function RightPanel() {
  const t = useT()
  const status = useStatusStore((state) => state.status)
  if (status === null) return <div className={css.root} />
  const usage = status.usage
  const contextUsed = status.context_used ?? 0
  const contextWindow = status.context_window ?? 0
  const jobs = status.jobs ?? []
  const subagents = status.subagents ?? []
  const todos = status.todos ?? []

  return (
    <div className={css.root} data-selectable>
      <div className={css.header}>{t('status.idle' /* placeholder for a future title */)}</div>

      <Section title={t('usage.context')}>
        <Row label={t('usage.context')} value={`${formatCount(contextUsed)} / ${formatCount(contextWindow)}`} />
        <Row label="cache" value={status.cache_hit_ratio !== null && status.cache_hit_ratio !== undefined ? `${Math.round(status.cache_hit_ratio * 100)}%` : '—'} />
        <Row label={t('usage.turn')} value={status.turn_ms !== null && status.turn_ms !== undefined && status.turn_ms > 0 ? formatDuration(status.turn_ms) : '—'} />
      </Section>

      <Section title={t('usage.tokens')}>
        <Row label="in / out" value={`${formatCount(usage.input)} / ${formatCount(usage.output)}`} />
        <Row label="cache r/w" value={`${formatCount(usage.cache_read)} / ${formatCount(usage.cache_write)}`} />
        <Row label={t('usage.cost')} value={`$${usage.cost.toFixed(4)}`} />
        {(status.subagent_cost ?? 0) > 0 && (
          <Row label="subagents" value={`$${(status.subagent_cost ?? 0).toFixed(4)} · ${formatCount(status.subagent_tokens ?? 0)}`} />
        )}
      </Section>

      {todos.length > 0 && (
        <Section title={t('status.todos')}>
          {todos.map((todo, index) => (
            <div key={index} className={css.todoRow}>
              <StateDot
                state={todo.status === 'completed' ? 'ok' : todo.status === 'in_progress' ? 'running' : 'idle'}
                size={7}
              />
              <span className={css.todoText}>{String(todo.text ?? todo.description ?? '')}</span>
            </div>
          ))}
        </Section>
      )}

      {jobs.length > 0 && (
        <Section title={t('status.jobs')}>
          {jobs.slice(0, 8).map((job) => (
            <button
              key={job.id}
              type="button"
              className={css.jobRow}
              onClick={() => void useAppStore.getState().runCommand('jobs', job.id)}
            >
              <StateDot state={job.status === 'running' ? 'running' : job.status === 'ok' ? 'ok' : 'error'} size={7} />
              <span className={css.jobCommand}>{job.command}</span>
            </button>
          ))}
        </Section>
      )}

      {subagents.length > 0 && (
        <Section title={t('status.subagents')}>
          {subagents.slice(0, 8).map((subagent) => (
            <div key={subagent.call_id} className={css.jobRow}>
              <StateDot
                state={subagent.status === 'running' ? 'running' : subagent.status === 'ok' ? 'ok' : 'error'}
                size={7}
              />
              <span className={css.jobCommand}>{subagent.name}</span>
              <span className={css.jobMeta}>{formatCount(subagent.tokens)} tok</span>
            </div>
          ))}
        </Section>
      )}

      {status.last_error !== '' && status.last_error !== undefined && (
        <Section title={t('status.error')}>
          <div className={css.errorText}>{status.last_error}</div>
        </Section>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={css.section}>
      <div className={css.sectionTitle}>{title}</div>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className={css.row}>
      <span className={css.rowLabel}>{label}</span>
      <span className={css.rowValue}>{value}</span>
    </div>
  )
}

function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '0'
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m`
}
