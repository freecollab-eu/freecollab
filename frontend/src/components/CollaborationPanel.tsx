import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUpRight, Bell, BellOff, Bookmark, Check, CheckCircle2, ChevronDown, ClipboardCheck, History, Loader2, Pin, Plus, RefreshCw, X } from 'lucide-react';
import { api, type Channel, type ChannelMember, type Message } from '../lib/api';
import { collaborationApi, type ChannelNotificationSettings, type CollaborationTab, type Decision, type DecisionFields, type SavedItem } from '../lib/collaboration-api';
import { useModalFocus } from '../lib/useModalFocus';
import { VersionProofs } from './VersionProofs';
import { chatProductivity } from '../lib/chat-productivity';
import '../styles/panels.css';
import '../styles/collaboration.css';

interface CollaborationPanelProps {
  channel: Channel;
  tab: CollaborationTab;
  onClose: () => void;
  onOpenMessage?: (channelId: string, messageId: string) => void;
  sourceMessage?: Message | null;
  focusDecisionId?: string | null;
}
const tabs: Array<{ id: CollaborationTab; label: string; icon: typeof Pin }> = [
  { id: 'pins', label: 'Pinned', icon: Pin }, { id: 'bookmarks', label: 'Saved', icon: Bookmark },
  { id: 'decisions', label: 'Decisions', icon: ClipboardCheck }, { id: 'notifications', label: 'Notifications', icon: Bell },
];
const describe = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';
const dateLabel = (value: string) => new Date(value).toLocaleDateString(currentLocale(), { day: 'numeric', month: 'short', year: 'numeric' });
const emptyFields = (source?: Message | null): DecisionFields => ({ title: '', content: source?.content || '', status: 'proposed', responsible_user_id: null, decision_date: null, source_message_id: source?.id || null });
const announceSaved = (channelId: string) => window.dispatchEvent(new CustomEvent('freecollab:saved-changed', { detail: { channelId } }));

