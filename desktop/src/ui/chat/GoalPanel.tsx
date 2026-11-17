/** Goal details and lifecycle actions. Every button is a normal `/goal`
 * command, so the kernel keeps one implementation for TUI and desktop. */

import { useAppStore } from '../../state/appStore'
import { useT } from '../../i18n/locale'
import type { GoalView } from '../../rpc/types'
import css from './GoalPanel.module.css'

export function GoalPanel(props: { view: GoalView; onClose(): void }) {
  const t = useT()
  const runCommand = useAppStore((state) => state.runCommand)

  function act(argument: 'pause' | 'resume' | 'clear') {
    void runCommand('goal', argument).then(props.onClose)
  }

  return (
    <section className={css.panel} aria-label={t('goal.panel.title')}>
      <header className={css.head}>
        <strong>{t('goal.panel.title')}</strong>
        <button type="button" className={css.close} onClick={props.onClose}>
          {t('goal.panel.close')}
        </button>
      </header>
      <p className={css.objective}>{props.view.objective}</p>
      {props.view.check !== null && (
        <p className={css.meta}>
          {t('goal.panel.check')}: <code>{props.view.check}</code>
        </p>
      )}
      <p className={css.meta}>
        {t('goal.panel.budget', {
          turns: props.view.turns_used,
          maxTurns: props.view.max_turns,
          tokens: props.view.tokens_used,
          maxTokens: props.view.max_tokens,
        })}
      </p>
      <p className={css.meta}>
        {t('goal.panel.last', {
          verdict: props.view.last_verdict ?? t('goal.panel.none'),
          reason: props.view.last_reason || t('goal.panel.none'),
        })}
      </p>
      <div className={css.actions}>
        <button
          type="button"
          className={css.action}
          disabled={props.view.status !== 'active'}
          onClick={() => act('pause')}
        >
          {t('goal.panel.pause')}
        </button>
        <button
          type="button"
          className={css.action}
          disabled={
            props.view.status !== 'paused' && props.view.status !== 'blocked'
          }
          onClick={() => act('resume')}
        >
          {t('goal.panel.resume')}
        </button>
        <button type="button" className={css.action} onClick={() => act('clear')}>
          {t('goal.panel.clear')}
        </button>
      </div>
    </section>
  )
}
