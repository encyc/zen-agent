/** The slim per-pane composer: prompt or slash command into this pane's
 * session, Enter sends (the shared enterToSend preference), Esc stops the
 * run. Images, mentions and the model picker stay on the docked composer —
 * closing the split brings it back for the focused session. */

import { useSettingsStore } from '../../state/stores'
import { usePanesStore, type SessionPane } from '../../state/sessionPanes'
import { useT } from '../../i18n/locale'
import css from './panes.module.css'

export function PaneComposer({ pane }: { pane: SessionPane }) {
  const t = useT()
  const enterToSend = useSettingsStore((state) => state.enterToSend)
  const setDraft = usePanesStore((state) => state.setDraft)
  const submit = usePanesStore((state) => state.submit)
  const cancel = usePanesStore((state) => state.cancel)

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      cancel(pane.path)
      return
    }
    if (event.key !== 'Enter' || event.shiftKey || !enterToSend) return
    event.preventDefault()
    void submit(pane.path)
  }

  return (
    <div className={css.composer} data-pane-composer-host>
      <textarea
        className={css.composerInput}
        data-pane-composer
        aria-label={t('panes.composerPlaceholder')}
        placeholder={t('panes.composerPlaceholder')}
        rows={2}
        value={pane.draft}
        spellCheck={false}
        onChange={(event) => setDraft(pane.path, event.target.value)}
        onKeyDown={onKeyDown}
      />
    </div>
  )
}
