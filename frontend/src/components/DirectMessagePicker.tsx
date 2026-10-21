import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Archive, ArchiveRestore, Ban, CheckCircle2, ChevronRight, Loader2, MessageCircle, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { workspaceApi, type DirectMessage, type Workspace, type WorkspaceMember } from '../lib/workspace-api';
import { useModalFocus } from '../lib/useModalFocus';
import { getSecureMessaging } from '../lib/secure-client';

interface DirectMessagePickerProps { workspace: Workspace; onClose: () => void; onOpen: (channel: Channel) => void; }
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'Please try again in a moment.';

export function DirectMessagePicker({ workspace, onClose, onOpen }: DirectMessagePickerProps) {
  useI18n();
  const dialog = useRef<HTMLElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  useModalFocus(dialog, onClose);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [conversations, setConversations] = useState<DirectMessage[]>([]);
  const [query, setQuery] = useState('');
  const [archived, setArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [blocking, setBlocking] = useState<{ id: string; name: string } | null>(null);
  const identity = useRef(api.getCurrentUserId());
  const mounted = useRef(true);
  const generation = useRef(0);
  const encryptionPending = useRef(new Set<string>());
  const current = () => mounted.current && identity.current === api.getCurrentUserId();
  const normalized = query.trim().toLocaleLowerCase();
  const existingPeople = new Set(conversations.map(item => item.other_user.id));
  const matches = (name: string) => name.toLocaleLowerCase().includes(normalized);
  const visibleConversations = conversations.filter(item => item.archived === archived && matches(item.other_user.name));
  const availablePeople = members.filter(person => person.user_id !== identity.current && !existingPeople.has(person.user_id) && matches(person.name));
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);

  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; generation.current += 1; }; }, [workspace.id]);

  async function load() {
    const request = ++generation.current;
    setLoading(true); setError('');
    try {
      const [people, records] = await Promise.all([workspaceApi.members(workspace.id), workspaceApi.directMessages(workspace.id)]);
      if (!current() || request !== generation.current) return;
      setMembers(people); setConversations(records);
    } catch (failure) { if (current() && request === generation.current) setError(describe(failure)); }
    finally { if (current() && request === generation.current) setLoading(false); }
  }

  async function run(key: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(key); setError(''); setNotice('');
    try { await operation(); }
    catch (failure) { if (current()) setError(describe(failure)); }
    finally { if (current()) setBusy(''); }
  }

  async function open(personId: string) {
    await run(personId, async () => {
      const availability = await api.request<{ enabled: boolean }>('/secure/status');
      if (!current()) return;
      const channel = await workspaceApi.openDirectMessage(workspace.id, personId);
      if (!current()) return;
      if (availability.enabled && (channel.created || encryptionPending.current.has(channel.id) || channel.secure && !channel.matrix_room_id)) {
        encryptionPending.current.add(channel.id);
        const person = members.find(item => item.user_id === personId);
        try {
          const secureMessaging = await getSecureMessaging();
          if (!current()) return;
          const binding = await secureMessaging.enableChannel(channel.id, person?.name || 'Direct conversation');
          if (!current()) return;
          channel.secure = true; channel.matrix_room_id = binding.room_id || undefined; encryptionPending.current.delete(channel.id);
        } catch (failure) {
          if (current()) await load();
          throw new Error(`Encryption setup could not finish. Try opening this conversation again to retry. ${describe(failure)}`);
        }
      }
      if (current()) onOpen(channel);
    });
  }

  async function archive(conversation: DirectMessage) {
    await run(conversation.id, async () => {
      await workspaceApi.archiveDirectMessage(conversation.id, !conversation.archived);
      if (!current()) return;
      setConversations(previous => previous.map(item => item.id === conversation.id ? { ...item, archived: !item.archived } : item));
      setNotice(conversation.archived ? t("Conversation restored to your list.") : t("Conversation archived for you. Its messages are kept."));
      window.dispatchEvent(new Event('freecollab:channels-updated'));
    });
  }

  async function blockPerson(id: string, blocked: boolean) {
    await run(id, async () => {
      if (blocked) await workspaceApi.unblockPerson(id); else await workspaceApi.blockPerson(id);
      if (!current()) return;
      setConversations(previous => previous.map(item => item.other_user.id === id ? { ...item, blocked: !blocked } : item));
      setBlocking(null); setNotice(blocked ? t("Person unblocked.") : t("Person blocked. Existing messages are still available."));
    });
  }

  return createPortal(<div className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} className="fc-account-panel fc-dm-panel" role="dialog" aria-modal="true" aria-label={t("Direct messages")} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">{workspace.name}</span><h2>{t("A conversation, just for two.")}</h2><p>{t("Find someone in this workspace.")}</p></div><button data-autofocus className="fc-icon-button" type="button" onClick={onClose} aria-label={t("Close direct messages")}><X size={20} /></button></header>
      <div className="fc-account-body">
        {error && <div ref={errorRef} className="fc-alert fc-account-error" role="alert" tabIndex={-1}><AlertCircle size={17} /><p>{error}</p></div>}{notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
        <div className="fc-dm-search"><label htmlFor="direct-message-search" className="fc-visually-hidden">{t("Find workspace people or conversations")}</label><input className="fc-input" id="direct-message-search" type="search" placeholder={t("Find a person…")} value={query} onChange={event => setQuery(event.target.value)} /></div>
        {loading ? <p className="fc-account-loading" role="status"><Loader2 size={19} className="fc-account-spinner" />{t("Loading your conversations…")}</p> : <>
          <div className="fc-dm-heading"><h3>{archived ? t("Archived conversations") : t("Your conversations")}</h3><div className="fc-dm-person-actions"><button type="button" className="fc-account-link" onClick={() => setArchived(value => !value)}>{archived ? t("Show active") : t("Archived")}</button><button type="button" className="fc-icon-button" aria-label={t("Refresh direct messages")} disabled={!!busy} onClick={() => void load()}><RefreshCw size={15} /></button></div></div>
          <ul className="fc-dm-list">{visibleConversations.map(conversation => <li key={conversation.id}><button className="fc-dm-open" type="button" disabled={!!busy || conversation.blocked} onClick={() => void open(conversation.other_user.id)}><span className="fc-workspace-person-avatar" aria-hidden="true">{conversation.other_user.name.slice(0, 1).toUpperCase()}</span><div><strong>{conversation.other_user.name}</strong><small>{conversation.blocked ? t("Blocked by you") : conversation.secure ? t("Encrypted conversation") : t("Direct conversation")}</small></div>{busy === conversation.other_user.id && <Loader2 size={15} className="fc-account-spinner" />}</button><div className="fc-dm-person-actions"><button type="button" className="fc-icon-button" disabled={!!busy} aria-label={t("{value0} conversation with {value1}", { value0: String(conversation.archived ? 'Restore' : 'Archive'), value1: String(conversation.other_user.name) })} onClick={() => void archive(conversation)}>{conversation.archived ? <ArchiveRestore size={16} /> : <Archive size={16} />}</button><button type="button" className="fc-icon-button" disabled={!!busy} aria-label={t("{value0} {value1}", { value0: String(conversation.blocked ? 'Unblock' : 'Block'), value1: String(conversation.other_user.name) })} onClick={() => conversation.blocked ? void blockPerson(conversation.other_user.id, true) : setBlocking(conversation.other_user)}>{conversation.blocked ? <ShieldCheck size={16} /> : <Ban size={16} />}</button></div></li>)}</ul>
          {!visibleConversations.length && <p className="fc-dm-empty">{query ? t("No conversations match this name.") : archived ? t("Your archived conversations will appear here.") : t("Start with someone below. Your conversations will appear here.")}</p>}
          {!archived && <><div className="fc-dm-heading"><h3>{t("Start a conversation")}</h3><MessageCircle size={16} aria-hidden="true" /></div><ul className="fc-dm-list">{availablePeople.map(person => <li key={person.user_id}><button type="button" className="fc-dm-open" disabled={!!busy} onClick={() => void open(person.user_id)}><span className="fc-workspace-person-avatar" aria-hidden="true">{person.name.slice(0, 1).toUpperCase()}</span><div><strong>{person.name}</strong><small>{person.role === 'guest' ? t("Guest") : t("Workspace member")}</small></div></button>{busy === person.user_id ? <Loader2 size={16} className="fc-account-spinner" /> : <ChevronRight size={15} aria-hidden="true" />}</li>)}</ul>{!availablePeople.length && <p className="fc-dm-empty">{query ? t("No other workspace people match this name.") : t("Everyone available to you is already in your conversation list.")}</p>}</>}
          {blocking && <div className="fc-account-reauth"><h3>{t("Block ")}{blocking.name}?</h3><p>{t("You will no longer exchange direct messages while this block is active. Messages already received remain on your devices.")}</p><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setBlocking(null)}>{t("Cancel")}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => void blockPerson(blocking.id, false)}>{t("Block person")}</button></div></div>}
        </>}
      </div><footer className="fc-account-footer"><MessageCircle size={15} /><span>{t("People are limited to this workspace.")}</span><button className="fc-button" type="button" onClick={onClose}>{t("Done")}</button></footer>
    </section>
  </div>, document.body);
}
