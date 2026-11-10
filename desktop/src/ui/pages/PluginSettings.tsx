/** Settings → Plugins: the official connector catalog, the installed list,
 * and a credential drawer. Rows never carry credential values — the drawer
 * writes them straight into the host's settings store over RPC. */

import { useEffect, useState, type FormEvent } from 'react'
import clsx from 'clsx'
import { useLocale, useT } from '../../i18n/locale'
import type { Locale } from '../../i18n/locale'
import { frameErrorNotice } from '../../rpc/frames'
import type {
  PluginCatalogRow,
  PluginRow,
  PluginsResult,
  PluginServerRow,
} from '../../rpc/types'
import {
  configurePlugin,
  installPlugin,
  listPlugins,
  setPluginEnabled,
  uninstallPlugin,
} from '../../state/pluginApi'
import { newlyInstalledId } from '../../state/pluginInstall'
import { usePluginStore } from '../../state/stores'
import { RefreshIcon } from '../icons'
import css from './PluginSettings.module.css'

const STATUS_KEYS: Record<PluginRow['status'] | PluginServerRow['state'], string> = {
  disabled: 'settings.pluginsStatusDisabled',
  needs_credentials: 'settings.pluginsStatusNeedsCredentials',
  failed: 'settings.pluginsStatusFailed',
  connected: 'settings.pluginsStatusConnected',
  ready: 'settings.pluginsStatusReady',
  off: 'settings.pluginsStatusOff',
}

function frameMessage(frame: unknown): string {
  const message = (frame as { message?: unknown }).message
  return typeof message === 'string' ? message : ''
}

function noticeOf(frame: PluginsResult, locale: Locale, fallback: string): string {
  return frameErrorNotice(frame, locale) ?? (frameMessage(frame) || fallback)
}

function InstallButton(props: { entry: PluginCatalogRow; busy: boolean; onInstall(): void }) {
  const t = useT()
  if (props.entry.installed) {
    return <span className={css.installedBadge}>{t('settings.pluginsInstalledBadge')}</span>
  }
  return (
    <button type="button" className={css.primary} disabled={props.busy} onClick={props.onInstall}>
      {t('settings.pluginsInstall')}
    </button>
  )
}

function PluginRowView(props: {
  row: PluginRow
  busy: boolean
  onConfigure(): void
  onToggle(enabled: boolean): void
  onUninstall(): void
}) {
  const t = useT()
  const [armed, setArmed] = useState(false)
  const { row } = props

  return (
    <div className={css.plugin} data-plugin={row.id} data-status={row.status}>
      <div className={css.pluginMain}>
        <div className={css.name}>
          <span>{row.name}</span>
          <span className={css.version}>
            {t('settings.pluginsVersion', { version: row.version })}
          </span>
          <span className={css.status} data-status={row.status}>
            {t(STATUS_KEYS[row.status])}
          </span>
        </div>
        <div className={css.description}>{row.description}</div>
        <div className={css.meta}>
          {row.servers.map((server) => (
            <span key={server.name} className={css.server}>
              {server.name} · {t(STATUS_KEYS[server.state])}
              {server.state === 'connected' && server.tools.length > 0
                ? ` · ${t('settings.pluginsTools', { count: server.tools.length })}`
                : ''}
            </span>
          ))}
          {row.servers.map((server) =>
            server.state === 'failed' && server.error ? (
              <span key={`${server.name}:error`} className={css.serverError}>
                {t('settings.pluginsFailedSummary', { message: server.error })}
              </span>
            ) : null,
          )}
        </div>
      </div>
      <div className={css.actions}>
        <button type="button" className={css.action} disabled={props.busy} onClick={props.onConfigure}>
          {t('settings.pluginsConfigure')}
        </button>
        <button
          type="button"
          className={clsx(css.action, armed && css.actionDanger)}
          disabled={props.busy}
          onClick={() => {
            if (!armed) {
              setArmed(true)
              return
            }
            setArmed(false)
            props.onUninstall()
          }}
        >
          {armed ? t('settings.pluginsUninstallArmed') : t('settings.pluginsUninstall')}
        </button>
        <button
          type="button"
          role="switch"
          aria-checked={row.enabled}
          aria-label={t(row.enabled ? 'settings.pluginsDisable' : 'settings.pluginsEnable')}
          className={css.switch}
          disabled={props.busy}
          onClick={() => props.onToggle(!row.enabled)}
        >
          <span className={css.switchThumb} />
        </button>
      </div>
      {armed ? <div className={css.warning}>{t('settings.pluginsUninstallWarning')}</div> : null}
    </div>
  )
}

