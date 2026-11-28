/** Full-window zoom for an image the app is already showing (the read_image
 * card's preview today). Click anywhere or press Escape to close; the image
 * itself fills the scope with `contain`, so the largest screenshots stay
 * fully visible.
 *
 * `mount` scopes the mask to one pane (the chat pane passes its root here):
 * the backdrop portals in and turns absolute, so the sidebar and the work
 * rail keep their own space. Without it the mask covers the whole window. */

import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import css from './ImageLightbox.module.css'

export function ImageLightbox({
  src,
  alt,
  onClose,
  mount = null,
}: {
  src: string
  alt: string
  onClose: () => void
  mount?: HTMLElement | null
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const backdrop = (
    <div
      className={css.backdrop}
      data-absolute={mount !== null || undefined}
      role="dialog"
      aria-modal="true"
      aria-label={alt}
      onClick={onClose}
    >
      <img className={css.image} src={src} alt={alt} />
    </div>
  )
  return mount !== null ? createPortal(backdrop, mount) : backdrop
}
