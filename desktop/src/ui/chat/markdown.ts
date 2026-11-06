/** Assistant markdown: GFM via marked, model output sanitized before
 * injection, and fenced code decorated with a copy affordance. Labels are
 * passed in so the injected markup follows the active locale. */

import { useMemo } from 'react'
import { marked, Renderer } from 'marked'
import DOMPurify from 'dompurify'

export interface MarkdownLabels {
  copy: string
  copied: string
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeAttr(text: string): string {
  return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

export function markdownToHtml(text: string, labels: MarkdownLabels): string {
  const renderer = new Renderer()
  renderer.code = ({ text: code, lang }) => {
    const language = (lang ?? '').trim().split(/\s+/)[0] || 'text'
    return [
      '<div class="md-code-block">',
      '<div class="md-code-head">',
      `<span class="md-code-lang">${escapeHtml(language)}</span>`,
      '<button type="button" class="md-code-copy" data-copy-code',
      ` data-copy-label="${escapeAttr(labels.copy)}"`,
      ` data-copied-label="${escapeAttr(labels.copied)}"`,
      ` aria-label="${escapeAttr(labels.copy)}"></button>`,
      '</div>',
      `<pre><code>${escapeHtml(code)}</code></pre>`,
      '</div>',
    ].join('')
  }
  return marked.parse(text, {
    async: false,
    gfm: true,
    breaks: false,
    renderer,
  }) as string
}

export function renderMarkdown(text: string, labels: MarkdownLabels): string {
  return DOMPurify.sanitize(markdownToHtml(text, labels), {
    USE_PROFILES: { html: true },
  })
}

export function useMarkdown(text: string, labels: MarkdownLabels): string {
  return useMemo(
    () => renderMarkdown(text, { copy: labels.copy, copied: labels.copied }),
    [text, labels.copy, labels.copied],
  )
}
