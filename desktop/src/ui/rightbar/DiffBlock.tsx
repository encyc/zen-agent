/** Shared unified-diff renderer: parsed hunks with old/new gutters, per-line
 * add/del tinting, a wrap mode, and the oversized-diff truncation note.
 * ReviewPanel (session diffs) and GitPanel (git diffs) both render through it. */

import { useMemo } from 'react'
import { useT } from '../../i18n/locale.ts'
import { parseDiffHunks } from './review.ts'
import css from './DiffBlock.module.css'

const MAX_RENDERED_LINES = 1200

export function DiffBlock({ diff, wrap = false }: { diff: string; wrap?: boolean }) {
  const t = useT()
  const hunks = useMemo(() => parseDiffHunks(diff), [diff])
  const truncated = diff.split('\n').length > MAX_RENDERED_LINES

  if (hunks.length === 0) {
    return <pre className={css.diff}>{diff}</pre>
  }

  return (
    <div className={css.diff} data-wrap={wrap || undefined}>
      {hunks.map((hunk, hunkIndex) => (
        <div key={hunkIndex} className={css.hunk}>
          {hunk.header !== '' && <div className={css.hunkHeader}>{hunk.header}</div>}
          {hunk.lines.map((line, lineIndex) => (
            <div key={lineIndex} className={css.line} data-kind={line.kind}>
              <span className={css.gutter}>{line.oldNo ?? ''}</span>
              <span className={css.gutter}>{line.newNo ?? ''}</span>
              <span className={css.marker}>
                {line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ''}
              </span>
              <span className={css.code}>{line.text === '' ? ' ' : line.text}</span>
            </div>
          ))}
        </div>
      ))}
      {truncated && <div className={css.truncated}>{t('panel.reviewTruncated')}</div>}
    </div>
  )
}
