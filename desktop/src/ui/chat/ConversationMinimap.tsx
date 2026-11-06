/** Codex-style conversation rail: one dash per user turn in the whole loaded
 * session, clicked from the transcript's left gutter to jump back to it. */

import { memo, type CSSProperties } from 'react'
import type { UserNode } from '../../state/chatStore'
import { useT } from '../../i18n/locale'
import css from './ConversationMinimap.module.css'

export interface ConversationMinimapProps {
  users: UserNode[]
  onJump(id: string): void
  activeId: string | null
}

export const ConversationMinimap = memo(function ConversationMinimap({
  users,
  onJump,
  activeId,
}: ConversationMinimapProps) {
  const t = useT()
  const markers = users.map((node) => ({ id: node.id, label: node.text.trim().slice(0, 60) }))
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
          data-active={marker.id === activeId || undefined}
          aria-label={marker.label === '' ? t('minimap.jump') : `${t('minimap.jump')}: ${marker.label}`}
          onClick={() => onJump(marker.id)}
        />
      ))}
    </div>
  )
})
