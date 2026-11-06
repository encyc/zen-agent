/** User bubble (right-aligned, dsh bubble tokens) and the /skill command turn. */

import { useT } from '../../i18n/locale'
import { TurnActions } from './TurnActions'
import css from './MessageItem.module.css'

export function UserMessage({ text, steering = false }: { text: string; steering?: boolean }) {
  const t = useT()
  return (
    <div className={css.userRow} data-turn-row>
      {steering && <span className={css.steerChip}>{t('message.steering')}</span>}
      <div className={css.bubble} data-selectable data-steering={steering || undefined}>
        {text}
      </div>
      <TurnActions text={text} align="end" />
    </div>
  )
}

export function SkillMessage({
  name,
  args,
  body,
}: {
  name: string
  args: string | null
  body: string | null
}) {
  return (
    <div className={css.userRow}>
      <div className={css.skillCard} data-selectable>
        <div className={css.skillHead}>
          <span className={css.skillName}>{name}</span>
          {args !== null && args !== '' && (
            <span className={css.skillArgs}>{args}</span>
          )}
        </div>
        {body !== null && <pre className={css.skillBody}>{body}</pre>}
      </div>
    </div>
  )
}

