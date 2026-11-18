/** Graph section: the commit DAG as lane-drawn SVG rows. Each row renders its
 * dot at its lane and one polyline per parent edge (a cross-row edge draws as
 * a vertical drop — the common `git log` order makes the approximation read
 * correctly). Rows click through to the commit's diff. */

import { useMemo } from 'react'
import { useT } from '../../i18n/locale.ts'
import { useSettingsStore } from '../../state/stores.ts'
import { useGitStore } from '../../state/gitStore.ts'
import type { GitCommit } from '../../rpc/types.ts'
import { LANE_COLORS, assignLanes, type LaneRow } from './lanes.ts'
import css from './GitGraphSection.module.css'

const LANE_WIDTH = 12
const ROW_HEIGHT = 22

function relativeTime(timestamp: number, locale: string): string {
  const delta = Date.now() / 1000 - timestamp
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31536000],
    ['month', 2592000],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ]
  for (const [unit, seconds] of units) {
    if (delta >= seconds) {
      return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(
        -Math.floor(delta / seconds),
        unit,
      )
    }
  }
  return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(
    -Math.max(1, Math.round(delta)),
    'second',
  )
}

/** Row SVG: the commit dot plus one path per parent edge. `next` is the row
 * below (its lane is where a continuing line lands). */
function RowGraph({ row, next, laneCount }: { row: LaneRow; next: LaneRow | null; laneCount: number }) {
  const width = Math.max(1, laneCount) * LANE_WIDTH
  const y = ROW_HEIGHT / 2
  const cx = (lane: number) => lane * LANE_WIDTH + LANE_WIDTH / 2
  const nextLanes = new Map(next?.parents.map((edge) => [edge.hash, edge.lane]) ?? [])
  return (
    <svg className={css.graph} width={width} height={ROW_HEIGHT} aria-hidden="true">
      {/* Continuous lanes pass through this row. */}
      {Array.from({ length: laneCount }, (_, lane) => (
        <line
          key={lane}
          className={css.laneLine}
          data-color={lane % LANE_COLORS.length}
          x1={cx(lane)}
          x2={cx(lane)}
          y1={0}
          y2={ROW_HEIGHT}
        />
      ))}
      {/* The dot. */}
      <circle className={css.dot} data-color={row.lane % LANE_COLORS.length} cx={cx(row.lane)} cy={y} r={3.5} />
      {/* Edges to parents: a bend when the parent is the next row, else a drop. */}
      {row.parents.map((edge) => {
        const from = cx(row.lane)
        const target = nextLanes.get(edge.hash)
        if (target === undefined) {
          return (
            <line
              key={edge.hash}
              className={css.laneLine}
              data-color={edge.lane % LANE_COLORS.length}
              x1={from}
              x2={cx(edge.lane)}
              y1={y}
              y2={ROW_HEIGHT}
            />
          )
        }
        const to = cx(target)
        const d = target === row.lane
          ? `M ${from} ${y} L ${from} ${ROW_HEIGHT}`
          : `M ${from} ${y} C ${from} ${y + 6}, ${to} ${y + 8}, ${to} ${ROW_HEIGHT}`
        return (
          <path
            key={edge.hash}
            className={css.laneLine}
            data-color={edge.lane % LANE_COLORS.length}
            d={d}
            fill="none"
          />
        )
      })}
    </svg>
  )
}

export function GitGraphSection({ commits }: { commits: GitCommit[] }) {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const selected = useGitStore((state) => state.selected)
  const openCommit = useGitStore((state) => state.openCommit)
  const rows = useMemo(() => assignLanes(commits), [commits])
  const laneCount = useMemo(
    () => rows.reduce((max, row) => Math.max(max, row.lane + 1), 1),
    [rows],
  )

  return (
    <section className={css.root}>
      <div className={css.header}>{t('panel.git.graph')}</div>
      <ol className={css.list}>
        {rows.map((row, index) => {
          const commit = commits[index]
          const isSelected = selected?.kind === 'commit' && selected.hash === commit.hash
          return (
            <li key={commit.hash}>
              <button
                type="button"
                className={css.row}
                data-selected={isSelected || undefined}
                title={commit.subject}
                onClick={() => void openCommit(commit.hash)}
              >
                <RowGraph row={row} next={rows[index + 1] ?? null} laneCount={laneCount} />
                <span className={css.text}>
                  <span className={css.subject}>
                    {commit.refs.map((ref) => (
                      <span key={ref} className={css.ref}>
                        {ref}
                      </span>
                    ))}
                    {commit.subject}
                  </span>
                  <span className={css.meta}>
                    {commit.short} · {commit.author} · {relativeTime(commit.timestamp, locale)}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
