/** MCP management: the merged global/project registry, temporary connection
 * probes, and add/remove that write straight into the chosen mcp.json layer.
 * Listing never returns credentials — only the key names callers need. */

import { useEffect, useState, type FormEvent } from 'react'
import clsx from 'clsx'
import { useAppStore, type McpLayer, type McpServerInput } from '../../state/appStore'
import { useMcpStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import type { McpServerRow } from '../../rpc/types'
import { PlusIcon, RefreshIcon } from '../icons'
import css from './McpSettings.module.css'

type Feedback =
  | { kind: 'testing' }
  | { kind: 'ok'; tools: string[] }
  | { kind: 'failed'; message: string }

function messageOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** `KEY=value` per line; blank lines are ignored. */
function parsePairs(text: string): Record<string, string> | undefined {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
  if (lines.length === 0) return undefined
  const pairs: Record<string, string> = {}
  for (const line of lines) {
    const at = line.indexOf('=')
    if (at <= 0) throw new Error(line)
    pairs[line.slice(0, at).trim()] = line.slice(at + 1).trim()
  }
  return pairs
}

function McpRow({ row }: { row: McpServerRow }) {
  const t = useT()
  const removeMcpServer = useAppStore((state) => state.removeMcpServer)
  const testMcpServer = useAppStore((state) => state.testMcpServer)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busy, setBusy] = useState(false)
  const [armed, setArmed] = useState(false)
  const [error, setError] = useState('')

  async function test() {
    if (busy) return
    setBusy(true)
    setError('')
    setFeedback({ kind: 'testing' })
    try {
      const frame = await testMcpServer(row.name)
      if (frame.type === 'result' && frame.ok) {
        if (frame.connected === true) {
          setFeedback({ kind: 'ok', tools: (frame.tools ?? []) as string[] })
        } else {
          setFeedback({ kind: 'failed', message: messageOf(frame.message) })
        }
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

  async function remove() {
    if (busy) return
    if (!armed) {
      setArmed(true)
      return
    }
    setBusy(true)
    setError('')
    try {
      const frame = await removeMcpServer(row.name, row.source)
      if (!(frame.type === 'result' && frame.ok)) {
        setError(messageOf(frame.message) || t('settings.mcpRemoveFailed'))
        setArmed(false)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setArmed(false)
    } finally {
      setBusy(false)
    }
  }

  const endpoint = row.transport === 'http' ? row.url ?? '' : row.command ?? ''
  const stateLabel =
    row.state === 'connected'
      ? t('settings.mcpStateConnected', { count: row.tools.length })
      : row.state === 'failed'
        ? t('settings.mcpStateFailed')
        : t('settings.mcpStateOff')

  return (
    <div className={css.server} data-server={row.name} data-state={row.state}>
      <span className={css.monogram} aria-hidden>
        {row.transport === 'http' ? 'H' : 'S'}
      </span>
      <div className={css.copy}>
        <div className={css.name}>
          <span>{row.name}</span>
          <span className={css.transport}>{row.transport}</span>
          <span className={css.source}>
            {t(row.source === 'project' ? 'settings.mcpProject' : 'settings.mcpGlobal')}
          </span>
          {row.overrides_global ? (
            <span className={css.override}>{t('settings.mcpOverrides')}</span>
          ) : null}
        </div>
        <div className={css.endpoint} title={endpoint}>
          {endpoint}
        </div>
        <div className={css.meta}>
          <span className={clsx(css.state, row.state === 'connected' && css.stateOn)}>
            {stateLabel}
          </span>
          {row.env_keys.length > 0 ? (
            <span className={css.keys}>
              {t('settings.mcpEnvKeys', { keys: row.env_keys.join(', ') })}
            </span>
          ) : null}
          {row.header_keys.length > 0 ? (
            <span className={css.keys}>
              {t('settings.mcpHeaderKeys', { keys: row.header_keys.join(', ') })}
            </span>
          ) : null}
          {row.state === 'failed' && row.error ? (
            <span className={css.stateError}>{row.error}</span>
          ) : null}
        </div>
      </div>
      <div className={css.actions}>
        <button type="button" className={css.action} disabled={busy} onClick={() => void test()}>
          {feedback?.kind === 'testing' ? t('settings.testing') : t('settings.testConnection')}
        </button>
        <button
          type="button"
          className={clsx(css.action, armed && css.actionDanger)}
          disabled={busy}
          onClick={() => void remove()}
        >
          {armed ? t('settings.mcpRemoveArmed') : t('settings.remove')}
        </button>
      </div>
      {feedback?.kind === 'ok' ? (
        <div className={css.ok}>
          {feedback.tools.length > 0
            ? t('settings.mcpTools', { count: feedback.tools.length, names: feedback.tools.join(', ') })
            : t('settings.connected')}
        </div>
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

function AddServer({ onDone }: { onDone(): void }) {
  const t = useT()
  const addMcpServer = useAppStore((state) => state.addMcpServer)
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [url, setUrl] = useState('')
  const [env, setEnv] = useState('')
  const [headers, setHeaders] = useState('')
  const [layer, setLayer] = useState<McpLayer>('global')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const trimmed = name.trim()
    const endpoint = transport === 'http' ? url.trim() : command.trim()
    if (trimmed === '' || endpoint === '') return
    let pairs: Record<string, string> | undefined
    try {
      pairs = parsePairs(transport === 'http' ? headers : env)
    } catch (cause) {
      setError(t('settings.mcpPairError', { line: String(cause instanceof Error ? cause.message : cause) }))
      return
    }
    const input: McpServerInput = { name: trimmed, transport, layer }
    if (transport === 'http') {
      input.url = endpoint
      if (pairs !== undefined) input.headers = pairs
    } else {
      input.command = endpoint
      if (pairs !== undefined) input.env = pairs
    }
    setBusy(true)
    setError('')
    try {
      const frame = await addMcpServer(input)
      if (frame.type === 'result' && frame.ok) {
        onDone()
      } else {
        setError(messageOf(frame.message) || t('settings.mcpAddFailed'))
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className={css.form} onSubmit={save} data-mcp-form>
      <div className={css.field}>
        <label className={css.label} htmlFor="mcp-name">
          {t('settings.mcpName')}
        </label>
        <input
          id="mcp-name"
          className={css.input}
          value={name}
          placeholder="filesystem"
          autoFocus
          spellCheck={false}
          onChange={(event) => setName(event.target.value)}
        />
      </div>

      <div className={css.field}>
        <span className={css.label}>{t('settings.mcpTransport')}</span>
        <div className={css.segment} role="group" aria-label={t('settings.mcpTransport')}>
          {(['stdio', 'http'] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              className={css.segmentItem}
              aria-pressed={transport === kind}
              onClick={() => setTransport(kind)}
            >
              {kind.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {transport === 'stdio' ? (
        <>
          <div className={css.field}>
            <label className={css.label} htmlFor="mcp-command">
              {t('settings.mcpCommand')}
            </label>
            <input
              id="mcp-command"
              className={css.input}
              value={command}
              placeholder="npx -y @modelcontextprotocol/server-filesystem"
              spellCheck={false}
              onChange={(event) => setCommand(event.target.value)}
            />
          </div>
          <div className={css.field}>
            <label className={css.label} htmlFor="mcp-env">
              {t('settings.mcpEnv')}
            </label>
            <textarea
              id="mcp-env"
              className={css.textarea}
              value={env}
              placeholder={'API_KEY=…'}
              spellCheck={false}
              onChange={(event) => setEnv(event.target.value)}
            />
          </div>
        </>
      ) : (
        <>
          <div className={css.field}>
            <label className={css.label} htmlFor="mcp-url">
              {t('settings.mcpUrl')}
            </label>
            <input
              id="mcp-url"
              className={css.input}
              value={url}
              placeholder="https://example.com/mcp"
              spellCheck={false}
              onChange={(event) => setUrl(event.target.value)}
            />
          </div>
          <div className={css.field}>
            <label className={css.label} htmlFor="mcp-headers">
              {t('settings.mcpHeaders')}
            </label>
            <textarea
              id="mcp-headers"
              className={css.textarea}
              value={headers}
              placeholder={'Authorization=Bearer …'}
              spellCheck={false}
              onChange={(event) => setHeaders(event.target.value)}
            />
          </div>
        </>
      )}

      <div className={css.field}>
        <span className={css.label}>{t('settings.mcpLayer')}</span>
        <div className={css.segment} role="group" aria-label={t('settings.mcpLayer')}>
          {(['global', 'project'] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              className={css.segmentItem}
              aria-pressed={layer === kind}
              onClick={() => setLayer(kind)}
            >
              {t(kind === 'global' ? 'settings.mcpGlobal' : 'settings.mcpProject')}
            </button>
          ))}
        </div>
        <div className={css.hint}>{t('settings.mcpLayerHint')}</div>
      </div>

      {layer === 'project' ? (
        <div className={css.warning}>{t('settings.mcpProjectWarning')}</div>
      ) : null}
      {error ? <div className={css.error}>{error}</div> : null}

      <div className={css.formActions}>
        <button type="submit" className={css.primary} disabled={busy || name.trim() === ''}>
          {t('settings.mcpSave')}
        </button>
        <button type="button" className={css.action} onClick={onDone}>
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}

export function McpSettings() {
  const t = useT()
  const servers = useMcpStore((state) => state.servers)
  const problem = useMcpStore((state) => state.problem)
  const refreshMcp = useAppStore((state) => state.refreshMcp)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void refreshMcp()
  }, [refreshMcp])

  const refresh = async () => {
    if (busy) return
    setBusy(true)
    try {
      await refreshMcp()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.stack}>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div className={css.headCopy}>
            <h2 className={css.cardHeading}>{t('settings.mcpServers')}</h2>
            <p className={css.cardCaption}>{t('settings.mcpServersCaption')}</p>
          </div>
          <div className={css.headActions}>
            <button type="button" className={css.action} disabled={busy} onClick={() => void refresh()}>
              <RefreshIcon size={12} />
              <span>{t('settings.mcpRefresh')}</span>
            </button>
            <button type="button" className={css.addButton} onClick={() => setAdding(true)}>
              <PlusIcon size={12} />
              <span>{t('settings.mcpAdd')}</span>
            </button>
          </div>
        </div>

        {problem !== null ? (
          <div className={css.problem}>{t('settings.mcpProblem', { message: problem })}</div>
        ) : null}

        {adding ? <AddServer onDone={() => setAdding(false)} /> : null}

        <div className={css.panel}>
          {servers.length === 0 ? (
            <div className={css.empty}>{t('settings.mcpEmpty')}</div>
          ) : (
            servers.map((row) => <McpRow key={`${row.source}:${row.name}`} row={row} />)
          )}
        </div>
      </section>
    </div>
  )
}
