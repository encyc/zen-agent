/** The brand mark: the bundled app icon, falling back to a drawn taiji when
 * the asset can't load. A stale webview cache can keep 404ing the old hashed
 * asset forever — a broken-image glyph must never stand in for the brand. */

import { useState } from 'react'
import appIcon from '../assets/app-icon.png'

export function AppMark(props: React.ComponentProps<'img'>) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    // Drawn in the palette's own ink; the disc uses the surface color so the
    // dots read on both sides of the mode switch. Callers pass img-style
    // props (alt, draggable, data-*), so the svg side narrows them.
    const { alt, ...svgProps } = props
    return (
      <svg
        viewBox="0 0 64 64"
        role={alt ? 'img' : undefined}
        aria-label={alt || undefined}
        aria-hidden={alt ? undefined : true}
        {...(svgProps as React.ComponentProps<'svg'>)}
        data-app-mark="fallback"
      >
        <circle cx="32" cy="32" r="30" fill="var(--dsw-alias-bg-base)" />
        <path d="M32 4 A28 28 0 0 1 32 60 Z" fill="currentColor" />
        <path d="M32 4 A14 14 0 0 0 32 32 Z" fill="currentColor" />
        <path d="M32 32 A14 14 0 0 0 32 60 Z" fill="var(--dsw-alias-bg-base)" />
        <circle cx="32" cy="32" r="29.4" fill="none" stroke="currentColor" strokeWidth="2.6" />
        <circle cx="32" cy="18" r="4.6" fill="var(--dsw-alias-bg-base)" />
        <circle cx="32" cy="46" r="4.6" fill="currentColor" />
      </svg>
    )
  }
  return <img {...props} src={appIcon} onError={() => setFailed(true)} />
}
