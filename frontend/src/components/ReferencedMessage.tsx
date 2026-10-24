import { Fragment, useEffect, useRef, useState } from 'react';
import { AtSign, CheckSquare, FileText, Hash, Paperclip, Users } from 'lucide-react';
import { api } from '../lib/api';
import { useCollaborationContext } from '../lib/collaboration-context';
import { t, useI18n } from '../i18n';
import { messageReferencesApi } from '../lib/message-references-api';
import { normalizeMessageReferences, referenceKey, type MessageReference, type ResolvedReference } from '../lib/message-references';
import '../styles/message-references.css';

export interface ReferenceOpenLease { isCurrent(): boolean; signal: AbortSignal; element: HTMLElement }

export interface ReferencedMessageProps {
  content: string; references?: MessageReference[]; channelId: string; workspaceId?: string; secure?: boolean;
  onOpen?: (reference: ResolvedReference, lease: ReferenceOpenLease) => void | Promise<void>;
}
const icons = { person: AtSign, group: Users, document: FileText, task: CheckSquare, file: Paperclip, channel: Hash };

/** Literal text remains readable; typed targets resolve through current access. */
export function ReferencedMessage({ content, references, channelId, workspaceId, secure = false, onOpen }: ReferencedMessageProps) {
  useI18n();
  const collaboration = useCollaborationContext();
  const valid = normalizeMessageReferences(content, references);
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const scope = JSON.stringify([actor, token, workspaceId, channelId, secure, content, valid]);
  const latest = useRef(scope); latest.current = scope;
  const mounted = useRef(false), openSequence = useRef(0), openAbort = useRef<AbortController | null>(null);
  const clearOpenIntent = useRef<(() => void) | null>(null);
  const latestOnOpen = useRef(onOpen); latestOnOpen.current = onOpen;
  const cancelOpening = () => { ++openSequence.current; openAbort.current?.abort(); openAbort.current = null; clearOpenIntent.current?.(); clearOpenIntent.current = null; };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; cancelOpening(); };
  }, []);
  const [snapshot, setSnapshot] = useState<{ scope: string; rows: ResolvedReference[] } | null>(null);
  const [opening, setOpening] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => {
    cancelOpening(); setOpening(''); setNotice('');
    if (!valid.length) return;
    const abort = new AbortController(); let live = true;
    const current = () => live && latest.current === scope && token === api.getAccessToken() && actor === api.getCurrentUserId();
    const load = async () => {
      try { const rows = await messageReferencesApi.resolve(channelId, valid, abort.signal, secure); if (current()) setSnapshot({ scope, rows }); }
      catch { if (current()) setSnapshot({ scope, rows: [] }); }
    };
    const clear = () => { live = false; abort.abort(); cancelOpening(); setSnapshot(null); setOpening(''); setNotice(''); };
    const refresh = () => { if (!document.hidden) void load(); };
    const changed = (event: Event) => { if ((event as CustomEvent<{ channelId: string }>).detail?.channelId === channelId) void load(); };
    window.addEventListener('freecollab:auth-changed', clear); window.addEventListener('freecollab:workspace-changed', clear); window.addEventListener('focus', refresh); window.addEventListener('freecollab:references-changed', changed); void load();
    return () => { live = false; abort.abort(); cancelOpening(); window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:workspace-changed', clear); window.removeEventListener('focus', refresh); window.removeEventListener('freecollab:references-changed', changed); };
  }, [scope]);
  if (!valid.length) return <>{content}</>;
  const rows = snapshot?.scope === scope && token === api.getAccessToken() ? snapshot.rows : [];
  const open = async (reference: MessageReference, element: HTMLElement) => {
    if (!mounted.current || !latestOnOpen.current || openAbort.current || opening || latest.current !== scope || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) return;
    const key = referenceKey(reference), request = ++openSequence.current, controller = new AbortController(), providerCurrent = collaboration?.isCurrent;
    openAbort.current = controller; setOpening(key); setNotice('');
    const current = () => mounted.current && request === openSequence.current && latest.current === scope
      && token === api.getAccessToken() && actor === api.getCurrentUserId() && (!providerCurrent || providerCurrent());
    const changedIntent = (event: Event) => {
      if (event instanceof KeyboardEvent && ['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'NumLock'].includes(event.key)) return;
      if (request !== openSequence.current) return;
      cancelOpening();
      if (mounted.current && latest.current === scope) setOpening('');
    };
    const detachIntent = () => { document.removeEventListener('pointerdown', changedIntent, true); document.removeEventListener('keydown', changedIntent, true); };
    clearOpenIntent.current = detachIntent;
    document.addEventListener('pointerdown', changedIntent, true); document.addEventListener('keydown', changedIntent, true);
    try {
      const fresh = (await messageReferencesApi.resolve(channelId, [reference], controller.signal, secure))[0];
      if (!current()) return;
      if (!fresh?.available) { setSnapshot({ scope, rows: rows.filter(row => referenceKey(row) !== key) }); setNotice('This reference is no longer available.'); return; }
      await latestOnOpen.current?.(fresh, { isCurrent: current, signal: controller.signal, element });
    } catch { if (current()) setNotice('This reference could not be opened. Try again.'); }
    finally {
      detachIntent(); if (clearOpenIntent.current === detachIntent) clearOpenIntent.current = null;
      if (openAbort.current === controller) openAbort.current = null;
      if (current()) setOpening('');
    }
  };
  let offset = 0;
  return <>{valid.map((reference, index) => {
    const before = content.slice(offset, reference.start), label = content.slice(reference.start, reference.end);
    offset = reference.end;
    const resolved = rows.find(row => referenceKey(row) === referenceKey(reference) && row.available);
    const Icon = icons[reference.kind];
    return <Fragment key={reference.kind + ':' + reference.id + ':' + index}>{before}{resolved && onOpen ? <button type="button" className="fc-message-reference" data-reference-kind={reference.kind} data-reference-id={reference.id}
      disabled={!!opening} title={resolved.label || label} aria-label={t('Open reference {name}', { name: resolved.label || label.slice(1) })} onClick={event => void open(reference, event.currentTarget)}><Icon size={12} aria-hidden="true" /><span>{label}</span></button>
      : <span className="fc-message-reference is-unavailable" data-reference-kind={reference.kind} data-reference-id={reference.id} title={t('Reference unavailable')}>{label}</span>}</Fragment>;
  })}{content.slice(offset)}{notice && <span className="fc-message-reference-notice" role="status">{t(notice)}</span>}</>;
}
