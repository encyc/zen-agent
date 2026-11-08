/** Work panel — pi's docked right rail: a 46px tab strip with review, files,
 * worktree and per-subagent tabs, a launcher for the tool tabs, a close
 * control and a resizable left edge. */

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useStatusStore, useWorkPanelStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import {
  RIGHTBAR_DEFAULT,
  RIGHTBAR_MAX_RATIO,
  RIGHTBAR_MIN,
  clampWidth,
} from '../columns'
import {
  BotIcon,
  BranchIcon,
  FileIcon,
  FolderIcon,
  PlusIcon,
  SparkIcon,
  XIcon,
} from '../icons'
import { FilesPanel } from './FilesPanel'
import { MemoryPanel } from './MemoryPanel'
import { ReviewPanel } from './ReviewPanel'
import { SubagentTranscript } from './SubagentTranscript'
import { WorktreePanel } from './WorktreePanel'
import { TaijiPanel } from './TaijiPanel'
import css from './RightPanel.module.css'

export interface RightPanelProps {
  width: number
  onWidth(width: number): void
  onClose(): void
}

interface DragState {
  pointerId: number
  startX: number
  startWidth: number
  frame: number
}

const TOOL_TABS = [
  { kind: 'review', label: 'panel.review', Icon: FileIcon },
  { kind: 'files', label: 'panel.files', Icon: FolderIcon },
  { kind: 'worktree', label: 'panel.worktree', Icon: BranchIcon },
  { kind: 'memory', label: 'panel.memory', Icon: SparkIcon },
  { kind: 'taiji', label: 'panel.taiji', Icon: BranchIcon },
] as const

function clampPanel(px: number): number {
  return clampWidth(px, RIGHTBAR_MIN, Math.max(RIGHTBAR_MIN, window.innerWidth * RIGHTBAR_MAX_RATIO))
}

