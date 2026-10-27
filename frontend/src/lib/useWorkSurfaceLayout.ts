import { useEffect, useLayoutEffect, useRef, useState, type FocusEvent, type RefObject } from 'react';
import { api } from './api';

export type WorkSurfaceMode = 'split' | 'expanded' | 'minimized';
export interface WorkSurfaceLayoutOptions {
  workRef: RefObject<HTMLElement>;
  timelineRef: RefObject<HTMLElement>;
  composerRef: RefObject<HTMLElement>;
  /** Includes the account, credential, workspace, channel, encryption and actual work target. */
  scope: string;
  enabled: boolean;
}
interface FieldSnapshot {
  element: HTMLElement;
  selection?: { start: number; end: number; direction: 'forward' | 'backward' | 'none'; value: string };
}
interface SurfaceSnapshot {
  work: HTMLElement;
  focus: FieldSnapshot | null;
  scroll: { element: HTMLElement; top: number; left: number }[];
}
interface Transition {
  generation: number;
  identity: string;
  mode: WorkSurfaceMode;
  snapshot: SurfaceSnapshot;
  expectedFocus: Element | null;
}

const editable = (element: HTMLElement): element is HTMLInputElement | HTMLTextAreaElement => element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement;
const visible = (element: HTMLElement) => element.isConnected && !element.closest('[hidden], [inert]') && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
const preferredField = (element: HTMLElement) => visible(element) && (editable(element) || element.isContentEditable || element.hasAttribute('data-work-focus'));
function fieldSnapshot(element: HTMLElement): FieldSnapshot {
  if (editable(element) && element.selectionStart !== null && element.selectionEnd !== null)
    return { element, selection: { start: element.selectionStart, end: element.selectionEnd, direction: element.selectionDirection || 'none', value: element.value } };
  return { element };
}

