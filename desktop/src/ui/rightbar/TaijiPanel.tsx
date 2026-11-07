/** Taiji tab: the repository's task graph — commit lanes with the zen
 * sessions that live on each branch. Read-only in P0; the Router session
 * stays the only way to act on a task. */

import { useCallback, useEffect, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useStatusStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import type { TaijiGraph, TaijiResult, TaijiSessionMap } from '../../rpc/types'
import { attachSessions, laneColor } from './taijiGraph'
import { BranchIcon, RefreshIcon } from '../icons'
import css from './TaijiPanel.module.css'

const REQUEST_TIMEOUT_MS = 8000
const LANE_W = 11
const ROW_H = 34

interface EdgeSpan {
  fromLane: number
  toLane: number
  childIndex: number
  parentIndex: number
}

/** Rows are rendered newest-first, so a child commit sits above its parent:
 * the curve lives in the child's cell, straight verticals in the cells
 * between, and a stub above the parent's dot. */
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

function RailCell(props: {
  index: number
  lane: number
  laneCount: number
  spans: EdgeSpan[]
}) {
  const { index, lane, laneCount, spans } = props
  const width = laneCount * LANE_W + 6
  const x = (laneNumber: number) => laneNumber * LANE_W + LANE_W / 2 + 3
  const pieces: React.ReactNode[] = []
  spans.forEach((span, key) => {
    const color = laneColor(span.fromLane)
    if (span.childIndex === index) {
      const d = `M ${x(span.toLane)} ${ROW_H / 2} C ${x(span.toLane)} ${ROW_H * 0.8}, ${x(span.fromLane)} ${ROW_H * 0.8}, ${x(span.fromLane)} ${ROW_H}`
      pieces.push(<path key={key} d={d} fill="none" stroke={color} strokeWidth="1.5" />)
    } else if (span.childIndex < index && index < span.parentIndex) {
      pieces.push(
        <line key={key} x1={x(span.fromLane)} y1={0} x2={x(span.fromLane)} y2={ROW_H} stroke={color} strokeWidth="1.5" />,
      )
    } else if (span.parentIndex === index) {
      pieces.push(
        <line key={key} x1={x(span.fromLane)} y1={0} x2={x(span.fromLane)} y2={ROW_H / 2} stroke={color} strokeWidth="1.5" />,
      )
    }
  })
  return (
    <svg className={css.rail} width={width} height={ROW_H} aria-hidden="true">
      {pieces}
      <circle cx={x(lane)} cy={ROW_H / 2} r="3.5" fill={laneColor(lane)} />
    </svg>
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

  if (failed) return <p className={css.note}>{t('panel.taiji.error')}</p>
  if (graph === null || sessions === null) return <p className={css.note}>{t('panel.taiji.loading')}</p>
  if (!graph.is_repo) return <p className={css.note}>{t('panel.taiji.empty')}</p>

  const rows = attachSessions(graph, sessions, currentSessionPath)
  const positions = new Map(graph.commits.map((commit, index) => [commit.sha, index]))
  const spans = edgeSpans(graph, positions)
  const laneCount = Math.max(graph.max_lane + 1, 1)

  return (
    <div className={css.root}>
      <div className={css.header}>
        <BranchIcon size={13} />
        <span className={css.title}>{t('panel.taiji')}</span>
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
        {rows.map((row, index) => (
          <li key={row.commit.sha} className={css.item}>
            <div className={css.itemHead}>
              <RailCell index={index} lane={row.commit.lane} laneCount={laneCount} spans={spans} />
              <span className={css.subject} title={row.commit.subject}>
                {row.commit.subject}
              </span>
              {row.branch && (
                <span className={css.badge} data-head={row.branch.is_head || undefined}>
                  {row.branch.name}
                </span>
              )}
            </div>
            {row.sessions.length > 0 && (
              <div className={css.sessions}>
                {row.sessions.map((session) => (
                  <span key={session.name} className={css.session} data-here={row.isHere || undefined}>
                    {row.isHere ? `${session.title} · ${t('panel.taiji.here')}` : session.title}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