function CredentialDrawer(props: {
  row: PluginRow
  busy: boolean
  onClose(): void
  onSaved(frame: PluginsResult): void
}) {
  const t = useT()
  const locale = useLocale()
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function save(event: FormEvent) {
    event.preventDefault()
    if (saving) return
    const filled = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value !== ''),
    )
    if (Object.keys(filled).length === 0) {
      props.onClose()
      return
    }
    setSaving(true)
    setError('')
    try {
      const frame = await configurePlugin(props.row.id, filled)
      if (frame.type === 'result' && frame.ok) {
        props.onSaved(frame)
        props.onClose()
      } else {
        setError(noticeOf(frame, locale, t('settings.pluginsConfigureFailed', { message: '' })))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className={css.drawer} onSubmit={save} data-plugin-drawer={props.row.id}>
      <div className={css.drawerHead}>
        <h3 className={css.drawerTitle}>
          {t('settings.pluginsConfigureTitle', { name: props.row.name })}
        </h3>
        <button type="button" className={css.action} onClick={props.onClose}>
          {t('common.cancel')}
        </button>
      </div>
      <p className={css.hint}>{t('settings.pluginsConfigureHint')}</p>
      {props.row.credentials.map((spec) => (
        <div className={css.field} key={spec.key}>
          <label className={css.label} htmlFor={`plugin-${props.row.id}-${spec.key}`}>
            {spec.label}
          </label>
          <input
            id={`plugin-${props.row.id}-${spec.key}`}
            className={css.input}
            type={spec.secret ? 'password' : 'text'}
            value={values[spec.key] ?? ''}
            placeholder={
              spec.set
                ? t('settings.pluginsCredentialKeep')
                : t('settings.pluginsCredentialPlaceholder')
            }
            autoComplete="off"
            spellCheck={false}
            onChange={(event) =>
              setValues((current) => ({ ...current, [spec.key]: event.target.value }))
            }
          />
          <div className={css.fieldMeta}>
            {spec.set ? (
              <span className={css.set}>{t('settings.pluginsCredentialSet')}</span>
            ) : null}
            {spec.url ? (
              <a className={css.link} href={spec.url} target="_blank" rel="noreferrer">
                {t('settings.pluginsCredentialHelp')}
              </a>
            ) : null}
          </div>
          {spec.help ? <div className={css.hint}>{spec.help}</div> : null}
        </div>
      ))}
      {error ? <div className={css.error}>{error}</div> : null}
      <div className={css.formActions}>
        <button type="submit" className={css.primary} disabled={saving || props.busy}>
          {t('settings.pluginsSave')}
        </button>
      </div>
    </form>
  )
}

