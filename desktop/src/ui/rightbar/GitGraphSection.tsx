/** Graph section: the commit DAG as lane-drawn SVG rows.
 *
 * VSCode-flavoured history: a monospace hash and relative date under the
 * subject, branch/tag pills (the checked-out branch filled), and only the
 * lanes that actually traverse a row. The lane layout hands the renderer the
 * incoming/outgoing/through/converge sets, so a quiet history stays quiet
 * instead of painting every lane on every row. */

import { useMemo, useState } from 'react'
import { useT } from '../../i18n/locale.ts'
import { useSettingsStore } from '../../state/stores.ts'
import { useGitStore } from '../../state/gitStore.ts'
import type { GitCommit } from '../../rpc/types.ts'
import { formatAge } from '../format.ts'
import { BranchIcon, ChevronDownIcon, ChevronRightIcon, ListIcon } from '../icons'
import { LANE_COLORS, assignLanes, type LaneRow } from './lanes.ts'
import { ViewToggle } from './ViewToggle.tsx'
import { readViewPref, writeViewPref } from './viewPrefs.ts'
import css from './GitGraphSection.module.css'

const VIEW_KEY = 'zen.git.graphView'
const COLLAPSED_KEY = 'zen.git.graph.collapsed'
type GraphView = 'graph' | 'list'

const LANE_PAD = 6
const ROW_HEIGHT = 34
const COLOR_COUNT = LANE_COLORS.length

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

/** Narrower lanes once a broad history fills the rail: 20 live lanes at the
 * full 14px each would leave the subject no room at all. */
function laneWidthFor(laneCount: number): number {
  if (laneCount > 10) return 9
  if (laneCount > 6) return 11
  return 14
}

/** A smooth vertical S-bend between two lane centres. */
function bend(fromX: number, fromY: number, toX: number, toY: number): string {
  const mid = (fromY + toY) / 2
  return `M ${fromX} ${fromY} C ${fromX} ${mid}, ${toX} ${mid}, ${toX} ${toY}`
}

/** Row SVG: the commit dot, straight pass-through lanes, merges/convergences. */
function RowGraph({
  row,
  width,
  laneWidth,
}: {
  row: LaneRow
  width: number
  laneWidth: number
}) {
  const cy = ROW_HEIGHT / 2
  const laneX = (lane: number) => LANE_PAD + lane * laneWidth + laneWidth / 2
  const dotX = laneX(row.lane)
  const arrives = row.incoming.includes(row.lane)
  return (
    <svg
      className={css.graph}
      data-lane-graph=""
      width={width}
      height={ROW_HEIGHT}
      viewBox={`0 0 ${width} ${ROW_HEIGHT}`}
      aria-hidden="true"
    >
      {row.through.map((lane) => (
        <line
          key={`through-${lane}`}
          className={css.lane}
          data-color={lane % COLOR_COUNT}
          x1={laneX(lane)}
          x2={laneX(lane)}
          y1={0}
          y2={ROW_HEIGHT}
        />
      ))}
      {row.converge.map((lane) => (
        <path
          key={`converge-${lane}`}
          className={css.lane}
          data-color={lane % COLOR_COUNT}
          d={bend(laneX(lane), 0, dotX, cy)}
          fill="none"
        />
      ))}
      {arrives && (
        <line
          className={css.lane}
          data-color={row.lane % COLOR_COUNT}
          x1={dotX}
          x2={dotX}
          y1={0}
          y2={cy}
        />
      )}
      {row.parents.map((edge, index) => (
        <path
          key={`parent-${index}`}
          className={css.lane}
          data-color={edge.lane % COLOR_COUNT}
          d={
            edge.lane === row.lane
              ? `M ${dotX} ${cy} L ${dotX} ${ROW_HEIGHT}`
              : bend(dotX, cy, laneX(edge.lane), ROW_HEIGHT)
          }
          fill="none"
        />
      ))}
      <circle className={css.dotHalo} cx={dotX} cy={cy} r={5.5} />
      <circle
        className={css.dot}
        data-color={row.lane % COLOR_COUNT}
        cx={dotX}
        cy={cy}
        r={3.5}
      />
    </svg>
  )
}

