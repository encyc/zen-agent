/** Assistant markdown: GFM via marked, model output sanitized before
 * injection, and fenced code decorated with a copy affordance. Labels are
 * passed in so the injected markup follows the active locale. */

import { useMemo } from 'react'
import { marked, Renderer } from 'marked'
import DOMPurify from 'dompurify'
import { parseFileRef, parseLinkRef } from './fileRefs.ts'

export interface MarkdownLabels {
  copy: string
  copied: string
}

export interface MarkdownOptions {
  /** Render workspace file paths as links into the work panel's Files tab. */
  fileLinks?: boolean
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeAttr(text: string): string {
  return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function fileRefHtml(path: string, label: string): string {
  return [
    `<code class="md-file-ref" data-file-path="${escapeAttr(path)}"`,
    ' role="link" tabindex="0">',
    escapeHtml(label),
    '</code>',
  ].join('')
}

export function markdownToHtml(
  text: string,
  labels: MarkdownLabels,
  options: MarkdownOptions = {},
): string {
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
  if (options.fileLinks === true) {
    renderer.codespan = ({ text: code }) => {
      const path = parseFileRef(code)
      return path === null ? `<code>${escapeHtml(code)}</code>` : fileRefHtml(path, code)
    }
  }
  // A destination is an explicit reference: a local path (folder or file,
  // extension or not) never becomes a native anchor — the transcript turns
  // it into a panel reference when enabled, otherwise it keeps just its
  // label. A remote target opens outside the shell so the app is never
  // navigated away. The parser is bound at parse time, so the default
  // renderer is captured for every anchor.
  const defaultLink = renderer.link.bind(renderer)
  renderer.link = function (token) {
    const local = parseLinkRef(token.href)
    if (local !== null) {
      return options.fileLinks === true
        ? fileRefHtml(local, this.parser.parseInline(token.tokens))
        : this.parser.parseInline(token.tokens)
    }
    const href = token.href.trim()
    if (href === '' || href.startsWith('#')) return defaultLink(token)
    return defaultLink(token).replace('<a ', '<a target="_blank" rel="noreferrer" ')
  }
  return marked.parse(text, {
    async: false,
    gfm: true,
    breaks: false,
    renderer,
  }) as string
}

export function renderMarkdown(
  text: string,
  labels: MarkdownLabels,
  options: MarkdownOptions = {},
): string {
  return DOMPurify.sanitize(markdownToHtml(text, labels, options), {
    USE_PROFILES: { html: true },
  })
}

export function useMarkdown(
  text: string,
  labels: MarkdownLabels,
  options: MarkdownOptions = {},
): string {
  const fileLinks = options.fileLinks === true
  return useMemo(
    () => renderMarkdown(text, { copy: labels.copy, copied: labels.copied }, { fileLinks }),
    [text, labels.copy, labels.copied, fileLinks],
  )
}

