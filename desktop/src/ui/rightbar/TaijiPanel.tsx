/** Taiji tab: the dispatch board. The fleet sorted by how much a person has
 * to do — questions first, then acceptance, then work in flight — and every
 * band carries its own actions. Board data comes from the `taiji` rpc ops;
 * actions talk to the supervisor directly (spec 附录 C). */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppStore } from '../../state/appStore'
import { useComposerStore, useNoticeStore, useSettingsStore, useWorkPanelStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { localizeError } from '../../i18n/errors.ts'
import type {
  TaijiAttention,
  TaijiDiffFile,
  TaijiWorker,
} from '../../rpc/types'
import { diffSummary, groupWorkers, statusTone, taskAge, relativeTime } from './taijiBoard'
import { BranchIcon, RefreshIcon } from '../icons'
import css from './TaijiPanel.module.css'

import { useTaijiStore } from '../../state/taijiStore'

const POLL_MS = 2000
const KILL_ARM_MS = 3000


function notice(kind: 'info' | 'error', text: string) {
  useNoticeStore.getState().show({ kind, text })
}

/** A refusal in the person's language: a known code maps to the dictionary,
 * anything else keeps the server's own message. */
function failureReason(reply: { message?: string; code?: string; params?: Record<string, string> }): string {
  return localizeError(reply, useSettingsStore.getState().locale)
}

/** A task age in the interface language; the tick form is digits already. */
function renderAge(
  age: ReturnType<typeof taskAge>,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  if (age.kind === 'tick') return age.text
  if (age.kind === 'hours') return t('panel.taiji.ageHours', { h: age.hours, m: age.minutes })
  return t('panel.taiji.ageDays', { d: age.days })
}

/** Send a human message through the board. `queued` means the worker was
 * mid-turn and the words ride that turn's tail. */
async function deliver(name: string, text: string, t: (key: string, params?: Record<string, string | number>) => string) {
  try {
    const reply = await useTaijiStore.getState().send(name, text)
    if (reply.ok === true) {
      if (reply.queued === true) notice('info', t('panel.taiji.queuedNotice', { name }))
      else notice('info', t('panel.taiji.delivered'))
    } else {
      notice('error', t('panel.taiji.actionFailed', { reason: failureReason(reply) }))
    }
  } catch {
    notice('error', t('panel.taiji.actionFailed', { reason: 'timeout' }))
  }
}

/** One inline text input that submits on Enter and clears itself. */
function InlineInput(props: {
  placeholder: string
  label: string
  onSubmit: (text: string) => void
}) {
  const { placeholder, label, onSubmit } = props
  const inputRef = useRef<HTMLInputElement>(null)
  const submit = () => {
    const text = inputRef.current?.value.trim() ?? ''
    if (!text) return
    if (inputRef.current) inputRef.current.value = ''
    onSubmit(text)
  }
  return (
    <div className={css.inline}>
      <input
        ref={inputRef}
        className={css.inlineInput}
        type="text"
        placeholder={placeholder}
        aria-label={label}
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit()
        }}
      />
      <button type="button" className={css.smallButton} onClick={submit}>
        {label}
      </button>
    </div>
  )
}