function RefBadge({ name, current }: { name: string; current: boolean }) {
  const tag = name.startsWith('tag: ')
  return (
    <span
      className={css.ref}
      data-kind={tag ? 'tag' : 'branch'}
      data-current={current || undefined}
      data-ref={name}
      title={name}
    >
      {tag ? name.slice('tag: '.length) : name}
    </span>
  )
}

export function GitGraphSection({ commits, branch }: { commits: GitCommit[]; branch: string }) {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const selected = useGitStore((state) => state.selected)
  const openCommit = useGitStore((state) => state.openCommit)
  const [view, setView] = useState<GraphView>(() =>
    readViewPref(VIEW_KEY, ['graph', 'list'] as const, 'graph'),
  )
  const [collapsed, setCollapsed] = useState(loadCollapsed)
  const changeView = (next: GraphView) => {
    setView(next)
    writeViewPref(VIEW_KEY, next)
  }
  const toggle = () => {
    setCollapsed((previous) => {
      const next = !previous
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
      } catch {
        // A locked-down webview can refuse storage; the toggle still works.
      }
      return next
    })
  }
  const toggleLabel = collapsed
    ? t('panel.git.expandGraph')
    : t('panel.git.collapseGraph')
  const rows = useMemo(() => assignLanes(commits), [commits])
  const laneCount = useMemo(
    () =>
      rows.reduce(
        (max, row) =>
          Math.max(
            max,
            row.lane + 1,
            ...row.parents.map((edge) => edge.lane + 1),
            ...row.incoming.map((lane) => lane + 1),
            ...row.converge.map((lane) => lane + 1),
          ),
        1,
      ),
    [rows],
  )
  const laneWidth = laneWidthFor(laneCount)
  const width = LANE_PAD * 2 + laneCount * laneWidth

  return (
    <section
      className={css.root}
      data-git-graph=""
      data-view={view}
      data-collapsed={collapsed || undefined}
    >
      <div className={css.header}>
        <button
          type="button"
          className={css.toggle}
          data-graph-toggle=""
          aria-expanded={!collapsed}
          aria-label={toggleLabel}
          title={toggleLabel}
          onClick={toggle}
        >
          {collapsed ? <ChevronRightIcon size={12} /> : <ChevronDownIcon size={12} />}
          <span>{t('panel.git.graph')}</span>
          <span className={css.headerCount}>{commits.length}</span>
        </button>
        <span className={css.headerSpacer} />
        {!collapsed && (
          <ViewToggle<GraphView>
            label={t('panel.git.graphViewMode')}
            value={view}
            onChange={changeView}
            options={[
              {
                value: 'graph',
                label: t('panel.git.graphView'),
                icon: <BranchIcon size={12} />,
              },
              {
                value: 'list',
                label: t('panel.git.listView'),
                icon: <ListIcon size={12} />,
              },
            ]}
          />
        )}
      </div>
      <ol className={css.list} hidden={collapsed}>
        {rows.map((row, index) => {
          const commit = commits[index]
          const isSelected = selected?.kind === 'commit' && selected.hash === commit.hash
          return (
            <li key={commit.hash}>
              <button
                type="button"
                className={view === 'list' ? `${css.row} ${css.rowList}` : css.row}
                data-selected={isSelected || undefined}
                data-commit={commit.short}
                title={`${commit.subject}\n${commit.author} · ${formatAge(commit.timestamp, locale)}`}
                onClick={() => void openCommit(commit.hash)}
              >
                {view === 'graph' && row !== undefined && (
                  <RowGraph row={row} width={width} laneWidth={laneWidth} />
                )}
                <span className={css.text}>
                  <span className={css.topLine}>
                    {commit.refs.map((ref) => (
                      <RefBadge
                        key={ref}
                        name={ref}
                        current={ref === branch}
                      />
                    ))}
                    <span className={css.subject}>{commit.subject}</span>
                  </span>
                  <span className={css.meta}>
                    <span className={css.hash}>{commit.short}</span>
                    <span className={css.author}>{commit.author}</span>
                    <span className={css.age}>{formatAge(commit.timestamp, locale)}</span>
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
