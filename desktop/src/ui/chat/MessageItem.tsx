/** User bubble (right-aligned, dsh bubble tokens) and the /skill command turn. */

import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useT } from '../../i18n/locale'
import css from './MessageItem.module.css'

export function UserMessage({ text, steering = false }: { text: string; steering?: boolean }) {
  const t = useT()
  return (
    <div className={css.userRow}>
      {steering && <span className={css.steerChip}>{t('message.steering')}</span>}
      <div className={css.bubble} data-selectable data-steering={steering || undefined}>
        {text}
      </div>
    </div>
  )
}

export function SkillMessage({
  name,
  args,
  body,
}: {
  name: string
  args: string | null
  body: string | null
}) {
  return (
    <div className={css.userRow}>
      <div className={css.skillCard} data-selectable>
        <div className={css.skillHead}>
          <span className={css.skillName}>{name}</span>
          {args !== null && args !== '' && (
            <span className={css.skillArgs}>{args}</span>
          )}
        </div>
        {body !== null && <pre className={css.skillBody}>{body}</pre>}
      </div>
    </div>
  )
}

/** Markdown renderer shared by assistant turns. GFM via marked; model output is
 * untrusted, so raw HTML and javascript: URLs are stripped before injection. */
export function renderMarkdown(text: string): string {
  const html = marked.parse(text, {
    async: false,
    gfm: true,
    breaks: false,
  })
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
}

export function useMarkdown(text: string): string {
  return useMemo(() => renderMarkdown(text), [text])
}
