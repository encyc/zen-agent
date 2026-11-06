/** Welcome screen: choose the working directory, remember recent projects.
 * The zen rpc subprocess runs with `--cwd` scoped to the picked folder. */

import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { useT } from '../i18n/locale'
import { FolderIcon, PlusIcon, XIcon } from './icons'
import css from './WelcomeScreen.module.css'

const RECENTS_KEY = 'zen.recent.projects'

interface Recent {
  path: string
  at: number
}

function loadRecents(): Recent[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY)
    return raw === null ? [] : (JSON.parse(raw) as Recent[])
  } catch {
    return []
  }
}

export function WelcomeScreen({ onStart }: { onStart(cwd: string | undefined): void }) {
  const t = useT()
  const [recents, setRecents] = useState<Recent[]>(loadRecents)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(RECENTS_KEY, JSON.stringify(recents.slice(0, 8)))
    } catch {
      // ignore
    }
  }, [recents])

  const pickFolder = async (): Promise<string | null> => {
    try {
      const selection = await invoke<string | null>('plugin:dialog|open', {
        options: { directory: true, multiple: false, title: 'ZenAgent' },
      })
      return typeof selection === 'string' ? selection : null
    } catch {
      return null
    }
  }

  const begin = (cwd?: string) => {
    setStarting(true)
    if (cwd !== undefined) {
      setRecents((previous) => [
        { path: cwd, at: Date.now() },
        ...previous.filter((recent) => recent.path !== cwd),
      ])
    }
    onStart(cwd)
  }

  const onPick = async () => {
    const path = await pickFolder()
    if (path !== null) begin(path)
  }

  return (
    <div className={css.root}>
      <div className={css.card}>
        <div className={css.mark} aria-hidden>
          <svg width="44" height="44" viewBox="0 0 18 18" fill="none">
            <rect x="1" y="1" width="16" height="16" rx="4.5" fill="var(--dsw-alias-state-business-primary)" />
            <path
              d="M9 4.2 10.3 7.7 13.8 9l-3.5 1.3L9 13.8 7.7 10.3 4.2 9l3.5-1.3Z"
              fill="var(--dsw-alias-label-primary-foreground)"
            />
          </svg>
        </div>
        <h1 className={css.title}>{t('brand.name')}</h1>
        <p className={css.subtitle}>{t('chat.empty.body')}</p>
        <button type="button" className={css.primaryButton} onClick={() => void onPick()}>
          <FolderIcon size={15} />
          <span>{starting ? t('connection.starting') : '打开项目目录'}</span>
        </button>
        <button type="button" className={css.secondaryButton} onClick={() => begin(undefined)}>
          <PlusIcon size={13} />
          <span>用当前目录启动</span>
        </button>
        {recents.length > 0 && (
          <div className={css.recents}>
            <div className={css.recentsLabel}>最近项目</div>
            {recents.map((recent) => (
              <div key={recent.path} className={css.recentRow}>
                <button
                  type="button"
                  className={css.recentButton}
                  onClick={() => begin(recent.path)}
                >
                  <FolderIcon size={13} />
                  <span className={css.recentPath}>{recent.path}</span>
                </button>
                <button
                  type="button"
                  className={css.recentRemove}
                  aria-label="remove"
                  onClick={() =>
                    setRecents((previous) => previous.filter((item) => item.path !== recent.path))
                  }
                >
                  <XIcon size={11} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
