/** Keeps the chat where the user left it when Settings or Tools replaces the
 * shell. The page is unmounted while a page is open, so the scroll offset,
 * focused control and composer focus are captured before the switch and
 * re-applied on return. */

let savedScroll = 0
let composerFocused = false
let focusedLabel: string | null = null

export function captureChatState(): void {
  const scroller = document.querySelector<HTMLElement>('[data-chat-scroll]')
  savedScroll = scroller?.scrollTop ?? 0
  const active = document.activeElement
  composerFocused = active?.closest('[data-shell-composer]') !== null
  focusedLabel = active instanceof HTMLElement ? active.getAttribute('aria-label') : null
}

export function restoreChatState(): void {
  const scroll = savedScroll
  const focusComposer = composerFocused
  const label = focusedLabel
  requestAnimationFrame(() => {
    const scroller = document.querySelector<HTMLElement>('[data-chat-scroll]')
    if (scroller !== null) scroller.scrollTop = scroll
    if (label !== null) {
      const target = Array.from(document.querySelectorAll<HTMLElement>('[aria-label]')).find(
        (element) => element.getAttribute('aria-label') === label,
      )
      target?.focus()
      return
    }
    if (focusComposer) {
      document.querySelector<HTMLTextAreaElement>('[data-shell-composer] textarea')?.focus()
    }
  })
}
