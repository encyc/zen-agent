/** Full-window zoom for an image the app is already showing (the read_image
 * card's preview today). Click anywhere or press Escape to close; the image
 * itself fills the window with `contain`, so the largest screenshots stay
 * fully visible. */

import { useEffect } from 'react'
import css from './ImageLightbox.module.css'

export function ImageLightbox({
  src,
  alt,
  onClose,
}: {
  src: string
  alt: string
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className={css.backdrop} role="dialog" aria-modal="true" aria-label={alt} onClick={onClose}>
      <img className={css.image} src={src} alt={alt} />
    </div>
  )
}
