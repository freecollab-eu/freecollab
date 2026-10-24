import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { ArrowUpRight, FileText, Layers, X } from 'lucide-react';
import { api } from '../lib/api';
import { t, useI18n } from '../i18n';
import { useModalFocus } from '../lib/useModalFocus';
import { useCollaborationWorkbenchLayout } from '../lib/useCollaborationWorkbench';
import type { ConversationWorkLayoutActions } from './ConversationWorkControls';
import type { WorkSurfaceMode } from '../lib/useWorkSurfaceLayout';
import '../styles/collaboration-workbench.css';

/** Scope is captured when opening the card; it stays in memory, never in DOM. */
export interface CollaborationWorkbenchEntry { id: string; title: string; kind: string; scope: string; returnFocus?: HTMLElement | null }
export interface CollaborationWorkbenchEntryContext {
  layoutActions: ConversationWorkLayoutActions;
  /** A safe unique DOM namespace, without actor credentials or workspace scope. */
  scopeId: string;
}
export interface CollaborationWorkbenchProps<T extends CollaborationWorkbenchEntry> {
  scope: string;
  entries: readonly T[];
  composerRef: RefObject<HTMLElement>;
  timelineRef: RefObject<HTMLElement>;
  renderEntry: (entry: T, context: CollaborationWorkbenchEntryContext) => ReactNode;
  activeId?: string | null;
  /** Change when explicitly revealing the same already-active parked card. */
  activationKey?: string | number;
  onActiveChange?: (id: string) => void;
  /** Only a registered child guard may close a parked card. Never a raw removal. */
  onGuardedClose?: (entry: T) => void;
}
interface Controls { resume(): void; restoreBeside(): void; focus(): void; mode(): WorkSurfaceMode }
interface EntryProps<T extends CollaborationWorkbenchEntry> {
  entry: T;
  identity: string;
  isActive: boolean;
  composerRef: RefObject<HTMLElement>;
  timelineRef: RefObject<HTMLElement>;
  renderEntry: CollaborationWorkbenchProps<T>['renderEntry'];
  activity: () => number;
  valid: () => boolean;
  ownsActivity: () => boolean;
  activate: (id: string) => void;
  noteIntent: () => void;
  register: (id: string, controls: Controls) => () => void;
  onModeChange: (id: string, mode: WorkSurfaceMode, identity: string) => void;
}
function WorkbenchCard<T extends CollaborationWorkbenchEntry>({ entry, identity, isActive, composerRef, timelineRef, renderEntry, activity, valid, ownsActivity, activate, noteIntent, register, onModeChange }: EntryProps<T>) {
  const workRef = useRef<HTMLDivElement>(null), generatedId = useId();
  const scopeId = 'collaboration-work-' + generatedId.replace(/:/g, '');
  const layout = useCollaborationWorkbenchLayout({ workRef, timelineRef, composerRef, scope: identity + ':' + entry.id, activity, current: valid, canRestore: ownsActivity, returnFocus: entry.returnFocus });
  const live = useRef(layout); live.current = layout;
  useModalFocus(workRef, layout.restoreBeside, layout.mode === 'expanded', { preserveInsideFocus: true, restoreFocus: false, escapePhase: 'bubble' });
  useLayoutEffect(() => register(entry.id, {
    resume: () => live.current.resume(), restoreBeside: () => live.current.restoreBeside(), mode: () => live.current.mode,
    focus: () => {
      const work = workRef.current;
      if (!valid() || !work || work.closest('[hidden], [inert]')) return;
      work.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' });
      if (!work.contains(document.activeElement)) work.focus({ preventScroll: true });
    },
  }), [entry.id, identity, register]);
  useLayoutEffect(() => onModeChange(entry.id, layout.mode, identity), [entry.id, layout.mode, identity, onModeChange]);
  const layoutActions: ConversationWorkLayoutActions = { expanded: layout.mode === 'expanded', onToggleExpanded: layout.toggleExpanded, onMinimize: layout.minimize, onBeforeViewChange: layout.beforeViewChange, workAreaId: scopeId };
  return <div className="fc-collaboration-work-card" data-collaboration-work-id={entry.id} data-work-active={isActive ? true : undefined} data-work-kind={entry.kind} data-workspace-mode={layout.mode} hidden={layout.mode === 'minimized'}
    onPointerDownCapture={() => { noteIntent(); activate(entry.id); }} onFocusCapture={event => { if (event.target !== workRef.current) noteIntent(); activate(entry.id); layout.onFocusCapture(event); }}>
    <div ref={workRef} id={scopeId} className="fc-collaboration-work-surface fc-conversation-workspace" data-workspace-mode={layout.mode} tabIndex={-1}
      role={layout.mode === 'expanded' ? 'dialog' : 'region'} aria-modal={layout.mode === 'expanded' ? true : undefined}
      aria-label={layout.mode === 'expanded' ? t('Full screen work area') + ': ' + entry.title : entry.title}>
      {renderEntry(entry, { layoutActions, scopeId })}
    </div>
  </div>;
}

