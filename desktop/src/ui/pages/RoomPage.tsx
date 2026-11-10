/** The swarm room. This task ships the empty shell so the mode switch can
 * route; the conversation and roster land in the room task. */

import { useT } from '../../i18n/locale'
import css from './RoomPage.module.css'

export function RoomPage() {
  const t = useT()
  return (
    <div className={css.root} data-swarm-page>
      <div className={css.empty}>{t('room.empty')}</div>
    </div>
  )
}