export function RightPanel({ width, onWidth, onClose }: RightPanelProps) {
  const t = useT()
  const status = useStatusStore((state) => state.status)
  const tabs = useWorkPanelStore((state) => state.tabs)
  const activeId = useWorkPanelStore((state) => state.activeId)
  const showTab = useWorkPanelStore((state) => state.showTab)
  const activate = useWorkPanelStore((state) => state.activate)
  const closeTab = useWorkPanelStore((state) => state.closeTab)
  const [launcher, setLauncher] = useState(false)
  const launcherRef = useRef<HTMLDivElement | null>(null)
  const drag = useRef<DragState | null>(null)

  useEffect(() => {
    if (!launcher) return
    const close = (event: MouseEvent) => {
      if (!launcherRef.current?.contains(event.target as Node)) setLauncher(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [launcher])

  const activeTab = tabs.find((tab) => tab.id === activeId) ?? tabs[0]
  const subagentName = (callId: string | undefined, fallback: string | undefined) =>
    status?.subagents?.find((entry) => entry.call_id === callId)?.name ||
    fallback ||
    t('panel.subagent')

  const labelFor = (tab: (typeof tabs)[number]): string => {
    if (tab.kind === 'subagent') return subagentName(tab.callId, tab.name)
    const tool = TOOL_TABS.find((entry) => entry.kind === tab.kind)
    return tool === undefined ? tab.kind : t(tool.label)
  }

  const iconFor = (kind: string) => {
    const tool = TOOL_TABS.find((entry) => entry.kind === kind)
    return tool?.Icon ?? BotIcon
  }

  const stopDrag = () => {
    const state = drag.current
    if (state === null) return
    if (state.frame !== 0) cancelAnimationFrame(state.frame)
    drag.current = null
    document.documentElement.removeAttribute('data-work-panel-resizing')
  }

  const start = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: width,
      frame: 0,
    }
    document.documentElement.setAttribute('data-work-panel-resizing', 'true')
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (state === null || state.pointerId !== event.pointerId) return
    const next = clampPanel(state.startWidth + (state.startX - event.clientX))
    if (state.frame !== 0) cancelAnimationFrame(state.frame)
    state.frame = requestAnimationFrame(() => {
      state.frame = 0
      if (drag.current === state) onWidth(next)
    })
  }

  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 32 : 16
    let next: number | null = null
    if (event.key === 'ArrowLeft') next = width + step
    else if (event.key === 'ArrowRight') next = width - step
    else if (event.key === 'Home') next = RIGHTBAR_DEFAULT
    if (next === null) return
    event.preventDefault()
    onWidth(clampPanel(next))
  }

  return (
    <div className={css.root} data-work-panel data-tab={activeTab?.kind ?? 'review'}>
      <div
        className={css.resize}
        role="separator"
        aria-orientation="vertical"
        aria-label={t('chat.resizeWidth')}
        aria-valuemin={RIGHTBAR_MIN}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={(event) => {
          if (drag.current?.pointerId !== event.pointerId) return
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
          stopDrag()
        }}
        onPointerCancel={(event) => {
          if (drag.current?.pointerId !== event.pointerId) return
          stopDrag()
        }}
        onDoubleClick={() => onWidth(RIGHTBAR_DEFAULT)}
        onKeyDown={keyDown}
      />
      <div className={css.header}>
        <div className={css.tabs} role="tablist">
          {tabs.map((tab) => {
            const Icon = iconFor(tab.kind)
            const label = labelFor(tab)
            const selected = tab.id === activeTab?.id
            return (
              <div
                key={tab.id}
                className={css.tabWrap}
                data-active={selected || undefined}
                role="presentation"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  className={css.tab}
                  title={label}
                  onClick={() => activate(tab.id)}
                >
                  <Icon size={13} />
                  <span className={css.tabLabel}>{label}</span>
                </button>
                <button
                  type="button"
                  className={css.tabClose}
                  aria-label={t('panel.closeTab', { name: label })}
                  title={t('panel.closeTab', { name: label })}
                  onClick={() => closeTab(tab.id)}
                >
                  <XIcon size={11} />
                </button>
              </div>
            )
          })}
        </div>
        <div
          className={css.launcherWrap}
          ref={launcherRef}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setLauncher(false)
          }}
        >
          <button
            type="button"
            className={css.iconButton}
            aria-label={t('panel.launcher')}
            title={t('panel.launcher')}
            aria-expanded={launcher}
            onClick={() => setLauncher((value) => !value)}
          >
            <PlusIcon size={14} />
          </button>
          {launcher && (
            <div className={css.launcher} role="menu">
              {TOOL_TABS.map(({ kind, label, Icon }) => (
                <button
                  key={kind}
                  type="button"
                  role="menuitem"
                  className={css.launcherRow}
                  data-launcher-item={kind}
                  onClick={() => {
                    showTab(kind)
                    setLauncher(false)
                  }}
                >
                  <Icon size={13} />
                  <span>{t(label)}</span>
                  {tabs.some((entry) => entry.id === kind) && (
                    <span className={css.launcherOpen} />
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          className={css.close}
          aria-label={t('panel.close')}
          onClick={onClose}
        >
          <XIcon size={13} />
        </button>
      </div>
      <div className={css.body} data-selectable data-embed="">
        {activeTab === undefined ? null : activeTab.kind === 'subagent' ? (
          <SubagentTranscript
            target={{ callId: activeTab.callId ?? '', name: activeTab.name ?? '' }}
          />
        ) : activeTab.kind === 'memory' ? (
          <MemoryPanel />
        ) : activeTab.kind === 'files' ? (
          <FilesPanel />
        ) : activeTab.kind === 'worktree' ? (
          <WorktreePanel />
        ) : activeTab.kind === 'taiji' ? (
          <TaijiPanel />
        ) : (
          <div className={css.reviewWrap}>
            <ReviewPanel />
          </div>
        )}
      </div>
    </div>
  )
}
