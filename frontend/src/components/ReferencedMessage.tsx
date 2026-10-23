import { Fragment, useEffect, useRef, useState } from 'react';
import { AtSign, CheckSquare, FileText, Hash, Paperclip, Users } from 'lucide-react';
import { api } from '../lib/api';
import { t, useI18n } from '../i18n';
import { messageReferencesApi } from '../lib/message-references-api';
import { normalizeMessageReferences, referenceKey, type MessageReference, type ResolvedReference } from '../lib/message-references';
import '../styles/message-references.css';

export interface ReferencedMessageProps {
  content: string; references?: MessageReference[]; channelId: string; workspaceId?: string; secure?: boolean;
  onOpen?: (reference: ResolvedReference) => void;
}
const icons = { person: AtSign, group: Users, document: FileText, task: CheckSquare, file: Paperclip, channel: Hash };

/** Literal text remains readable; typed targets resolve through current access. */
export function ReferencedMessage({ content, references, channelId, workspaceId, secure = false, onOpen }: ReferencedMessageProps) {
  useI18n();
  const valid = normalizeMessageReferences(content, references);
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const scope = JSON.stringify([actor, token, workspaceId, channelId, secure, content, valid]);
  const latest = useRef(scope); latest.current = scope;
  const [snapshot, setSnapshot] = useState<{ scope: string; rows: ResolvedReference[] } | null>(null);
  const [opening, setOpening] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => {
    if (!valid.length) return;
    const abort = new AbortController(); let live = true;
    const current = () => live && latest.current === scope && token === api.getAccessToken() && actor === api.getCurrentUserId();
    const load = async () => {
      try { const rows = await messageReferencesApi.resolve(channelId, valid, abort.signal, secure); if (current()) setSnapshot({ scope, rows }); }
      catch { if (current()) setSnapshot({ scope, rows: [] }); }
    };
    const clear = () => { live = false; abort.abort(); setSnapshot(null); setOpening(''); setNotice(''); };
    const refresh = () => { if (!document.hidden) void load(); };
    const changed = (event: Event) => { if ((event as CustomEvent<{ channelId: string }>).detail?.channelId === channelId) void load(); };
    window.addEventListener('freecollab:auth-changed', clear); window.addEventListener('freecollab:workspace-changed', clear); window.addEventListener('focus', refresh); window.addEventListener('freecollab:references-changed', changed); void load();
    return () => { live = false; abort.abort(); window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:workspace-changed', clear); window.removeEventListener('focus', refresh); window.removeEventListener('freecollab:references-changed', changed); };
  }, [scope]);
  if (!valid.length) return <>{content}</>;
  const rows = snapshot?.scope === scope && token === api.getAccessToken() ? snapshot.rows : [];
  const open = async (reference: MessageReference) => {
    if (!onOpen || opening || latest.current !== scope || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) return;
    const key = referenceKey(reference); setOpening(key); setNotice('');
    try {
      const fresh = (await messageReferencesApi.resolve(channelId, [reference], undefined, secure))[0];
      if (latest.current !== scope || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) return;
      if (!fresh?.available) { setSnapshot({ scope, rows: rows.filter(row => referenceKey(row) !== key) }); setNotice('This reference is no longer available.'); return; }
      onOpen(fresh);
    } catch { if (latest.current === scope && token === api.getAccessToken()) setNotice('This reference could not be opened. Try again.'); }
    finally { if (latest.current === scope && token === api.getAccessToken()) setOpening(''); }
  };
  let offset = 0;
  return <>{valid.map((reference, index) => {
    const before = content.slice(offset, reference.start), label = content.slice(reference.start, reference.end);
    offset = reference.end;
    const resolved = rows.find(row => referenceKey(row) === referenceKey(reference) && row.available);
    const Icon = icons[reference.kind];
    return <Fragment key={reference.kind + ':' + reference.id + ':' + index}>{before}{resolved && onOpen ? <button type="button" className="fc-message-reference" data-reference-kind={reference.kind} data-reference-id={reference.id}
      disabled={!!opening} title={resolved.label || label} aria-label={t('Open reference {name}', { name: resolved.label || label.slice(1) })} onClick={() => void open(reference)}><Icon size={12} aria-hidden="true" /><span>{label}</span></button>
      : <span className="fc-message-reference is-unavailable" data-reference-kind={reference.kind} data-reference-id={reference.id} title={t('Reference unavailable')}>{label}</span>}</Fragment>;
  })}{content.slice(offset)}{notice && <span className="fc-message-reference-notice" role="status">{t(notice)}</span>}</>;
}