/** Several real work surfaces share a flow, without replacing one another. */
export function CollaborationWorkbench<T extends CollaborationWorkbenchEntry>({ scope, entries, composerRef, timelineRef, renderEntry, activeId, activationKey, onActiveChange, onGuardedClose }: CollaborationWorkbenchProps<T>) {
  useI18n();
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const identity = JSON.stringify([scope, actor, token]);
  const currentIdentity = useRef(identity); currentIdentity.current = identity;
  const root = useRef<HTMLElement>(null), controls = useRef(new Map<string, Controls>()), activity = useRef(0), active = useRef<string | null>(null), previousIds = useRef(new Set<string>());
  const revealFrame = useRef(0), revealSequence = useRef(0);
  const lastExternalActivation = useRef({ identity, key: activationKey });
  const liveCallback = useRef(onActiveChange); liveCallback.current = onActiveChange;
  const [views, setViews] = useState<Record<string, WorkSurfaceMode>>({});
  const [, refreshAuthority] = useState(0);
  const [activeEntry, setActiveEntry] = useState<{ identity: string; id: string | null }>({ identity, id: null });
  const scopeOwner = useRef({ scope, actor, token });
  if (scopeOwner.current.scope !== scope) {
    scopeOwner.current = { scope, actor, token }; ++activity.current; active.current = null; previousIds.current = new Set();
    ++revealSequence.current; cancelAnimationFrame(revealFrame.current);
  }
  const safe = !!actor && !!token && scopeOwner.current.actor === actor && scopeOwner.current.token === token;
  const visibleEntries = safe ? entries.filter(entry => entry.scope === scope) : [];
  const ids = new Set(visibleEntries.map(entry => entry.id));
  const valid = () => currentIdentity.current === identity && actor === api.getCurrentUserId() && token === api.getAccessToken();
  const register = useRef((id: string, control: Controls) => {
    controls.current.set(id, control);
    return () => { if (controls.current.get(id) === control) controls.current.delete(id); };
  }).current;
  const onModeChange = useRef((id: string, mode: WorkSurfaceMode, ownerIdentity: string) => {
    if (currentIdentity.current !== ownerIdentity) return;
    const key = ownerIdentity + ':' + id;
    setViews(previous => previous[key] === mode ? previous : { ...previous, [key]: mode });
  }).current;
  const activate = (id: string) => {
    if (!valid() || !ids.has(id)) return;
    if (active.current !== id) { active.current = id; setActiveEntry({ identity, id }); ++activity.current; liveCallback.current?.(id); }
  };
  const noteIntent = () => { ++activity.current; ++revealSequence.current; cancelAnimationFrame(revealFrame.current); };
  const reveal = (id: string) => {
    if (!valid() || !ids.has(id)) return;
    activate(id);
    let previousModal = false;
    for (const [otherId, control] of controls.current) if (otherId !== id && control.mode() === 'expanded') { previousModal = true; control.restoreBeside(); }
    const target = controls.current.get(id);
    if (target?.mode() === 'minimized') target.resume(); else if (!previousModal) target?.focus();
    // Another card may still be isolated by the previous full-screen dialog
    // until its layout cleanup runs. Focus only this still-current reveal.
    cancelAnimationFrame(revealFrame.current);
    const request = ++revealSequence.current, expectedActivity = activity.current;
    revealFrame.current = requestAnimationFrame(() => {
      if (!valid() || request !== revealSequence.current || expectedActivity !== activity.current || active.current !== id || controls.current.get(id) !== target) return;
      target?.focus();
    });
  };
  const liveReveal = useRef(reveal); liveReveal.current = reveal;
  useEffect(() => () => { ++revealSequence.current; cancelAnimationFrame(revealFrame.current); }, []);
  useEffect(() => {
    const changed = () => { ++activity.current; active.current = null; setActiveEntry({ identity: currentIdentity.current, id: null }); refreshAuthority(value => value + 1); };
    window.addEventListener('freecollab:auth-changed', changed);
    window.addEventListener('freecollab:workspace-changed', changed);
    return () => { window.removeEventListener('freecollab:auth-changed', changed); window.removeEventListener('freecollab:workspace-changed', changed); };
  }, []);
  useEffect(() => {
    const focus = (event: FocusEvent) => {
      if (!(event.target instanceof HTMLElement)) return;
      const card = event.target.closest<HTMLElement>('[data-collaboration-work-id]');
      if (card && root.current?.contains(card)) return;
      if (root.current?.querySelector('[data-collaboration-work-dock]')?.contains(event.target)) return;
      if (active.current !== null) { active.current = null; setActiveEntry({ identity: currentIdentity.current, id: null }); ++activity.current; }
    };
    document.addEventListener('focusin', focus);
    return () => document.removeEventListener('focusin', focus);
  }, []);
  useLayoutEffect(() => {
    const added = visibleEntries.filter(entry => !previousIds.current.has(entry.id));
    previousIds.current = new Set(visibleEntries.map(entry => entry.id));
    if (added.length) liveReveal.current(added[added.length - 1].id);
  }, [identity, entries]);
  useLayoutEffect(() => {
    const explicit = lastExternalActivation.current.identity !== identity || lastExternalActivation.current.key !== activationKey;
    lastExternalActivation.current = { identity, key: activationKey };
    // Reporting an ordinary card focus to the parent is not a request to
    // focus its outer region again after a writing menu has opened.
    if (activeId && (explicit || active.current !== activeId)) liveReveal.current(activeId);
  }, [activeId, activationKey, identity]);
  const parked = visibleEntries.filter(entry => views[identity + ':' + entry.id] === 'minimized');
  if (!visibleEntries.length) return null;
  return <section ref={root} className="fc-collaboration-workbench" aria-label={t('Open work')}>
    <div className="fc-collaboration-workbench-deck">
      {visibleEntries.map(entry => <WorkbenchCard key={identity + ':' + entry.id} entry={entry} identity={identity} isActive={activeEntry.identity === identity && activeEntry.id === entry.id} composerRef={composerRef} timelineRef={timelineRef} renderEntry={renderEntry}
        activity={() => activity.current} valid={valid} ownsActivity={() => active.current === entry.id} activate={activate} noteIntent={noteIntent} register={register} onModeChange={onModeChange} />)}
    </div>
    {!!parked.length && <div className="fc-collaboration-workbench-dock" data-collaboration-work-dock role="group" aria-label={t('Work set aside')}>
      <span className="fc-workbench-dock-label"><Layers size={15} aria-hidden="true" />{t('Work kept open')}</span>
      {parked.map(entry => <div key={entry.id} className="fc-collaboration-dock-entry"><button type="button" className="fc-work-resume" data-collaboration-resume={entry.id} aria-label={t('Continue {title}', { title: entry.title })} onClick={() => reveal(entry.id)}>
        <FileText size={16} aria-hidden="true" /><span>{entry.title}</span><ArrowUpRight size={15} aria-hidden="true" />
      </button>{onGuardedClose && <button type="button" className="fc-icon-button" data-collaboration-close={entry.id} aria-label={t('Close {title}', { title: entry.title })} onClick={() => { if (valid()) onGuardedClose(entry); }}><X size={17} aria-hidden="true" /></button>}</div>)}
    </div>}
  </section>;
}
