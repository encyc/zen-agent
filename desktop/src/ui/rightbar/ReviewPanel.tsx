/** Review tab: every transcript diff grouped per file, rendered as unified
 * hunks with old/new gutters, per-file stats and an optional wrap mode. */

import { useMemo, useState } from 'react'
import { useChatStore } from '../../state/chatStore'
import { useT } from '../../i18n/locale'
import { ChevronDownIcon, ChevronRightIcon } from '../icons'
import { collectFileChanges, fileBasename } from './review'
import { DiffBlock } from './DiffBlock'
import css from './ReviewPanel.module.css'

export function ReviewPanel() {
  const t = useT()
  const nodes = useChatStore((state) => state.nodes)
  const changes = useMemo(() => collectFileChanges(nodes), [nodes])
  const [closed, setClosed] = useState<Set<string>>(new Set())
  const [wrap, setWrap] = useState(false)

  if (changes.length === 0) {
    return <p className={css.empty}>{t('panel.reviewEmpty')}</p>
  }

  const additions = changes.reduce((sum, change) => sum + change.additions, 0)
  const removals = changes.reduce((sum, change) => sum + change.removals, 0)

  return (
    <div className={css.root}>
      <div className={css.toolbar}>
        <span className={css.summary}>
          {changes.length} · <span className={css.added}>+{additions}</span>{' '}
          <span className={css.removed}>−{removals}</span>
        </span>
        <button
          type="button"
          className={css.wrap}
          aria-pressed={wrap}
          onClick={() => setWrap((value) => !value)}
        >
          {t('panel.reviewWrap')}
        </button>
      </div>
      <div className={css.scroll}>
        {changes.map((change) => {
          const open = !closed.has(change.path)
          return (
            <section key={change.path} className={css.file}>
              <button
                type="button"
                className={css.fileHeader}
                aria-expanded={open}
                title={change.path}
                onClick={() =>
                  setClosed((prev) => {
                    const next = new Set(prev)
                    if (next.has(change.path)) next.delete(change.path)
                    else next.add(change.path)
                    return next
                  })
                }
              >
                <span className={css.chevron}>
                  {open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
                </span>
                <span className={css.fileName}>{fileBasename(change.path)}</span>
                <span className={css.fileDir}>{change.path}</span>
                <span className={css.counts}>
                  <span className={css.added}>+{change.additions}</span>{' '}
                  <span className={css.removed}>−{change.removals}</span>
                </span>
              </button>
              {open && change.diffs.map((diff, index) => (
                <DiffBlock key={index} diff={diff} wrap={wrap} />
              ))}
            </section>
          )
        })}
      </div>
    </div>
  )
}
