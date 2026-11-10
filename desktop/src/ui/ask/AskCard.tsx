/** The `ask` tool card, mounted above the composer (pi-desktop's AskToolCard):
 * one question at a time with progress dots, option cards, multi-select,
 * a free-text option, and skip/decline/submit. The tool call is blocked while
 * the card is open, so the composer stays mounted and steerable. */

import { useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useAskStore, useSessionStore, type PendingAsk } from '../../state/stores'
import { pendingForProject } from '../../state/pendingProject'
import {
  CUSTOM_OPTION,
  advanceDraft,
  draftAnswers,
  draftStatuses,
  emptyAskDraft,
  openQuestion,
  selectOption,
  setCustomText,
  skipAnswer,
  type AskDraft,
} from '../../state/askDraft'
import { useT } from '../../i18n/locale'
import { Button } from '../primitives'
import { SparkIcon } from '../icons'
import css from './AskCard.module.css'

export function AskCard() {
  const queue = useAskStore((state) => state.queue)
  const project = useAppStore((state) => state.project)
  const session = useSessionStore((state) => state.currentPath)
  const request = pendingForProject(queue, project, session)[0]
  if (request === undefined) return null
  // Keying by requestId keeps resolving/scroll state out of the next ask.
  return <AskCardBody key={request.requestId} request={request} />
}

function AskCardBody({ request }: { request: PendingAsk }) {
  const t = useT()
  const queue = useAskStore((state) => state.queue)
  const activeProject = useAppStore((state) => state.project)
  const activeSession = useSessionStore((state) => state.currentPath)
  const queued = Math.max(
    0,
    pendingForProject(queue, activeProject, activeSession).length - 1,
  )
  const resolveAsk = useAppStore((state) => state.resolveAsk)
  const stored = useAskStore((state) => state.drafts[request.requestId])
  const setStored = useAskStore((state) => state.setDraft)
  const [resolving, setResolving] = useState(false)

  const draft: AskDraft =
    stored !== undefined && stored.answers.length === request.questions.length
      ? {
          ...stored,
          index: Math.min(
            Math.max(0, stored.index),
            request.questions.length - 1,
          ),
        }
      : emptyAskDraft(request.questions)

  const index = draft.index
  const current = request.questions[index]
  const currentDraft = draft.answers[index]
  const statuses = draftStatuses(draft)
  const updateDraft = (next: AskDraft) => setStored(request.requestId, next)

  if (current === undefined || currentDraft === undefined) return null

  const submit = (answers: (string[] | null)[]) => {
    if (resolving) return
    setResolving(true)
    void resolveAsk(request.requestId, answers)
  }

  const goNext = () => {
    if (index === request.questions.length - 1) {
      submit(draftAnswers(draft))
      return
    }
    updateDraft(advanceDraft(draft))
  }

  const skip = () => {
    const skipped = skipAnswer(draft, index)
    if (index === request.questions.length - 1) {
      submit(draftAnswers(skipped))
      return
    }
    updateDraft({ ...skipped, index: index + 1 })
  }

  return (
    <div className={css.root}>
      <section className={css.card} role="region" aria-label={t('ask.title')} data-testid="ask-card">
        <header className={css.strip}>
          <span className={css.stripIcon} aria-hidden>
            <SparkIcon size={12} />
          </span>
          <span className={css.title} role="status" aria-live="polite">
            {t('ask.title')}
          </span>
          <span className={css.progress}>
            {t('ask.progress', {
              current: index + 1,
              total: request.questions.length,
            })}
            {queued > 0 ? <span> · {t('ask.queued', { count: queued })}</span> : null}
          </span>
          {request.questions.length > 1 && (
            <div className={css.indicators} aria-label={t('ask.indicatorLabel')}>
              {statuses.map((status, position) => (
                <button
                  key={position}
                  type="button"
                  className={css.indicator}
                  data-status={status}
                  data-current={position === index || undefined}
                  aria-label={t(`ask.status.${status}`, { number: position + 1 })}
                  aria-current={position === index ? 'step' : undefined}
                  onClick={() => updateDraft(openQuestion(draft, position))}
                />
              ))}
            </div>
          )}
        </header>

        <div className={css.body}>
          <div className={css.questionBlock}>
            {current.header !== undefined && (
              <span className={css.questionHeader}>{current.header}</span>
            )}
            <h3 className={css.question}>
              <span className={css.questionBody} tabIndex={0}>
                {current.question}
              </span>
            </h3>
          </div>

          <div
            className={css.options}
            role={current.multi ? 'group' : 'radiogroup'}
            aria-label={current.question}
          >
            {current.options.map((option, position) => {
              const selected = currentDraft.values.includes(option.label)
              const recommended = option.label === current.recommended
              return (
                <button
                  key={`${option.label}-${position}`}
                  type="button"
                  className={css.option}
                  data-selected={selected || undefined}
                  role={current.multi ? 'checkbox' : 'radio'}
                  aria-checked={current.multi ? undefined : selected}
                  aria-pressed={current.multi ? selected : undefined}
                  onClick={() =>
                    updateDraft(
                      selectOption(draft, index, option.label, current.multi),
                    )
                  }
                >
                  <span className={css.optionMark} aria-hidden>
                    {selected ? '✓' : ''}
                  </span>
                  <span className={css.optionCopy}>
                    <span className={css.optionLabel}>
                      {option.label}
                      {recommended && (
                        <span className={css.recommended}>
                          {t('ask.recommended')}
                        </span>
                      )}
                    </span>
                    {option.description !== undefined && (
                      <span className={css.optionDescription}>
                        {option.description}
                      </span>
                    )}
                  </span>
                </button>
              )
            })}
            <button
              type="button"
              className={css.option}
              data-selected={currentDraft.customSelected || undefined}
              role={current.multi ? 'checkbox' : 'radio'}
              aria-checked={current.multi ? undefined : currentDraft.customSelected}
              aria-pressed={current.multi ? currentDraft.customSelected : undefined}
              onClick={() =>
                updateDraft(selectOption(draft, index, CUSTOM_OPTION, current.multi))
              }
            >
              <span className={css.optionMark} aria-hidden>
                {currentDraft.customSelected ? '✓' : ''}
              </span>
              <span className={css.optionCopy}>{t('ask.customOption')}</span>
            </button>
          </div>

          {currentDraft.customSelected && (
            <input
              className={css.customInput}
              value={currentDraft.customText}
              placeholder={t('ask.customPlaceholder')}
              aria-label={t('ask.customOption')}
              autoFocus
              onChange={(event) =>
                updateDraft(setCustomText(draft, index, event.target.value))
              }
            />
          )}
        </div>

        <footer className={css.footer}>
          <button
            type="button"
            className={css.decline}
            disabled={resolving}
            onClick={() => submit(request.questions.map(() => null))}
          >
            {t('ask.decline')}
          </button>
          <div className={css.footerMain}>
            <Button variant="ghost" disabled={resolving} onClick={skip}>
              {t('ask.skip')}
            </Button>
            <Button variant="primary" disabled={resolving} onClick={goNext}>
              {index === request.questions.length - 1
                ? t('ask.submit')
                : t('ask.next')}
            </Button>
          </div>
        </footer>
      </section>
    </div>
  )
}
