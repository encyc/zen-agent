/** The slim per-pane composer: prompt or slash command into this pane's
 * session, with the dock's keyboard contract (Enter submits when the setting
 * allows it, ⌘/Ctrl+Enter always does), a visible send button, and Esc to
 * stop the pane's run. Images, mentions and the model picker stay on the
 * docked composer — closing the split brings it back for the focused session. */

import { useSettingsStore } from '../../state/stores'
import { usePanesStore, type SessionPane } from '../../state/sessionPanes'
import { isSubmitKey } from '../composer/ComposerPlate'
import { useT } from '../../i18n/locale'
import { SendArrowUpIcon, StopIcon } from '../icons'
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
      if (pane.busy) cancel(pane.path)
      return
    }
    if (!isSubmitKey(event, enterToSend)) return
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
      <button
        type="button"
        className={css.sendButton}
        data-pane-send
        aria-label={t('panes.composerPlaceholder')}
        disabled={pane.draft.trim() === ''}
        onClick={() => void submit(pane.path)}
      >
        <SendArrowUpIcon size={14} />
      </button>
      {pane.busy && (
        <button
          type="button"
          className={css.action}
          data-pane-stop
          aria-label={t('panes.stop')}
          onClick={() => cancel(pane.path)}
        >
          <StopIcon size={12} />
        </button>
      )}
    </div>
  )
}
