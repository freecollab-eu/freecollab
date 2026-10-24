import { useLayoutEffect, useRef, useState, type FocusEvent, type RefObject } from 'react';
import { api } from './api';
import type { WorkSurfaceMode } from './useWorkSurfaceLayout';

interface FieldPosition {
  element: HTMLElement;
  value?: string;
  start?: number | null;
  end?: number | null;
  direction?: 'forward' | 'backward' | 'none';
}
interface Snapshot {
  element: HTMLElement;
  field: FieldPosition | null;
  scrollers: { element: HTMLElement; top: number; left: number }[];
}
interface Transition {
  generation: number;
  activity: number;
  identity: string;
  mode: WorkSurfaceMode;
  snapshot: Snapshot;
  expectedFocus: Element | null;
}
export interface CollaborationWorkbenchLayoutOptions {
  workRef: RefObject<HTMLElement>;
  timelineRef: RefObject<HTMLElement>;
  composerRef: RefObject<HTMLElement>;
  returnFocus?: HTMLElement | null;
  scope: string;
  /** Shared generation advances when another card or field becomes current. */
  activity: () => number;
  current: () => boolean;
  canRestore: () => boolean;
}
const editable = (element: HTMLElement): element is HTMLInputElement | HTMLTextAreaElement => element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement;
const visible = (element: HTMLElement) => element.isConnected && !element.closest('[hidden], [inert]') && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
const preferred = (element: HTMLElement) => visible(element) && (editable(element) || element.isContentEditable || element.hasAttribute('data-work-focus'));
function position(element: HTMLElement): FieldPosition {
  return editable(element) ? { element, value: element.value, start: element.selectionStart, end: element.selectionEnd, direction: element.selectionDirection || 'none' } : { element };
}

/** Presentation for one retained card. Other cards never share its draft or view. */
export function useCollaborationWorkbenchLayout({ workRef, timelineRef, composerRef, scope, activity, current, canRestore, returnFocus }: CollaborationWorkbenchLayoutOptions) {
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const identity = JSON.stringify([scope, actor, token]);
  const identityRef = useRef(identity), generation = useRef(0), mounted = useRef(true);
  const liveCurrent = useRef(current), liveActivity = useRef(activity), liveRestore = useRef(canRestore);
  liveCurrent.current = current; liveActivity.current = activity; liveRestore.current = canRestore;
  const liveReturnFocus = useRef(returnFocus); liveReturnFocus.current = returnFocus;
  const [view, setView] = useState<{ identity: string; mode: WorkSurfaceMode }>({ identity, mode: 'split' });
  const mode: WorkSurfaceMode = view.identity === identity ? view.mode : 'split';
  const modeRef = useRef(mode); modeRef.current = mode;
  const lastField = useRef<HTMLElement | null>(null), prepared = useRef<Snapshot | null>(null), parked = useRef<Snapshot | null>(null);
  const resumeMode = useRef<'split' | 'expanded'>('split'), pending = useRef<Transition | null>(null);
  if (identityRef.current !== identity) {
    identityRef.current = identity; ++generation.current;
    lastField.current = null; prepared.current = null; parked.current = null; pending.current = null; resumeMode.current = 'split';
  }
  const valid = () => mounted.current && identityRef.current === identity && actor === api.getCurrentUserId() && token === api.getAccessToken() && liveCurrent.current();
  function capture(preferField = true): Snapshot | null {
    const work = workRef.current;
    if (!valid() || !work?.isConnected) return null;
    const active = document.activeElement;
    let field: HTMLElement | null = null;
    if (active instanceof HTMLElement && work.contains(active) && visible(active)) {
      if (preferred(active)) lastField.current = active;
      if (!preferField || preferred(active)) field = active;
    }
    if (!field && lastField.current && work.contains(lastField.current) && preferred(lastField.current)) field = lastField.current;
    if (!field && active instanceof HTMLElement && work.contains(active) && visible(active)) field = active;
    const scrollers = new Set<HTMLElement>([work]);
    if (timelineRef.current) scrollers.add(timelineRef.current);
    for (const element of work.querySelectorAll<HTMLElement>('.fc-conversation-work-body, .fc-collaboration-body, .fc-reference-groups-body, textarea, [data-work-scroll]')) scrollers.add(element);
    return { element: work, field: field ? position(field) : null, scrollers: [...scrollers].map(element => ({ element, top: element.scrollTop, left: element.scrollLeft })) };
  }
  function beforeViewChange() { prepared.current = capture(); }
  function transition(next: WorkSurfaceMode, snapshot: Snapshot | null) {
    if (!valid() || !snapshot || snapshot.element !== workRef.current) return;
    prepared.current = null;
    pending.current = { generation: ++generation.current, activity: liveActivity.current(), identity, mode: next, snapshot, expectedFocus: document.activeElement };
    modeRef.current = next; setView({ identity, mode: next });
  }
  function toggleExpanded() {
    transition(modeRef.current === 'expanded' ? 'split' : 'expanded', modeRef.current === 'minimized' ? parked.current : prepared.current || capture(false));
  }
  function minimize() {
    if (modeRef.current !== 'minimized') resumeMode.current = modeRef.current;
    const snapshot = prepared.current || capture();
    parked.current = snapshot; transition('minimized', snapshot);
  }
  function resume() { if (modeRef.current === 'minimized') transition(resumeMode.current, parked.current || capture()); }
  function restoreBeside() {
    if (modeRef.current === 'expanded') transition('split', prepared.current || capture());
  }
  function onFocusCapture(event: FocusEvent<HTMLElement>) {
    if (valid() && event.target instanceof HTMLElement && workRef.current?.contains(event.target) && preferred(event.target)) lastField.current = event.target;
  }
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; ++generation.current; pending.current = null; prepared.current = null; parked.current = null; lastField.current = null; };
  }, []);
  useLayoutEffect(() => {
    const action = pending.current;
    if (!action || action.identity !== identity || action.mode !== mode) return;
    const frame = requestAnimationFrame(() => {
      if (!valid() || !liveRestore.current() || action !== pending.current || action.generation !== generation.current || action.activity !== liveActivity.current() || workRef.current !== action.snapshot.element) return;
      const active = document.activeElement;
      // A later field or modal wins before either scroll or focus restoration.
      const originStillCurrent = active === action.expectedFocus || active === document.body || active === action.snapshot.field?.element
        || active instanceof HTMLElement && action.snapshot.element.contains(active) && !preferred(active);
      if (!originStillCurrent) { pending.current = null; return; }
      const restoreScroll = () => {
        for (const saved of action.snapshot.scrollers) if (saved.element.isConnected && (saved.element === timelineRef.current || action.snapshot.element.contains(saved.element))) {
          saved.element.scrollTop = saved.top; saved.element.scrollLeft = saved.left;
        }
      };
      restoreScroll();
      const field = action.snapshot.field;
      const returning = liveReturnFocus.current;
      const desired = mode === 'minimized' ? returning && visible(returning) ? returning : composerRef.current : field?.element;
      const target = desired && visible(desired) && (mode === 'minimized' || action.snapshot.element.contains(desired)) ? desired : mode !== 'minimized' && visible(action.snapshot.element) ? action.snapshot.element : null;
      if (target) {
        target.focus({ preventScroll: true });
        if (target === field?.element && editable(target) && field.value === target.value && field.start != null && field.end != null) target.setSelectionRange(field.start, field.end, field.direction || 'none');
        restoreScroll();
      }
      pending.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [identity, mode, view]);
  return { mode, beforeViewChange, toggleExpanded, minimize, resume, restoreBeside, onFocusCapture };
}
