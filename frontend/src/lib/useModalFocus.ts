import { useLayoutEffect, useRef, type RefObject } from 'react'

interface ModalEntry {
  element: HTMLElement
  previousFocus: HTMLElement | null
}
const modalStack: ModalEntry[] = []
const originalInert = new Map<HTMLElement, boolean>()
let originalOverflow: string | undefined
const topModal = () => modalStack[modalStack.length - 1]

function isolateTopModal() {
  for (const [element, inert] of originalInert) element.inert = inert
  const top = topModal()?.element
  if (!top) {
    originalInert.clear()
    return
  }
  let current = top
  while (current.parentElement) {
    for (const sibling of current.parentElement.children) {
      if (!(sibling instanceof HTMLElement) || sibling === current) continue
      if (!originalInert.has(sibling)) originalInert.set(sibling, sibling.inert)
      sibling.inert = true
    }
    current = current.parentElement
    if (current === document.body) break
  }
}

function focusableElements(element: HTMLElement): HTMLElement[] {
  return Array.from(element.querySelectorAll<HTMLElement>(
    'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )).filter(node => node.tabIndex >= 0 && node.getClientRects().length > 0 && !node.closest('[inert]') && getComputedStyle(node).visibility !== 'hidden')
}

/** Isolate, focus and restore dialogs, including dialogs opened above another. */
export function useModalFocus(ref: RefObject<HTMLElement>, onClose: () => void, active = true): void {
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useLayoutEffect(() => {
    const element = ref.current
    if (!active || !element) return
    const entry: ModalEntry = { element, previousFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null }
    if (!modalStack.length) {
      originalOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
    modalStack.push(entry)
    isolateTopModal()
    const first = () => element.querySelector<HTMLElement>('[data-autofocus]') || focusableElements(element)[0] || element
    first().focus({ preventScroll: true })

    const onKey = (event: KeyboardEvent) => {
      if (topModal() !== entry || event.isComposing) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closeRef.current()
      }
      if (event.key !== 'Tab') return
      const controls = focusableElements(element)
      if (!controls.length) { event.preventDefault(); element.focus(); return }
      const focused = document.activeElement
      if (!element.contains(focused) || (event.shiftKey ? focused === controls[0] : focused === controls[controls.length - 1])) {
        event.preventDefault()
        const target = event.shiftKey ? controls[controls.length - 1] : controls[0]
        target.focus()
      }
    }
    const onFocus = (event: FocusEvent) => {
      if (topModal() === entry && event.target instanceof Node && !element.contains(event.target)) first().focus({ preventScroll: true })
    }
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('focusin', onFocus)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('focusin', onFocus)
      const wasTop = topModal() === entry
      const index = modalStack.indexOf(entry)
      if (index !== -1) modalStack.splice(index, 1)
      isolateTopModal()
      if (!modalStack.length && originalOverflow !== undefined) {
        document.body.style.overflow = originalOverflow
        originalOverflow = undefined
      }
      if (wasTop) {
        const previous = entry.previousFocus
        const visible = previous?.isConnected && !previous.closest('[inert]') && previous.getClientRects().length > 0
        const underlying = topModal()?.element
        const fallback = underlying
          ? underlying.querySelector<HTMLElement>('[data-autofocus]') || focusableElements(underlying)[0] || underlying
          : document.querySelector<HTMLElement>('[data-modal-return-focus]:not(:disabled)')
        const target = visible ? previous : fallback
        if (target && !target.closest('[inert]') && target.getClientRects().length > 0) target.focus({ preventScroll: true })
      }
    }
  }, [ref, active])
}
