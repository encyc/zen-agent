/** The global role library as a copy source: each project owns its own
 * swarm roles, so the room (or Settings → Swarm) copies one over instead of
 * editing the shared template in place. */

import { useEffect, useMemo, useState } from 'react'
import { useT } from '../../i18n/locale'
import type { SwarmRole } from '../../rpc/types'
import { useSwarmStore } from '../../state/swarmStore'
import css from './AgentSettings.module.css'

export function LibraryPicker(props: { onClose(): void; onCopied(): void }) {
  const t = useT()
  const libraryRoles = useSwarmStore((state) => state.libraryRoles)
  const copyRole = useSwarmStore((state) => state.copyRole)
  const current = useSwarmStore((state) => state.roles)
  const loaded = useSwarmStore((state) => state.loaded)
  const load = useSwarmStore((state) => state.load)
  const [rows, setRows] = useState<SwarmRole[] | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const existing = useMemo(() => new Set(current.map((role) => role.id)), [current])
  const close = props.onClose

  // Reached from Settings too, where the room was never loaded: the
  // already-there check needs this project's roles.
  useEffect(() => {
    if (!loaded) void load()
  }, [loaded, load])

  useEffect(() => {
    let alive = true
    void libraryRoles()
      .then((list) => {
        if (alive) setRows(list)
      })
      .catch(() => {
        if (alive) setRows([])
      })
    return () => {
      alive = false
    }
  }, [libraryRoles])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  async function copy(role: SwarmRole) {
    if (busy !== '') return
    setBusy(role.id)
    setError('')
    try {
      const answer = await copyRole(role.id)
      if (answer.ok) props.onCopied()
      else setError(answer.message ?? t('swarm.copyExists'))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy('')
    }
  }

  return (
    <div
      className={css.backdrop}
      data-library-picker
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        className={css.modal}
        role="dialog"
        aria-modal="true"
        aria-label={t('swarm.copyFromLibrary')}
      >
        <div className={css.drawer}>
          <div className={css.drawerHead}>
            <h3 className={css.drawerTitle}>{t('swarm.copyFromLibrary')}</h3>
            <button type="button" className={css.action} onClick={close}>
              {t('common.close')}
            </button>
          </div>
          {error !== '' && <div className={css.error}>{error}</div>}
          {rows === null ? null : rows.length === 0 ? (
            <div className={css.empty}>{t('swarm.libraryEmpty')}</div>
          ) : (
            <div className={css.panel}>
              {rows.map((role) => {
                const taken = existing.has(role.id)
                return (
                  <div className={css.role} key={role.id} data-library-role={role.id}>
                    <div className={css.roleMain}>
                      <div className={css.name}>
                        {role.name}
                        <span className={css.id}>{role.id}</span>
                      </div>
                      {role.description !== '' && (
                        <div className={css.description}>{role.description}</div>
                      )}
                      {taken && <div className={css.meta}>{t('swarm.copyExists')}</div>}
                    </div>
                    <div className={css.actions}>
                      <button
                        type="button"
                        className={css.action}
                        data-library-copy={role.id}
                        disabled={taken || busy !== ''}
                        onClick={() => void copy(role)}
                      >
                        {t('swarm.copy')}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
