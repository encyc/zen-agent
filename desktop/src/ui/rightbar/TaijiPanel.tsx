/** Taiji tab: the repository's task graph — the commit lanes with the zen
 * sessions and worker statuses that live on each branch. Read-only in P0;
 * the Router session stays the only way to act on a task. */

import { useCallback, useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useStatusStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import type { TaijiGraph, TaijiProjectSession, TaijiResult, TaijiSessionMap } from '../../rpc/types'
import { attachSessions, laneColor, relativeTime, statusMeta } from './taijiGraph'
import { BranchIcon, RefreshIcon } from '../icons'
import css from './TaijiPanel.module.css'

const REQUEST_TIMEOUT_MS = 8000
const LANE_W = 11
const ROW_H = 40
const DAY_S = 86400

interface EdgeSpan {
  fromLane: number
  toLane: number
  childIndex: number
  parentIndex: number
}

function edgeSpans(graph: TaijiGraph, positions: Map<string, number>): EdgeSpan[] {
  return graph.commits.flatMap((commit, childIndex) =>
    commit.parents
      .map((parent) => positions.get(parent))
      .filter((parentIndex): parentIndex is number => parentIndex !== undefined)
      .map((parentIndex) => ({
        fromLane: graph.commits[parentIndex]!.lane,
        toLane: commit.lane,
        childIndex,
        parentIndex,
      })),
  )
}

/** One row's slice of the rail: full-height lane lines so adjacent cells
 * join seamlessly, a curve leaving through the cell's bottom edge, and the
 * commit dot at mid-height (ringed when a worktree is checked out here). */
function RailCell(props: {
  index: number
  lane: number
  laneCount: number
  spans: EdgeSpan[]
  headTip: boolean
}) {
  const { index, lane, laneCount, spans, headTip } = props
  const width = laneCount * LANE_W + 6
  const x = (laneNumber: number) => laneNumber * LANE_W + LANE_W / 2 + 3
  const mid = ROW_H / 2
  const pieces: React.ReactNode[] = []
  spans.forEach((span, key) => {
    const color = laneColor(span.fromLane)
    if (span.childIndex === index && span.childIndex !== span.parentIndex) {
      const shoulder = mid + ROW_H * 0.25
      const d = `M ${x(span.toLane)} ${mid} C ${x(span.toLane)} ${shoulder}, ${x(span.fromLane)} ${shoulder}, ${x(span.fromLane)} ${ROW_H}`
      pieces.push(
        <path key={key} d={d} fill="none" stroke={color} strokeWidth="1.5" />,
      )
    } else if (span.childIndex < index && index < span.parentIndex) {
      pieces.push(
        <line key={key} x1={x(span.fromLane)} y1={0} x2={x(span.fromLane)} y2={ROW_H} stroke={color} strokeWidth="1.5" />,
      )
    } else if (span.parentIndex === index) {
      pieces.push(
        <line key={key} x1={x(span.fromLane)} y1={0} x2={x(span.fromLane)} y2={mid} stroke={color} strokeWidth="1.5" />,
      )
    }
  })
  return (
    <svg className={css.rail} width={width} height={ROW_H} aria-hidden="true">
      {pieces}
      {headTip ? (
        <>
          <circle cx={x(lane)} cy={mid} r="6.5" fill="none" stroke={laneColor(lane)} strokeWidth="1.2" opacity="0.55" />
          <circle cx={x(lane)} cy={mid} r="3.2" fill={laneColor(lane)} />
        </>
      ) : (
        <circle cx={x(lane)} cy={mid} r="3.5" fill={laneColor(lane)} />
      )}
    </svg>
  )
}

function SessionChip(props: { session: TaijiProjectSession; here: boolean }) {
  const { session, here } = props
  const t = useT()
  const meta = statusMeta(session.status)
  const tone = meta?.tone ?? 'plain'
  const label = meta ? t(meta.key) : here ? t('panel.taiji.here') : session.title
  return (
    <span className={css.chip} data-tone={tone} data-here={here || undefined} title={session.title}>
      <i className={meta?.pulse ? css.pulseDot : css.dot} aria-hidden="true" />
      {label}
    </span>
  )
}

export function TaijiPanel() {
  const t = useT()
  const currentSessionPath = useStatusStore((state) => state.status?.session)
  const [graph, setGraph] = useState<TaijiGraph | null>(null)
  const [sessions, setSessions] = useState<TaijiSessionMap | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      const frame = await useAppStore
        .getState()
        .client()
        .send({ type: 'taiji', op: 'snapshot' }, REQUEST_TIMEOUT_MS)
      const result = frame as unknown as TaijiResult
      if (result.ok !== true) throw new Error(String(frame.message ?? 'taiji failed'))
      setGraph(result.taiji.graph)
      setSessions(result.taiji.sessions)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (failed)
    return (
      <div className={css.state}>
        <BranchIcon size={16} />
        <p>{t('panel.taiji.error')}</p>
      </div>
    )
  if (graph === null || sessions === null)
    return (
      <div className={css.state}>
        <span className={css.stateSpin} />
        <p>{t('panel.taiji.loading')}</p>
      </div>
    )
  if (!graph.is_repo)
    return (
      <div className={css.state}>
        <BranchIcon size={16} />
        <p>{t('panel.taiji.empty')}</p>
      </div>
    )

  const rows = attachSessions(graph, sessions, currentSessionPath)
  const positions = new Map(graph.commits.map((commit, index) => [commit.sha, index]))
  const spans = edgeSpans(graph, positions)
  const laneCount = Math.max(graph.max_lane + 1, 1)
  const now = Date.now() / 1000
  const activeCount = rows.filter((row) =>
    row.sessions.some((session) => statusMeta(session.status)?.tone === 'run'),
  ).length

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>{t('panel.taiji')}</span>
        <span className={css.summary}>
          {graph.branches.length > 0
            ? t('panel.taiji.summary', { branches: graph.branches.length, active: activeCount })
            : ''}
        </span>
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.taiji.refresh')}
          title={t('panel.taiji.refresh')}
          onClick={() => void load()}
        >
          <RefreshIcon size={13} />
        </button>
      </div>
      <ul className={css.list}>
        {rows.map((row, index) => {
          const visible = row.sessions.filter(
            (session) => session.status !== undefined || row.isHere || now - session.updated_at < DAY_S,
          )
          return (
            <li key={row.commit.sha} className={css.row}>
              <RailCell
                index={index}
                lane={row.commit.lane}
                laneCount={laneCount}
                spans={spans}
                headTip={row.branch?.is_head === true}
              />
              <div className={css.body}>
                <div className={css.subjectRow}>
                  <span className={css.subject} title={row.commit.subject}>
                    {row.commit.subject}
                  </span>
                  <span className={css.time}>{relativeTime(row.commit.ts, now)}</span>
                </div>
                {(row.branch !== undefined || visible.length > 0) && (
                  <div className={css.metaRow}>
                    {row.branch && (
                      <span className={css.ref} title={row.branch.name}>
                        <i style={{ background: laneColor(row.commit.lane) }} aria-hidden="true" />
                        {row.branch.name}
                      </span>
                    )}
                    {visible.map((session) => (
                      <SessionChip key={session.name} session={session} here={row.isHere} />
                    ))}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