export function CollaborationPanel({ channel, tab, onClose, onOpenMessage, sourceMessage, focusDecisionId }: CollaborationPanelProps) {
  useI18n();
  const [active, setActive] = useState(tab);
  const [items, setItems] = useState<SavedItem[]>([]);
  const [records, setRecords] = useState<Decision[]>([]);
  const [members, setMembers] = useState<ChannelMember[]>([]);
  const [settings, setSettings] = useState<ChannelNotificationSettings>({ level: 'mentions', email_digest: 'none' });
  const [editor, setEditor] = useState<{ previous?: Decision; fields: DecisionFields } | null>(null);
  const [history, setHistory] = useState<{ id: string; records: Decision[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dialog = useRef<HTMLElement>(null);
  const generation = useRef(0);
  const identity = useRef(api.getAccessToken());
  const focusedDecision = useRef('');
  useModalFocus(dialog, onClose);
  const current = (request: number) => generation.current === request && identity.current === api.getAccessToken();
  const isAdmin = channel.role === 'admin' || members.find(member => member.user_id === api.getCurrentUserId())?.role === 'admin';

  useEffect(() => { setActive(tab); }, [tab]);
  useEffect(() => {
    if (focusDecisionId) setActive('decisions');
  }, [focusDecisionId]);
  useEffect(() => {
    const key = channel.id + ':' + focusDecisionId;
    if (loading || active !== 'decisions' || !focusDecisionId || focusedDecision.current === key || !records.some(record => record.id === focusDecisionId)) return;
    const frame = requestAnimationFrame(() => {
      const card = dialog.current?.querySelector<HTMLElement>('#decision-' + CSS.escape(focusDecisionId));
      if (card) { card.scrollIntoView({ block: 'center' }); card.focus({ preventScroll: true }); focusedDecision.current = key; }
    });
    return () => cancelAnimationFrame(frame);
  }, [channel.id, focusDecisionId, loading, active, records]);
  useEffect(() => {
    if (sourceMessage && sourceMessage.channel_id === channel.id) { setActive('decisions'); setEditor({ fields: emptyFields(sourceMessage) }); }
  }, [sourceMessage?.id, channel.id]);
  useEffect(() => {
    const request = ++generation.current;
    setItems([]); setRecords([]); setMembers([]); setHistory(null); setError(''); setNotice(''); setBusy('');
    setEditor(sourceMessage?.channel_id === channel.id && active === 'decisions' ? { fields: emptyFields(sourceMessage) } : null);
    void load(request);
    const changed = (event: Event) => {
      const changedChannel = (event as CustomEvent<{ channelId: string }>).detail?.channelId;
      if (active === 'bookmarks' || active === 'pins' && changedChannel === channel.id) void load();
    };
    window.addEventListener('freecollab:saved-changed', changed);
    return () => { ++generation.current; window.removeEventListener('freecollab:saved-changed', changed); };
  }, [channel.id, active]);

  async function load(request = generation.current) {
    setLoading(true); setError('');
    try {
      if (active === 'pins' || active === 'bookmarks') {
        const result = active === 'pins' ? await collaborationApi.pins(channel.id) : await collaborationApi.bookmarks();
        if (current(request)) setItems(result);
      } else if (active === 'decisions') {
        const result = await Promise.allSettled([collaborationApi.decisions(channel.id), api.getChannelMembers(channel.id)]);
        if (!current(request)) return;
        if (result[0].status === 'fulfilled') setRecords(result[0].value); else setError(describe(result[0].reason));
        if (result[1].status === 'fulfilled') setMembers(result[1].value); else setError(describe(result[1].reason));
      } else {
        const result = await collaborationApi.settings(channel.id);
        if (current(request)) setSettings(result);
      }
    } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setLoading(false); }
  }

  async function run(key: string, operation: (request: number) => Promise<void>) {
    if (busy) return;
    const request = generation.current;
    setBusy(key); setError(''); setNotice('');
    try { await operation(request); }
    catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setBusy(''); }
  }

  function openMessage(channelId: string, messageId: string) { onOpenMessage?.(channelId, messageId); onClose(); }

  async function saveDecision(event: FormEvent) {
    event.preventDefault();
    if (!editor) return;
    await run('decision', async request => {
      const fields = { ...editor.fields, title: editor.fields.title.trim(), content: editor.fields.content.trim() };
      const result = editor.previous ? await collaborationApi.reviseDecision(editor.previous, fields) : await collaborationApi.createDecision(channel.id, fields);
      if (!current(request)) return;
      setEditor(null); setHistory(null); setRecords(existing => [result, ...existing.filter(record => record.id !== result.id)]);
      setNotice(editor.previous ? t("A new revision has been added. Earlier revisions are preserved.") : t("Decision recorded for this channel."));
    });
  }

  return createPortal(<div className="fc-collaboration-layer" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("Channel notebook")} className="fc-collaboration-panel" aria-busy={loading}>
      <header className="fc-collaboration-header"><div><span className="fc-kicker">{channel.name || t("Conversation")}</span><h2>{t("The useful things.")}</h2><p>{t("A little structure. A clearer conversation.")}</p></div><button data-autofocus type="button" className="fc-icon-button" aria-label={t("Close channel notebook")} onClick={onClose}><X size={19} /></button></header>
      <nav className="fc-collaboration-tabs" aria-label={t("Channel notebook sections")}>{tabs.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-current={active === id ? 'page' : undefined} onClick={() => setActive(id)}><Icon size={15} /><span>{label}</span></button>)}</nav>
      <div className="fc-collaboration-body">
        <div className="fc-collaboration-toolbar"><span className="fc-kicker">{active === 'pins' ? t("Shared with the channel") : active === 'bookmarks' ? t("Only you can see these") : active === 'decisions' ? t("A record of what matters") : t("Set your own pace")}</span><button type="button" className="fc-text-button" disabled={loading || !!busy} onClick={() => void load()} aria-label={t("Refresh channel notebook")}><RefreshCw size={14} className={loading ? 'fc-refreshing' : ''} />{t("Refresh")}</button></div>
        {error && <div className="fc-alert fc-collaboration-error" role="alert">{error}{active === 'decisions' && <button type="button" className="fc-text-button" onClick={() => void load()}>{t("Reload current decisions")}</button>}</div>}
        {notice && <div className="fc-collaboration-notice" role="status"><CheckCircle2 size={16} /><span>{notice}</span></div>}
        {loading ? <div className="fc-collaboration-empty" role="status"><Loader2 className="fc-collaboration-spinner" size={23} /><p>{t("Gathering the useful things…")}</p></div> : <>
          {(active === 'pins' || active === 'bookmarks') && <>{items.length ? <ul className="fc-saved-list">{items.map(item => <li className="fc-saved-card" key={`${item.channel_id}:${item.item_id}`}>
            <div className="fc-saved-meta"><span>{active === 'pins' ? <Pin size={13} /> : <Bookmark size={13} />}{active === 'bookmarks' && item.channel_id !== channel.id ? t("Another conversation") : channel.name || t("Conversation")}</span><time dateTime={item.created_at}>{dateLabel(item.created_at)}</time></div>
            {item.available && item.message ? <p className="fc-saved-content">{item.message.content}</p> : <p className="fc-saved-unavailable">{item.resolution_error || t("This message is no longer available.")}</p>}
            <div className="fc-saved-actions">{active==='bookmarks'&&<button type="button" className="fc-text-button" disabled={!!busy} aria-pressed={!!item.completed_at} onClick={()=>void run('complete-'+item.item_id,async request=>{await chatProductivity.completeSaved(item.channel_id,item.item_id,!item.completed_at);if(current(request))setItems(rows=>rows.map(row=>row.channel_id===item.channel_id&&row.item_id===item.item_id?{...row,completed_at:item.completed_at?null:new Date().toISOString()}:row));})}><Check size={14}/>{item.completed_at?t('Completed'):t('Mark complete')}</button>}{item.available && item.message && onOpenMessage && <button type="button" className="fc-text-button" onClick={() => openMessage(item.channel_id, item.message!.id)}>{t("Open conversation")}<ArrowUpRight size={13} /></button>}{(active === 'bookmarks' || isAdmin) && <button type="button" className="fc-text-button fc-saved-remove" disabled={!!busy} onClick={() => void run(item.item_id, async request => { if (active === 'pins') await collaborationApi.unpin(item.channel_id, item.item_id); else await collaborationApi.removeBookmark(item.channel_id, item.item_id); if (current(request)) { setItems(existing => existing.filter(saved => saved.channel_id !== item.channel_id || saved.item_id !== item.item_id)); setNotice(active === 'pins' ? t("Message unpinned.") : t("Message removed from your saved items.")); announceSaved(item.channel_id); } })}>{busy === item.item_id ? t("Removing…") : active === 'pins' ? t("Unpin") : t("Remove")}</button>}</div>
          </li>)}</ul> : <div className="fc-collaboration-empty">{active === 'pins' ? <Pin size={25} strokeWidth={1.25} /> : <Bookmark size={25} strokeWidth={1.25} />}<h3>{active === 'pins' ? t("Keep the essentials close.") : t("Keep it for later.")}</h3><p>{active === 'pins' ? t("Channel admins can pin useful messages from the conversation. Everyone here can find them again.") : t("Save a message from any conversation. Your collection stays private.")}</p></div>}</>}
          {active === 'decisions' && <>
            {!editor && <button type="button" className="fc-button fc-collaboration-new" onClick={() => { setEditor({ fields: emptyFields() }); setHistory(null); }}><Plus size={16} />{t("Record a decision")}</button>}
            {editor && <form className="fc-decision-editor" onSubmit={saveDecision}><div className="fc-decision-editor-heading"><h3>{editor.previous ? t("Revise decision · {value0}", { value0: String(editor.previous.revision + 1) }) : t("A decision worth keeping.")}</h3><button type="button" className="fc-icon-button" aria-label={t("Cancel decision editor")} disabled={!!busy} onClick={() => setEditor(null)}><X size={16} /></button></div>
              <label htmlFor="decision-title">{t("Title")}</label><input id="decision-title" className="fc-input" required maxLength={200} value={editor.fields.title} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, title: event.target.value } })} placeholder={t("What did we agree?")} />
              <label htmlFor="decision-content">{t("Decision and context")}</label><textarea id="decision-content" className="fc-input" required rows={5} maxLength={20000} value={editor.fields.content} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, content: event.target.value } })} placeholder={t("The choice, the reason, and what happens next.")} />
              <div className="fc-decision-fields"><div><label htmlFor="decision-status">{t("Status")}</label><select id="decision-status" className="fc-input" value={editor.fields.status} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, status: event.target.value as DecisionFields['status'] } })}><option value="proposed">{t("Proposed")}</option><option value="accepted">{t("Accepted")}</option><option value="superseded">{t("Superseded")}</option></select></div><div><label htmlFor="decision-date">{t("Decision date")}</label><input id="decision-date" className="fc-input" type="date" value={editor.fields.decision_date || ''} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, decision_date: event.target.value || null } })} /></div></div>
              <label htmlFor="decision-responsible">{t("Responsible person")}</label><select id="decision-responsible" className="fc-input" value={editor.fields.responsible_user_id || ''} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, responsible_user_id: event.target.value || null } })}><option value="">{t("No one assigned")}</option>{members.map(member => <option key={member.user_id} value={member.user_id}>{member.name}</option>)}</select>
              {editor.fields.source_message_id && <p className="fc-decision-source"><ClipboardCheck size={14} />{t("Linked to the original message.")}</p>}
              <p className="fc-collaboration-caption">{editor.previous ? t("This adds a revision. The earlier record remains available.") : t("Visible to the current members of this conversation.")}</p><button type="submit" className="fc-button fc-button-primary" disabled={!!busy || !editor.fields.title.trim() || !editor.fields.content.trim()}>{busy === 'decision' ? <Loader2 size={15} className="fc-collaboration-spinner" /> : <Check size={15} />}{editor.previous ? t("Save revision") : t("Record decision")}</button>
            </form>}
            {records.length ? <ul className="fc-decision-list">{records.map(record => <li key={record.id} id={`decision-${record.id}`} tabIndex={-1} className="fc-decision-card"><div className="fc-decision-meta"><span className={`fc-decision-status is-${record.status || 'proposed'}`}>{record.status || t("Encrypted decision")}</span><span>{t("Revision ")}{record.revision}</span></div><h3>{record.title || t("Encrypted decision")}</h3>{record.resolution_error ? <p className="fc-saved-unavailable">{record.resolution_error}</p> : <p className="fc-decision-content">{record.content}</p>}<div className="fc-decision-details">{record.responsible_user_id && <span>{members.find(member => member.user_id === record.responsible_user_id)?.name || t("Assigned teammate")}</span>}<time dateTime={record.decision_date || record.created_at}>{record.decision_date || dateLabel(record.created_at)}</time></div><div className="fc-decision-actions">{!record.resolution_error && (record.created_by === api.getCurrentUserId() || isAdmin) && <button type="button" className="fc-text-button" onClick={() => { setEditor({ previous: record, fields: { title: record.title || '', content: record.content || '', status: record.status || 'proposed', responsible_user_id: record.responsible_user_id || null, decision_date: record.decision_date || null, source_message_id: record.source_message_id || null } }); setHistory(null); dialog.current?.querySelector('.fc-collaboration-body')?.scrollTo({ top: 0, behavior: 'smooth' }); }}>{t("Revise")}</button>}<button type="button" className="fc-text-button" disabled={!!busy} aria-expanded={history?.id === record.id} onClick={() => { if (history?.id === record.id) setHistory(null); else void run('history-' + record.id, async request => { const revisions = await collaborationApi.revisions(record.id); if (current(request)) setHistory({ id: record.id, records: revisions }); }); }}><History size={13} />{t("History")}<ChevronDown size={12} /></button>{record.source_message_id && onOpenMessage && <button type="button" className="fc-text-button" onClick={() => openMessage(record.channel_id, record.source_message_id!)}>{t("Source")}<ArrowUpRight size={12} /></button>}</div>{!record.resolution_error && <VersionProofs decision={record} />}{history?.id === record.id && <ol className="fc-decision-history" aria-label={t("Decision revision history")}>{history.records.map(revision => <li key={revision.revision_id}><span>{t("Revision ")}{revision.revision} · {dateLabel(revision.revised_at)}</span><strong>{revision.title || t("Encrypted revision")}</strong><p>{revision.resolution_error || revision.content}</p>{!revision.resolution_error && <VersionProofs decision={revision} />}</li>)}</ol>}</li>)}</ul> : !editor && <div className="fc-collaboration-empty"><ClipboardCheck size={25} strokeWidth={1.25} /><h3>{t("Make the next step clear.")}</h3><p>{t("Record agreements, give someone responsibility, and keep the reasons alongside the conversation.")}</p></div>}
          </>}
          {active === 'notifications' && <form className="fc-channel-notification-form" onSubmit={event => { event.preventDefault(); void run('settings', async request => { const saved = await collaborationApi.setSettings(channel.id, settings); if (current(request)) { setSettings(saved); setNotice(t("Your preferences for this conversation have been saved.")); } }); }}><fieldset><legend>{t("When should we let you know?")}</legend><p>{t("Choose what deserves your attention in this conversation.")}</p>{([{ level: 'all', title: 'Every message', description: t("Stay close to everything happening here."), icon: Bell }, { level: 'mentions', title: 'Mentions and watched threads', description: t("A gentle nudge when the conversation needs you."), icon: ClipboardCheck }, { level: 'muted', title: 'Quiet', description: t("Read in your own time. No in-app alerts."), icon: BellOff }] as const).map(({ level, title, description, icon: Icon }) => <label key={level} className={`fc-notification-choice ${settings.level === level ? 'is-selected' : ''}`}><Icon size={18} strokeWidth={1.5} /><span><strong>{title}</strong><small>{description}</small></span><input type="radio" name="channel-notification-level" value={level} checked={settings.level === level} disabled={!!busy} onChange={() => setSettings({ ...settings, level })} /></label>)}</fieldset><div className="fc-channel-digest"><label htmlFor="channel-digest">{t("Email summaries")}</label><p>{t("A simple activity count and a link back. Your message content stays out of email.")}</p><select id="channel-digest" className="fc-input" value={settings.email_digest} disabled={!!busy} onChange={event => setSettings({ ...settings, email_digest: event.target.value as ChannelNotificationSettings['email_digest'] })}><option value="none">{t("No email summaries")}</option><option value="daily">{t("Daily")}</option><option value="weekly">{t("Weekly")}</option></select><p className="fc-collaboration-caption">{t("Email summaries need email delivery to be enabled for your workspace.")}</p></div><button className="fc-button fc-button-primary" type="submit" disabled={!!busy}>{busy === 'settings' ? <Loader2 className="fc-collaboration-spinner" size={15} /> : <Check size={15} />}{t("Save preferences")}</button></form>}
        </>}
      </div>
    </section>
  </div>, document.body);
}