function AttentionCard(props: { worker: TaijiWorker }) {
  const { worker } = props
  const t = useT()
  const attention: TaijiAttention = worker.attention ?? { question: '', options: [] }
  const answer = (text: string) => void deliver(worker.name, `${t('taiji.answerPrefix')}${text}`, t)
  const toRouter = () => {
    const text = t('taiji.toRouterPrompt', {
      branch: worker.branch,
      question: attention.question || worker.summary,
      name: worker.name,
    })
    void (async () => {
      try {
        await useAppStore.getState().client().send({ type: 'prompt', text }, 15000)
        notice('info', t('panel.taiji.toRouterNotice'))
      } catch {
        notice('error', t('panel.taiji.actionFailed', { reason: 'router busy' }))
      }
    })()
  }
  return (
    <div className={css.attentionCard} data-band="attention">
      <div className={css.cardName}>
        <span className={css.name}>{worker.name}</span>
        <span className={css.branch}>{worker.branch}</span>
      </div>
      <p className={css.attentionQ}>{attention.question || worker.summary}</p>
      {attention.options.length > 0 && (
        <div className={css.optionRow}>
          {attention.options.map((option, index) => (
            <button
              key={`${index}-${option}`}
              type="button"
              className={css.optionButton}
              onClick={() => answer(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}
      <InlineInput
        placeholder={t('panel.taiji.answerPlaceholder')}
        label={t('panel.taiji.send')}
        onSubmit={answer}
      />
      <button type="button" className={css.smallButton} onClick={toRouter}>
        {t('panel.taiji.toRouter')}
      </button>
    </div>
  )
}

function AcceptanceCard(props: { worker: TaijiWorker }) {
  const { worker } = props
  const t = useT()
  const [rejecting, setRejecting] = useState(false)
  const files: TaijiDiffFile[] = worker.diffstat ?? []
  const summary = diffSummary(worker.diffstat)
  const merge = async () => {
    try {
      const reply = await useTaijiStore.getState().merge(worker.name)
      if (reply.ok === true) notice('info', t('panel.taiji.merged', { branch: worker.branch }))
      else notice('error', t('panel.taiji.mergeFailed', { reason: failureReason(reply) }))
    } catch {
      notice('error', t('panel.taiji.actionFailed', { reason: 'timeout' }))
    }
  }
  return (
    <div className={css.acceptanceCard} data-band="acceptance">
      <div className={css.cardName}>
        <span className={css.name}>{worker.name}</span>
        <span className={css.branch}>{worker.branch}→{worker.base}</span>
        {summary && <span className={css.diff}>{t('panel.taiji.diff', summary)}</span>}
      </div>
      <p className={css.cardSummary}>{worker.summary}</p>
      {files.length > 0 && (
        <details className={css.files}>
          <summary>{t('panel.taiji.files', { count: files.length })}</summary>
          <ul>
            {files.map((file) => (
              <li key={file.path}>
                <span className={css.filePath}>{file.path}</span>
                <span className={css.fileCounts}>
                  +{file.adds} −{file.dels}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className={css.buttonRow}>
        <button type="button" className={css.primaryButton} onClick={() => void merge()}>
          {t('panel.taiji.merge')}
        </button>
        <button type="button" className={css.smallButton} onClick={() => setRejecting((value) => !value)}>
          {t('panel.taiji.reject')}
        </button>
      </div>
      {rejecting && (
        <InlineInput
          placeholder={t('panel.taiji.rejectPlaceholder')}
          label={t('panel.taiji.send')}
          onSubmit={(text) => {
            setRejecting(false)
            void deliver(worker.name, `${t('taiji.bouncePrefix')}${text}`, t)
          }}
        />
      )}
    </div>
  )
}

function RunningRow(props: { worker: TaijiWorker; now: number }) {
  const { worker, now } = props
  const t = useT()
  const [interjecting, setInterjecting] = useState(false)
  const [armed, setArmed] = useState(false)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<{ role: string; text: string }[] | null>(null)
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current)
  }, [])
  const kill = () => {
    if (!armed) {
      setArmed(true)
      armTimer.current = setTimeout(() => setArmed(false), KILL_ARM_MS)
      return
    }
    if (armTimer.current) clearTimeout(armTimer.current)
    setArmed(false)
    void (async () => {
      try {
        await useTaijiStore.getState().kill(worker.name)
        notice('info', t('panel.taiji.killedNotice', { name: worker.name }))
      } catch {
        notice('error', t('panel.taiji.actionFailed', { reason: 'timeout' }))
      }
    })()
  }
  const peek = () => {
    const next = !open
    setOpen(next)
    // Re-read on every open: a running worker's tail goes stale between opens.
    if (next) {
      setItems(null)
      void (async () => {
        try {
          const reply = await useTaijiStore.getState().peek(worker.name, 12)
          setItems(Array.isArray(reply.items) ? (reply.items as { role: string; text: string }[]) : [])
        } catch {
          setItems([])
        }
      })()
    }
  }
  return (
    <div className={css.runningRow} data-band="running">
      <button type="button" className={css.rowMain} onClick={peek} aria-expanded={open}>
        <i className={css.runDot} aria-hidden="true" />
        <span className={css.name}>{worker.name}</span>
        <span className={css.age}>{renderAge(taskAge(worker.created_at, now), t)}</span>
        <span className={css.rowSummary}>{worker.summary}</span>
      </button>
      <span className={css.rowButtons}>
        <button type="button" className={css.smallButton} onClick={() => setInterjecting((value) => !value)}>
          {t('panel.taiji.interject')}
        </button>
        <button type="button" className={armed ? css.killArmed : css.smallButton} onClick={kill}>
          {armed ? t('panel.taiji.killConfirm') : t('panel.taiji.kill')}
        </button>
      </span>
      {interjecting && (
        <InlineInput
          placeholder={t('panel.taiji.interjectPlaceholder', { name: worker.name })}
          label={t('panel.taiji.send')}
          onSubmit={(text) => {
            setInterjecting(false)
            void deliver(worker.name, text, t)
          }}
        />
      )}
      {open && (
        <div className={css.peek} data-selectable>
          {items === null
            ? t('panel.taiji.loading')
            : items.length === 0
              ? t('panel.taiji.peekEmpty')
              : items.map((item, index) => (
                  <p key={index} className={item.role === 'user' ? css.peekUser : css.peekAssistant}>
                    {item.text}
                  </p>
                ))}
        </div>
      )}
    </div>
  )
}

function QueuedRow(props: { worker: TaijiWorker }) {
  const { worker } = props
  const t = useT()
  const cancel = async () => {
    try {
      await useTaijiStore.getState().kill(worker.name)
      notice('info', t('panel.taiji.killedNotice', { name: worker.name }))
    } catch {
      notice('error', t('panel.taiji.actionFailed', { reason: 'timeout' }))
    }
  }
  return (
    <div className={css.queuedRow} data-band="queued">
      <i className={css.queueDot} aria-hidden="true" />
      <span className={css.name}>{worker.name}</span>
      <span className={css.rowSummary}>{worker.summary}</span>
      <span className={css.rowButtons}>
        <button type="button" className={css.smallButton} onClick={() => void cancel()}>
          {t('panel.taiji.cancel')}
        </button>
      </span>
    </div>
  )
}

function HistoryRow(props: { worker: TaijiWorker; now: number }) {
  const { worker, now } = props
  const t = useT()
  const tone = statusTone(worker.status)
  const resumable = (worker.status === 'error' || worker.status === 'interrupted') && worker.session
  const resume = () => void deliver(worker.name, t('taiji.resume'), t)
  return (
    <div className={css.historyRow} data-band="history" data-tone={tone}>
      <i className={css.historyDot} aria-hidden="true" />
      <span className={css.name}>{worker.name}</span>
      <span className={css.rowSummary}>{worker.summary}</span>
      <span className={css.historyMeta}>
        {t(`panel.taiji.status.${worker.status}`)}
        {' · '}
        {relativeTime(worker.updated_at, now)}
      </span>
      {resumable && (
        <span className={css.rowButtons}>
          <button type="button" className={css.smallButton} onClick={resume}>
            {t('panel.taiji.resume')}
          </button>
        </span>
      )}
    </div>
  )
}

function Section(props: { title: string; count: number; children: React.ReactNode }) {
  const { title, count, children } = props
  if (count === 0) return null
  return (
    <section className={css.section}>
      <h3 className={css.sectionTitle}>
        {title}
        <span className={css.sectionCount}>{count}</span>
      </h3>
      {children}
    </section>
  )
}

export function TaijiPanel() {
  const t = useT()
  const board = useTaijiStore((state) => state.board)
  const failed = useTaijiStore((state) => state.failed)
  const [now, setNow] = useState(() => Date.now() / 1000)

  const load = useCallback(async () => {
    await useTaijiStore.getState().refresh()
  }, [])

  useEffect(() => {
    void load()
    const poll = setInterval(() => void load(), POLL_MS)
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => {
      clearInterval(poll)
      clearInterval(tick)
    }
  }, [load])

  if (failed)
    return (
      <div className={css.state}>
        <BranchIcon size={16} />
        <p>{t('panel.taiji.error')}</p>
        <button type="button" className={css.smallButton} onClick={() => void load()}>
          {t('panel.taiji.refresh')}
        </button>
      </div>
    )
  if (board === null)
    return (
      <div className={css.state}>
        <span className={css.stateSpin} />
        <p>{t('panel.taiji.loading')}</p>
      </div>
    )
  if (!board.active)
    return (
      <div className={css.state}>
        <BranchIcon size={16} />
        <p>{t('panel.taiji.inactive')}</p>
      </div>
    )

  const groups = groupWorkers(board.workers)
  const slots = board.slots

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>{t('panel.taiji')}</span>
        {slots && (
          <span className={css.slots} title={t('panel.taiji.slotsTitle')}>
            {slots.busy}/{slots.max}
            {slots.queued > 0 && <span className={css.slotsQueued}>+{slots.queued}</span>}
          </span>
        )}
        <button
          type="button"
          className={css.routerState}
          data-busy={board.router_busy || undefined}
          title={t('panel.taiji.routerFocus')}
          onClick={() => useWorkPanelStore.getState().closePanel()}
        >
          {board.router_busy ? t('panel.taiji.routerBusy') : t('panel.taiji.routerIdle')}
        </button>
        <button
          type="button"
          className={css.iconButton}
          aria-label={t('panel.taiji.refresh')}
          title={t('panel.taiji.refresh')}
          onClick={() => void load()}
        >
          <RefreshIcon size={13} />
        </button>
      </div>
      {board.workers.length === 0 ? (
        <div className={css.emptyCard} data-selectable>
          <BranchIcon size={20} />
          <p className={css.emptyTitle}>{t('panel.taiji.emptyTitle')}</p>
          <p className={css.emptyBody}>{t('panel.taiji.emptyBody')}</p>
          <button
            type="button"
            className={css.smallButton}
            onClick={() => {
              useComposerStore.getState().setDraft(t('panel.taiji.sampleTask'))
            }}
          >
            {t('panel.taiji.emptyTry')}
          </button>
        </div>
      ) : (
        <div className={css.bands}>
          <Section title={t('panel.taiji.section.attention')} count={groups.attention.length}>
            {groups.attention.map((worker) => (
              <AttentionCard key={worker.name} worker={worker} />
            ))}
          </Section>
          <Section title={t('panel.taiji.section.acceptance')} count={groups.acceptance.length}>
            {groups.acceptance.map((worker) => (
              <AcceptanceCard key={worker.name} worker={worker} />
            ))}
          </Section>
          <Section title={t('panel.taiji.section.running')} count={groups.running.length}>
            {groups.running.map((worker) => (
              <RunningRow key={worker.name} worker={worker} now={now} />
            ))}
          </Section>
          <Section title={t('panel.taiji.section.queued')} count={groups.queued.length}>
            {groups.queued.map((worker) => (
              <QueuedRow key={worker.name} worker={worker} />
            ))}
          </Section>
          <Section title={t('panel.taiji.section.history')} count={groups.history.length}>
            {groups.history.map((worker) => (
              <HistoryRow key={worker.name} worker={worker} now={now} />
            ))}
          </Section>
        </div>
      )}
    </div>
  )
}
