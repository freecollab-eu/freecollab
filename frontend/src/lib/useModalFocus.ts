import { useLayoutEffect, useRef, type RefObject } from 'react'

interface ModalEntry {
  element: HTMLElement
  previousFocus: HTMLElement | null
  suspended: boolean
}
export interface ModalFocusOptions {
  preserveInsideFocus?: boolean
  restoreFocus?: boolean
  escapePhase?: 'capture' | 'bubble'
  /** Retain this modal's stack position while related work temporarily hides it. */
  suspended?: boolean
}
interface ModalFocusRestoration { readonly sequence: number; readonly modal: HTMLElement | null; readonly target: HTMLElement | null }
let lastModalFocusRestoration: ModalFocusRestoration = { sequence: 0, modal: null, target: null }
/** A read-only receipt for the hook's own cleanup focus handoff. */
export function getModalFocusRestoration(): ModalFocusRestoration { return { ...lastModalFocusRestoration } }
const modalStack: ModalEntry[] = []
const originalInert = new Map<HTMLElement, boolean>()
let originalOverflow: string | undefined
const topModal = () => {
  for (let index = modalStack.length - 1; index >= 0; index--) if (!modalStack[index].suspended) return modalStack[index]
  return undefined
}

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

function synchronizeModalEnvironment() {
  isolateTopModal()
  if (topModal()) {
    if (originalOverflow === undefined) {
      originalOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
  } else if (originalOverflow !== undefined) {
    document.body.style.overflow = originalOverflow
    originalOverflow = undefined
  }
}

function focusableElements(element: HTMLElement): HTMLElement[] {
  return Array.from(element.querySelectorAll<HTMLElement>(
    'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )).filter(node => node.tabIndex >= 0 && node.getClientRects().length > 0 && !node.closest('[inert]') && getComputedStyle(node).visibility !== 'hidden')
}

/** Isolate, focus and restore dialogs, including dialogs opened above another. */
export function useModalFocus(ref: RefObject<HTMLElement>, onClose: () => void, active = true, options: ModalFocusOptions = {}): void {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const { preserveInsideFocus = false, restoreFocus = true, escapePhase = 'capture', suspended = false } = options
  const suspendedRef = useRef(suspended), entryRef = useRef<ModalEntry>()
  suspendedRef.current = suspended

  useLayoutEffect(() => {
    const element = ref.current
    if (!active || !element) return
    const entry: ModalEntry = { element, previousFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null, suspended: suspendedRef.current }
    entryRef.current = entry
    modalStack.push(entry)
    synchronizeModalEnvironment()
    const first = () => element.querySelector<HTMLElement>('[data-autofocus]') || focusableElements(element)[0] || element
    if (!entry.suspended && (!preserveInsideFocus || !element.contains(document.activeElement))) first().focus({ preventScroll: true })

    const onEscape = (event: KeyboardEvent) => {
      if (topModal() !== entry || event.key !== 'Escape' || event.defaultPrevented || event.isComposing || event.keyCode === 229) return
      event.preventDefault()
      event.stopPropagation()
      closeRef.current()
    }

    const onKey = (event: KeyboardEvent) => {
      if (topModal() !== entry || event.isComposing || event.keyCode === 229) return
      if (escapePhase === 'capture') onEscape(event)
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
    if (escapePhase === 'bubble') document.addEventListener('keydown', onEscape)
    document.addEventListener('focusin', onFocus)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      if (escapePhase === 'bubble') document.removeEventListener('keydown', onEscape)
      document.removeEventListener('focusin', onFocus)
      const wasTop = topModal() === entry
      const index = modalStack.indexOf(entry)
      if (index !== -1) modalStack.splice(index, 1)
      if (entryRef.current === entry) entryRef.current = undefined
      synchronizeModalEnvironment()
      if (wasTop && restoreFocus) {
        const previous = entry.previousFocus
        const visible = previous?.isConnected && !previous.closest('[inert]') && previous.getClientRects().length > 0
        const underlying = topModal()?.element
        const fallback = underlying
          ? underlying.querySelector<HTMLElement>('[data-autofocus]') || focusableElements(underlying)[0] || underlying
          : document.querySelector<HTMLElement>('[data-modal-return-focus]:not(:disabled)')
        const target = visible ? previous : fallback
        if (target && !target.closest('[inert]') && target.getClientRects().length > 0) {
          target.focus({ preventScroll: true })
          if (document.activeElement === target) {
            const receipt = { sequence: lastModalFocusRestoration.sequence + 1, modal: element, target }
            lastModalFocusRestoration = receipt
            // The continuation host queued its return frame before React's
            // cleanup. Keep DOM authority for that handoff only, then release it.
            requestAnimationFrame(() => {
              if (lastModalFocusRestoration === receipt) lastModalFocusRestoration = { sequence: receipt.sequence, modal: null, target: null }
            })
          }
        }
      }
    }
  }, [ref, active, preserveInsideFocus, restoreFocus, escapePhase])

  useLayoutEffect(() => {
    const entry = entryRef.current
    if (!entry || entry.suspended === suspended) return
    entry.suspended = suspended
    // Suspension has no focus-restoration side effect. The continuation host
    // reveals every ancestor before restoring the source field and its caret.
    synchronizeModalEnvironment()
  }, [active, suspended])
}