interface MessageSaveActionsProps {
  channel: Channel;
  message: Message;
  onDecision?: (message: Message) => void;
  onError?: (message: string) => void;
}

export function MessageSaveActions({ channel, message, onDecision, onError }: MessageSaveActionsProps) {
  useI18n();
  const [saved, setSaved] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0);
  const stateLoads = useRef(0);
  useEffect(() => {
    ++generation.current;
    setSaved(false); setPinned(false); setError(''); setBusy('');
    const load = () => {
      const request = ++stateLoads.current, lifecycle = generation.current, token = api.getAccessToken();
      void collaborationApi.savedState(channel.id).then(state => {
        if (request === stateLoads.current && lifecycle === generation.current && token === api.getAccessToken()) { setSaved(state.bookmarks.has(message.id)); setPinned(state.pins.has(message.id)); }
      }).catch(() => { /* Saving remains available if collection metadata is temporarily unavailable. */ });
    };
    const changed = (event: Event) => { if ((event as CustomEvent<{ channelId: string }>).detail?.channelId === channel.id) load(); };
    load(); window.addEventListener('freecollab:saved-changed', changed);
    return () => { ++generation.current; ++stateLoads.current; window.removeEventListener('freecollab:saved-changed', changed); };
  }, [channel.id, message.id]);
  async function run(kind: 'save' | 'pin') {
    if (busy) return;
    const request = generation.current, token = api.getAccessToken();
    setBusy(kind); setError('');
    try {
      if (kind === 'save') { if (saved) await collaborationApi.removeBookmark(channel.id, message.id); else await collaborationApi.bookmark(channel.id, message.id); }
      else { if (pinned) await collaborationApi.unpin(channel.id, message.id); else await collaborationApi.pin(channel.id, message.id); }
      if (request !== generation.current || token !== api.getAccessToken()) return;
      if (kind === 'save') setSaved(!saved); else setPinned(!pinned);
      announceSaved(channel.id);
    } catch (failure) { if (request === generation.current && token === api.getAccessToken()) { const text = describe(failure); if (onError) onError(text); else setError(text); } }
    finally { if (request === generation.current) setBusy(''); }
  }
  if (message.deleted) return null;
  return <span className="fc-message-save-actions"><button type="button" className="fc-message-save-button" aria-label={saved ? t("Remove saved message") : t("Save message")} title={saved ? t("Remove saved message") : t("Save for later")} aria-pressed={saved} disabled={!!busy} onClick={() => void run('save')}><Bookmark size={14} fill={saved ? 'currentColor' : 'none'} /></button>{channel.role === 'admin' && <button type="button" className="fc-message-save-button" aria-label={pinned ? t("Unpin message") : t("Pin message")} title={pinned ? t("Unpin message") : t("Pin for everyone")} aria-pressed={pinned} disabled={!!busy} onClick={() => void run('pin')}><Pin size={14} fill={pinned ? 'currentColor' : 'none'} /></button>}{onDecision && <button type="button" className="fc-message-save-button" aria-label={t("Record decision from message")} title={t("Record a decision")} onClick={() => onDecision(message)}><ClipboardCheck size={14} /></button>}{error && <span className="fc-message-save-error" role="alert">{error}</span>}</span>;
}