export function PluginSettings() {
  const t = useT()
  const locale = useLocale()
  const plugins = usePluginStore((state) => state.plugins)
  const catalog = usePluginStore((state) => state.catalog)
  const problem = usePluginStore((state) => state.problem)
  const applyPending = usePluginStore((state) => state.applyPending)
  const setView = usePluginStore((state) => state.setView)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [configuring, setConfiguring] = useState<string | null>(null)
  const [path, setPath] = useState('')
  const busy = pending !== null

  useEffect(() => {
    let alive = true
    listPlugins()
      .then((frame) => {
        if (!alive) return
        if (frame.type === 'result' && frame.ok) {
          setView(
            frame.plugins ?? [],
            frame.catalog ?? [],
            typeof frame.problem === 'string' ? frame.problem : null,
            frame.apply_pending === true,
          )
        } else {
          setError(noticeOf(frame, locale, t('settings.pluginsLoadFailed')))
        }
      })
      .catch((cause) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      alive = false
    }
    // t is recreated per render; the locale and store setter drive the load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setView, locale])

  function adopt(frame: PluginsResult) {
    setView(
      frame.plugins ?? [],
      frame.catalog ?? [],
      typeof frame.problem === 'string' ? frame.problem : null,
      frame.apply_pending === true,
    )
  }

  async function install(source: { id?: string; path?: string }) {
    if (pending !== null) return
    setPending(`install:${source.id ?? source.path ?? ''}`)
    setError('')
    const knownIds = plugins.map((row) => row.id)
    try {
      const frame = await installPlugin(source)
      if (frame.type === 'result' && frame.ok) {
        adopt(frame)
        const installedId = newlyInstalledId(frame, knownIds, source.id)
        const row = (frame.plugins ?? []).find((item) => item.id === installedId)
        if (row?.status === 'needs_credentials') setConfiguring(row.id)
      } else {
        setError(noticeOf(frame, locale, t('settings.pluginsInstallFailed', { message: '' })))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

  async function flip(row: PluginRow, enabled: boolean) {
    if (pending !== null) return
    setPending(`toggle:${row.id}`)
    setError('')
    try {
      const frame = await setPluginEnabled(row.id, enabled)
      if (frame.type === 'result' && frame.ok) adopt(frame)
      else setError(noticeOf(frame, locale, t('settings.pluginsToggleFailed', { message: '' })))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

  async function remove(row: PluginRow) {
    if (pending !== null) return
    setPending(`uninstall:${row.id}`)
    setError('')
    try {
      const frame = await uninstallPlugin(row.id)
      if (frame.type === 'result' && frame.ok) adopt(frame)
      else setError(noticeOf(frame, locale, t('settings.pluginsUninstallFailed', { message: '' })))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

  async function refresh() {
    if (pending !== null) return
    setPending('refresh')
    setError('')
    try {
      const frame = await listPlugins()
      if (frame.type === 'result' && frame.ok) adopt(frame)
      else setError(noticeOf(frame, locale, t('settings.pluginsLoadFailed')))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

  function installFromPath(event: FormEvent) {
    event.preventDefault()
    const target = path.trim()
    if (target === '') return
    void install({ path: target }).then(() => setPath(''))
  }

  const configured = plugins.find((row) => row.id === configuring) ?? null

  return (
    <div className={css.stack}>
      {applyPending ? (
        <div className={css.pending} role="status">
          {t('settings.pluginsApplyPending')}
        </div>
      ) : null}

      {configured ? (
        <CredentialDrawer
          key={configured.id}
          row={configured}
          busy={busy}
          onClose={() => setConfiguring(null)}
          onSaved={adopt}
        />
      ) : null}

      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div className={css.headCopy}>
            <h2 className={css.cardHeading}>{t('settings.pluginsCatalog')}</h2>
            <p className={css.cardCaption}>{t('settings.pluginsCatalogCaption')}</p>
          </div>
          <button type="button" className={css.action} disabled={busy} onClick={() => void refresh()}>
            <RefreshIcon size={12} />
            <span>{t('settings.pluginsRefresh')}</span>
          </button>
        </div>

        {problem !== null ? (
          <div className={css.problem}>{t('settings.pluginsProblem', { message: problem })}</div>
        ) : null}

        <div className={css.cards}>
          {catalog.map((entry) => (
            <article key={entry.id} className={css.catalogCard} data-plugin={entry.id}>
              <div className={css.catalogHead}>
                <span className={css.catalogName}>{entry.name}</span>
                <span className={css.pluginId}>{entry.id}</span>
              </div>
              <p className={css.catalogDescription}>{entry.description}</p>
              {entry.requires_runtime ? (
                <div className={css.hint}>
                  {t('settings.pluginsRequires', { runtime: entry.requires_runtime })}
                </div>
              ) : null}
              <div className={css.catalogAction}>
                <InstallButton
                  entry={entry}
                  busy={busy}
                  onInstall={() => void install({ id: entry.id })}
                />
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <h2 className={css.cardHeading}>{t('settings.pluginsInstalledSection')}</h2>
        </div>
        <div className={css.panel}>
          {plugins.length === 0 ? (
            <div className={css.empty}>{t('settings.pluginsEmpty')}</div>
          ) : (
            plugins.map((row) => (
              <PluginRowView
                key={row.id}
                row={row}
                busy={busy}
                onConfigure={() => setConfiguring(row.id)}
                onToggle={(enabled) => void flip(row, enabled)}
                onUninstall={() => void remove(row)}
              />
            ))
          )}
        </div>
      </section>

      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <h2 className={css.cardHeading}>{t('settings.pluginsInstallPath')}</h2>
        </div>
        <form className={css.form} onSubmit={installFromPath}>
          <div className={css.field}>
            <input
              id="plugin-path"
              className={css.input}
              value={path}
              placeholder={t('settings.pluginsPathPlaceholder')}
              spellCheck={false}
              onChange={(event) => setPath(event.target.value)}
            />
            <div className={css.hint}>{t('settings.pluginsInstallPathHint')}</div>
          </div>
          <div className={css.formActions}>
            <button type="submit" className={css.primary} disabled={busy || path.trim() === ''}>
              {t('settings.pluginsInstallFromPath')}
            </button>
          </div>
        </form>
      </section>

      {error ? <div className={css.error}>{error}</div> : null}
    </div>
  )
}
