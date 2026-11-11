/** The zen surface holds the coding/swarm switcher visible until the zen
 * worktree merges its own page; until then it says exactly that. */

import { useT } from '../../i18n/locale'
import css from './ZenPlaceholder.module.css'

export function ZenPlaceholder() {
  const t = useT()
  return (
    <div className={css.root} data-zen-placeholder>
      <div className={css.empty}>{t('zen.placeholder')}</div>
    </div>
  )
}