export function ThreadWatchButton({ channelId, messageId }: { channelId: string; messageId: string }) {
  useI18n();
  const [watching, setWatching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    const request = ++generation.current, token = api.getAccessToken();
    setLoading(true); setError('');
    void collaborationApi.threadWatch(channelId, messageId).then(result => { if (request === generation.current && token === api.getAccessToken()) setWatching(result.watching); }).catch(failure => { if (request === generation.current) setError(describe(failure)); }).finally(() => { if (request === generation.current) setLoading(false); });
    return () => { ++generation.current; };
  }, [channelId, messageId]);
  return <span className="fc-thread-watch"><button type="button" className="fc-text-button" aria-pressed={watching} disabled={loading} onClick={() => { const request = generation.current, token = api.getAccessToken(); setLoading(true); setError(''); void collaborationApi.setThreadWatch(channelId, messageId, !watching).then(result => { if (request === generation.current && token === api.getAccessToken()) setWatching(result.watching); }).catch(failure => { if (request === generation.current) setError(describe(failure)); }).finally(() => { if (request === generation.current) setLoading(false); }); }}>{loading ? <Loader2 className="fc-collaboration-spinner" size={13} /> : watching ? <Bell size={13} /> : <BellOff size={13} />}{watching ? t("Watching thread") : t("Watch thread")}</button>{error && <small role="alert">{error}</small>}</span>;
}