/** Change only the layout of a retained editor. No navigation, editor or storage writes. */
export function useWorkSurfaceLayout({ workRef, timelineRef, composerRef, scope, enabled }: WorkSurfaceLayoutOptions) {
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const identity = JSON.stringify([scope, enabled, actor, token]);
  const currentIdentity = useRef(identity), generation = useRef(0), mounted = useRef(true);
  const prepared = useRef<SurfaceSnapshot | null>(null), minimized = useRef<SurfaceSnapshot | null>(null);
  const resumeMode = useRef<'split' | 'expanded'>('split');
  const lastPreferredField = useRef<HTMLElement | null>(null), pending = useRef<Transition | null>(null);
  const [view, setView] = useState<{ identity: string; mode: WorkSurfaceMode }>({ identity, mode: 'split' });
  if (currentIdentity.current !== identity) {
    currentIdentity.current = identity; ++generation.current;
    prepared.current = null; minimized.current = null; lastPreferredField.current = null; pending.current = null;
    resumeMode.current = 'split';
  }
  const mode: WorkSurfaceMode = enabled && view.identity === identity ? view.mode : 'split';
  const liveMode = useRef(mode); liveMode.current = mode;
  const current = () => mounted.current && enabled && identity === currentIdentity.current && actor === api.getCurrentUserId() && token === api.getAccessToken();

  function workFocus(work: HTMLElement, preferField: boolean): FieldSnapshot | null {
    const active = document.activeElement;
    if (active instanceof HTMLElement && work.contains(active)) {
      if (preferredField(active)) lastPreferredField.current = active;
      if ((!preferField || preferredField(active)) && visible(active)) return fieldSnapshot(active);
    }
    const previous = lastPreferredField.current;
    if (previous && visible(previous) && work.contains(previous)) return fieldSnapshot(previous);
    return active instanceof HTMLElement && visible(active) && work.contains(active) ? fieldSnapshot(active) : null;
  }
  function capture(preferField: boolean): SurfaceSnapshot | null {
    const work = workRef.current;
    if (!current() || !work?.isConnected) return null;
    const scrollers = new Set<HTMLElement>([work]);
    if (timelineRef.current) scrollers.add(timelineRef.current);
    for (const element of work.querySelectorAll<HTMLElement>('.fc-conversation-work-body, .fc-collaboration-body, .fc-reference-groups-body, textarea, [data-work-scroll]')) scrollers.add(element);
    return { work, focus: workFocus(work, preferField), scroll: [...scrollers].map(element => ({ element, top: element.scrollTop, left: element.scrollLeft })) };
  }
  function beforeViewChange(_event?: { type?: string }) {
    prepared.current = capture(true);
  }
  function transition(next: WorkSurfaceMode, snapshot: SurfaceSnapshot | null) {
    if (!current() || !snapshot || snapshot.work !== workRef.current) return;
    prepared.current = null;
    pending.current = { generation: ++generation.current, identity, mode: next, snapshot, expectedFocus: document.activeElement };
    liveMode.current = next; setView({ identity, mode: next });
  }
  function toggleExpanded() {
    const snapshot = liveMode.current === 'minimized' ? minimized.current : prepared.current || capture(false);
    transition(liveMode.current === 'expanded' ? 'split' : 'expanded', snapshot);
  }
  function minimize() {
    if (liveMode.current !== 'minimized') resumeMode.current = liveMode.current;
    const snapshot = prepared.current || capture(true);
    // Read the last actual field again: a toolbar control may now own focus.
    if (snapshot && current()) snapshot.focus = workFocus(snapshot.work, true);
    minimized.current = snapshot; transition('minimized', snapshot);
  }
  function resume() { if (liveMode.current === 'minimized') transition(resumeMode.current, minimized.current || capture(true)); }
  function reset() {
    ++generation.current; pending.current = null; prepared.current = null; minimized.current = null; lastPreferredField.current = null;
    resumeMode.current = 'split';
    liveMode.current = 'split'; setView({ identity: currentIdentity.current, mode: 'split' });
  }
  function onFocusCapture(event: FocusEvent<HTMLElement>) {
    const element = event.target;
    if (current() && element instanceof HTMLElement && workRef.current?.contains(element) && preferredField(element)) lastPreferredField.current = element;
  }

  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; ++generation.current; pending.current = null; prepared.current = null; minimized.current = null; lastPreferredField.current = null; };
  }, []);
  useLayoutEffect(() => {
    const work = workRef.current, timeline = timelineRef.current;
    if (!enabled || !work || !timeline) return;
    const measure = () => {
      if (!current() || workRef.current !== work || timelineRef.current !== timeline) return;
      work.style.setProperty('--fc-work-scrollport-height', `${timeline.clientHeight}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(timeline);
    return () => { observer.disconnect(); work.style.removeProperty('--fc-work-scrollport-height'); };
  }, [identity]);
  useEffect(() => {
    if (!enabled) return;
    const clear = () => { prepared.current = null; };
    // A cancelled pointer gesture must never be reused by later keyboard activation.
    document.addEventListener('keydown', clear, true); document.addEventListener('pointercancel', clear, true); document.addEventListener('pointerdown', clear, true);
    return () => { document.removeEventListener('keydown', clear, true); document.removeEventListener('pointercancel', clear, true); document.removeEventListener('pointerdown', clear, true); };
  }, [identity, enabled]);
  useLayoutEffect(() => {
    const action = pending.current;
    if (!action || action.identity !== identity || action.mode !== mode) return;
    // Resuming hides its focused control. Restore the field after that commit,
    // before a newly active modal has to choose an unrelated first control.
    const resumedField = action.snapshot.focus?.element;
    if (mode !== 'minimized' && current() && action.generation === generation.current && workRef.current === action.snapshot.work &&
      document.activeElement === document.body && action.expectedFocus instanceof HTMLElement && !visible(action.expectedFocus) &&
      resumedField && action.snapshot.work.contains(resumedField) && visible(resumedField)) resumedField.focus({ preventScroll: true });
    const frame = requestAnimationFrame(() => {
      const { snapshot } = action;
      if (!current() || action.generation !== generation.current || pending.current !== action || workRef.current !== snapshot.work || !snapshot.work.isConnected) return;
      const restoreScroll = () => {
        for (const position of snapshot.scroll) if (position.element.isConnected && (position.element === timelineRef.current || snapshot.work.contains(position.element))) {
          position.element.scrollTop = position.top; position.element.scrollLeft = position.left;
        }
      };
      restoreScroll();
      const desired = mode === 'minimized' ? composerRef.current : snapshot.focus?.element;
      const target = desired && visible(desired) && (mode === 'minimized' || snapshot.work.contains(desired)) ? desired : mode !== 'minimized' && visible(snapshot.work) ? snapshot.work : null;
      const active = document.activeElement;
      // Respect a deliberate move to another field or a nested modal while layout settles.
      if (target && (active === action.expectedFocus || active === document.body || active === desired)) {
        target.focus({ preventScroll: true });
        const selection = snapshot.focus?.selection;
        if (mode !== 'minimized' && target === snapshot.focus?.element && editable(target) && selection && target.value === selection.value) {
          target.setSelectionRange(selection.start, selection.end, selection.direction);
        }
        restoreScroll();
      }
      pending.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [view, mode, identity]);

  return { mode, beforeViewChange, toggleExpanded, minimize, resume, reset, onFocusCapture };
}
