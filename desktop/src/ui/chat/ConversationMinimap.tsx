/** Codex-style conversation rail: one dash per user/assistant row, scrolled
 * and clicked from the transcript's left gutter. */

import { memo, type CSSProperties } from 'react'
import type { ChatNode } from '../../state/chatStore'
import { useT } from '../../i18n/locale'
import css from './ConversationMinimap.module.css'

export interface ConversationMinimapProps {
  nodes: ChatNode[]
  onJump(id: string): void
  activeId: string | null
}

export const ConversationMinimap = memo(function ConversationMinimap({
  nodes,
  onJump,
  activeId,
}: ConversationMinimapProps) {
  const t = useT()
  const markers: { id: string; kind: 'user' | 'assistant'; label: string }[] = []
  for (const node of nodes) {
    if (node.kind !== 'user' && node.kind !== 'assistant') continue
    const label = node.text.trim().slice(0, 60)
    markers.push({ id: node.id, kind: node.kind, label })
  }
  if (markers.length === 0) return null
  const style = { '--dash-count': markers.length } as CSSProperties
  return (
    <div
      className={css.rail}
      style={style}
      data-minimap
      role="group"
      aria-label={t('minimap.label')}
    >
      {markers.map((marker) => (
        <button
          key={marker.id}
          type="button"
          className={css.marker}
          data-minimap-dash
          data-kind={marker.kind}
          data-active={marker.id === activeId || undefined}
          aria-label={marker.label === '' ? t('minimap.jump') : `${t('minimap.jump')}: ${marker.label}`}
          onClick={() => onJump(marker.id)}
        />
      ))}
    </div>
  )
})
