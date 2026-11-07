/** Per-provider API key management. Keys are write-only: the server reports
 * whether one exists, never the value, so the input is always empty on open. */

import { useEffect, useState, type FormEvent } from 'react'
import clsx from 'clsx'
import { useAppStore } from '../../state/appStore'
import { useSecretsStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import type { SecretProviderRow } from '../../rpc/types'
import css from './ModelSettings.module.css'

type Scope = 'model' | 'search'

type Feedback =
  | { kind: 'testing' }
  | { kind: 'ok' }
  | { kind: 'failed'; message: string }

function messageOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function ProviderRow({ row, scope }: { row: SecretProviderRow; scope: Scope }) {
  const t = useT()
  const saveSecret = useAppStore((state) => state.saveSecret)
  const clearSecret = useAppStore((state) => state.clearSecret)
  const testSecret = useAppStore((state) => state.testSecret)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState<Feedback | null>(null)

  function beginEdit() {
    setValue('')
    setError('')
    setEditing(true)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    const key = value.trim()
    if (!key || busy) return
    setBusy(true)
    setError('')
    try {
      const frame = await saveSecret(row.env, key)
      if (frame.type === 'result' && frame.ok) {
        setEditing(false)
        setValue('')
        setFeedback(null)
      } else {
        setError(messageOf(frame.message) || t('settings.connectionFailed', { message: '' }))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function clear() {
    if (busy) return
    setBusy(true)
    setError('')
    setFeedback(null)
    try {
      await clearSecret(row.env)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    if (busy) return
    setBusy(true)
    setError('')
    setFeedback({ kind: 'testing' })
    try {
      const frame = await testSecret(scope, row.id)
      if (frame.type === 'result' && frame.ok && frame.connected === true) {
        setFeedback({ kind: 'ok' })
      } else {
        setFeedback({ kind: 'failed', message: messageOf(frame.message) })
      }
    } catch (cause) {
      setFeedback({
        kind: 'failed',
        message: cause instanceof Error ? cause.message : String(cause),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className={css.provider}
      data-provider={row.id}
      data-scope={scope}
      data-configured={row.configured || undefined}
    >
      <span className={css.monogram} aria-hidden>
        {row.name.trim().slice(0, 1).toUpperCase()}
      </span>
      <div className={css.copy}>
        <div className={css.name}>
          {row.name}
          {row.current ? (
            <span className={css.current}>{t('settings.currentProvider')}</span>
          ) : null}
        </div>
        <div className={css.meta}>
          <span>
            {scope === 'model'
              ? t('settings.modelCount', { count: row.models ?? 0 })
              : row.env}
          </span>
          <span className={clsx(css.status, row.configured && css.statusOn)}>
            {row.configured ? t('settings.configured') : t('settings.notConfigured')}
          </span>
          {row.configured && row.source === 'env' ? (
            <span className={css.source}>{t('settings.envSource')}</span>
          ) : null}
        </div>
      </div>
      <div className={css.actions}>
        <button type="button" className={css.action} onClick={beginEdit}>
          {row.configured ? t('settings.changeKey') : t('settings.setKey')}
        </button>
        {row.configured ? (
          <button type="button" className={css.action} onClick={() => void clear()}>
            {t('settings.clearKey')}
          </button>
        ) : null}
        <button
          type="button"
          className={css.action}
          disabled={!row.configured || busy}
          onClick={() => void test()}
        >
          {feedback?.kind === 'testing'
            ? t('settings.testing')
            : t('settings.testConnection')}
        </button>
      </div>
      {editing ? (
        <form className={css.editor} onSubmit={save}>
          <input
            className={css.input}
            type="password"
            value={value}
            placeholder={t('settings.keyPlaceholder')}
            aria-label={t('settings.setKey')}
            autoFocus
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setValue(event.target.value)}
          />
          <button type="submit" className={css.primary} disabled={busy || !value.trim()}>
            {t('settings.saveKey')}
          </button>
          <button
            type="button"
            className={css.action}
            onClick={() => {
              setEditing(false)
              setValue('')
              setError('')
            }}
          >
            {t('settings.cancelKey')}
          </button>
          <div className={css.hint}>{t('settings.keyHint')}</div>
        </form>
      ) : null}
      {feedback?.kind === 'ok' ? (
        <div className={css.ok}>{t('settings.connected')}</div>
      ) : null}
      {feedback?.kind === 'failed' ? (
        <div className={css.error}>
          {t('settings.connectionFailed', { message: feedback.message })}
        </div>
      ) : null}
      {error ? <div className={css.error}>{error}</div> : null}
    </div>
  )
}

export function ModelSettings() {
  const t = useT()
  const modelProviders = useSecretsStore((state) => state.modelProviders)
  const searchProviders = useSecretsStore((state) => state.searchProviders)
  const refreshSecrets = useAppStore((state) => state.refreshSecrets)

  useEffect(() => {
    void refreshSecrets()
  }, [refreshSecrets])

  return (
    <div className={css.stack}>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <h2 className={css.cardHeading}>{t('settings.modelProviders')}</h2>
          <p className={css.cardCaption}>{t('settings.modelProvidersCaption')}</p>
        </div>
        <div className={css.panel}>
          {modelProviders.map((row) => (
            <ProviderRow key={row.id} row={row} scope="model" />
          ))}
        </div>
      </section>

      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <h2 className={css.cardHeading}>{t('settings.searchProviders')}</h2>
          <p className={css.cardCaption}>{t('settings.searchProvidersCaption')}</p>
        </div>
        <div className={css.panel}>
          {searchProviders.map((row) => (
            <ProviderRow key={row.id} row={row} scope="search" />
          ))}
        </div>
      </section>
    </div>
  )
}
