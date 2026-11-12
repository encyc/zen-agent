import { useEffect } from 'react'
import { attachDragDrop } from './dragDrop'

/** One drag-drop subscription for every composer adapter: images only. */
export function useImageDrop(add: (paths: string[]) => void): void {
  useEffect(
    () =>
      attachDragDrop((paths) => {
        const images = paths.filter((path) => /\.(png|jpe?g|gif|webp|bmp)$/i.test(path))
        if (images.length > 0) add(images)
      }),
    [add],
  )
}
