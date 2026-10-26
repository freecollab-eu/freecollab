import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Download, Eye, FileText, Loader2, Search, X } from 'lucide-react';
import { api, type Channel, type Message } from '../lib/api';
import { getSecureMessaging } from '../lib/secure-client';
import { collaborationApi, type Decision } from '../lib/collaboration-api';
import { downloadChannelFile, listChannelFiles, wasCancelled, type ManagedFile } from '../lib/files-api';
import { FilePreview, canPreviewFile } from './FilePreview';
import { useModalFocus } from '../lib/useModalFocus';
import { loadSearchSources, sourceMatches, verifySearchSource, type SearchSource } from '../lib/enterprise-search';
import { EnterpriseSearchPanel } from './EnterpriseSearchPanel';
import '../styles/history-search.css';

interface Props { channels: Channel[]; onClose: () => void; onMessage: (message: Message) => void; initialQuery?: string }

export function historySourceKey(source: SearchSource): string {
  return JSON.stringify(['source', source.channel_id, source.kind, source.id]);
}

/** Full message history and decision projections own their live edit/delete state. */
export function historySupplementalSources(sources: SearchSource[], contentType = ''): SearchSource[] {
  const kind = contentType === 'decisions' ? 'decision' : contentType, seen = new Set<string>();
  return sources.filter(source => {
    if (source.kind === 'message' || source.kind === 'decision' && !(source.secure && source.encrypted_resource && source.encrypted_kind === 'decision')) return false;
    if (kind && source.kind !== kind) return false;
    const key = historySourceKey(source); if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

export function historySourceLabel(kind: SearchSource['kind']): string {
  return ({ document: 'Document content', project: 'Project', task: 'Task', feed: 'Activity feed', 'file-content': 'File content',
    message: 'Messages', decision: 'Decision', calendar: 'Calendar event', clip: 'Channel clips', workflow: 'Workflows', 'linked-document': 'Linked documents' })[kind];
}

/** Plaintext lives only in this account-scoped dialog, never in browser storage. */
export function HistorySearchPanel({ channels, onClose, onMessage, initialQuery = '' }: Props) {
  useI18n();
  const dialog = useRef<HTMLElement>(null);
  useModalFocus(dialog, onClose);
  const [query, setQuery] = useState(initialQuery);
  const [channelId, setChannelId] = useState('');
  const [author, setAuthor] = useState('');
  const [after, setAfter] = useState('');
  const [before, setBefore] = useState('');
  const [records, setRecords] = useState<Message[]>([]);
  const [files, setFiles] = useState<ManagedFile[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [documentId, setDocumentId] = useState('');
  const [sourcePreview, setSourcePreview] = useState<SearchSource | null>(null);
  const [contentType, setContentType] = useState('');
  const [fileType, setFileType] = useState('');
  const [relatedFailures, setRelatedFailures] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<ManagedFile | null>(null);
  const [downloadError, setDownloadError] = useState('');
  const [downloading, setDownloading] = useState('');
  const transfers = useRef(new Set<AbortController>());
  const urls = useRef(new Set<string>());
  const [names, setNames] = useState<Record<string, string>>({});
  const [complete, setComplete] = useState(0);
  const [syncing, setSyncing] = useState(true);
  const [failures, setFailures] = useState<string[]>([]);
  const [attempt, setAttempt] = useState(0);
  const scope = channels.map(channel => channel.id).sort().join(',');
  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let unsubscribe: (() => void) | undefined;
    const cursors = new Map<string, number>();
    const changes = new Map<string, Message>();
    const removed = new Set<string>();
    const token = api.getAccessToken();
    const current = () => live && token === api.getAccessToken();
    const merge = (rows: Message[]) => {
      for (const row of rows) { changes.set(row.id, row); if (row.deleted) removed.add(row.id); }
      setRecords(old => {
        const all = new Map(old.map(row => [row.id, row]));
        for (const row of rows) { if (row.deleted) all.delete(row.id); else all.set(row.id, row); }
        return [...all.values()];
      });
    };
    void getSecureMessaging().then(secure => {
      if (!current()) return;
      unsubscribe = secure.subscribe((event, data) => {
        if (!current() || !channels.some(channel => channel.id === data.channel_id)) return;
        if (event === 'new_message') merge([data as unknown as Message]);
        if (event === 'thread_reply') merge([data.reply as Message]);
        if (event === 'message_updated') {
          const id = String(data.id);
          setRecords(old => old.map(row => { if (row.id !== id) return row; const edited = {...row,content:String(data.content)}; changes.set(id,edited); return edited; }));
        }
        if (event === 'message_deleted') { const id = String(data.id || data.message_id); removed.add(id); setRecords(old => old.filter(row => row.id !== id)); }
      });
    });
    function clearChannel(channel: Channel) {
      setRecords(old => old.filter(row => row.channel_id !== channel.id));
      setFiles(old => old.filter(row => row.channel_id !== channel.id));
      setDecisions(old => old.filter(row => row.channel_id !== channel.id));
      setSources(old => old.filter(row => row.channel_id !== channel.id));
      setSourcePreview(old => old?.channel_id === channel.id ? null : old);
      setPreview(old => old?.channel_id === channel.id ? null : old);
    }
    async function loadRelated(channel: Channel) {
      const results = await Promise.allSettled([listChannelFiles(channel.id), collaborationApi.decisions(channel.id), loadSearchSources(channel, controller.signal)]);
      if (!current()) return;
      for (const [index, result] of results.entries()) {
        const key = `${channel.id}:${index}`;
        if (result.status === 'fulfilled') {
          if (index === 0) setFiles(old => [...old.filter(row => row.channel_id !== channel.id), ...result.value as ManagedFile[]]);
          else if (index === 1) setDecisions(old => [...old.filter(row => row.channel_id !== channel.id), ...(result.value as Decision[]).filter(row => !row.resolution_error)]);
          else {
            const fresh = (result.value as {items:SearchSource[]}).items;
            setSources(old => [...old.filter(row => row.channel_id !== channel.id), ...fresh]);
            setSourcePreview(old => old?.channel_id === channel.id && !fresh.some(row => row.kind === old.kind && row.id === old.id && row.version === old.version) ? null : old);
          }
          const unavailable = index === 1 && (result.value as Decision[]).some(row => row.resolution_error)
            || index === 2 && !(result.value as {complete:boolean}).complete;
          setRelatedFailures(old => { const next = {...old}; if (unavailable) next[key] = `${channel.name || 'Channel'}: ${index === 1 ? 'Some encrypted decisions could not be unlocked.' : 'Some content exceeded search limits or could not be verified; results are incomplete.'}`; else delete next[key]; return next; });
        } else {
          if (index === 0) { setFiles(old => old.filter(row => row.channel_id !== channel.id)); setPreview(old => old?.channel_id === channel.id ? null : old); }
          else if (index === 1) setDecisions(old => old.filter(row => row.channel_id !== channel.id));
          else { setSources(old => old.filter(row => row.channel_id !== channel.id)); setSourcePreview(old => old?.channel_id === channel.id ? null : old); }
          setRelatedFailures(old => ({...old, [key]: `${channel.name || 'Channel'}: ${index === 0 ? 'Files' : index === 1 ? 'Decisions' : 'Document content'} could not be loaded. ${result.reason instanceof Error ? result.reason.message : ''}`}));
        }
      }
    }
    async function synchronize(refreshRelated = true) {
      for (const channel of channels) {
        if (!current()) return;
        try {
          if (refreshRelated) await loadRelated(channel);
          if (!current()) return;
          if (await (await getSecureMessaging()).handles(channel.id)) continue;
          for (;;) {
            const update = await api.request<{messages:Message[];next_cursor:number;has_more:boolean}>(`/messages/channels/${channel.id}/sync?after=${cursors.get(channel.id) || 0}&limit=100`, {signal:controller.signal});
            if (!current()) return;
            merge(update.messages); cursors.set(channel.id,update.next_cursor);
            if (!update.has_more) break;
          }
        } catch (error) {
          if (!current() || controller.signal.aborted) return;
          // Failed authorization cannot leave previously decrypted search results visible.
          clearChannel(channel);
          setFailures(old => old.some(item => item.startsWith((channel.name || 'Channel') + ':')) ? old : [...old,`${channel.name || 'Channel'}: ${error instanceof Error ? error.message : 'History could not be synchronized.'}`]);
        }
      }
      if (current()) timer = setTimeout(() => { void synchronize(); },10000);
    }
    setRecords([]); setFiles([]); setDecisions([]); setSources([]); setSourcePreview(null); setPreview(null); setRelatedFailures({}); setDownloadError(''); setNames({}); setFailures([]); setComplete(0); setSyncing(true);
    (async () => {
      // Bound concurrency: one history request per channel and two channels at a time.
      const queue = [...channels];
      async function worker() {
        while (queue.length && current()) {
          const channel = queue.shift()!;
          try {
            const members = await api.getChannelMembers(channel.id);
            if (!current()) return;
            setNames(old => ({ ...old, ...Object.fromEntries(members.map(member => [member.user_id, member.name])) }));
            await loadRelated(channel);
            if (!current()) return;
            let cursor: string | undefined;
            const seen = new Set<string>();
            for (;;) {
              const page = await api.getMessages(channel.id, { before: cursor, limit: 100, signal: controller.signal });
              if (!current()) return;
              const fresh = page.filter(message => !seen.has(message.id));
              for (const message of fresh) seen.add(message.id);
              setRecords(old => { const all = new Map(old.map(row => [row.id,row])); for (const row of fresh) { const latest = changes.get(row.id) || row; if (!latest.deleted && !removed.has(row.id)) all.set(row.id,latest); } return [...all.values()]; });
              if (page.length < 100) break;
              const oldest = page[page.length - 1]?.id;
              if (!oldest || oldest === cursor || !fresh.length) throw new Error(t("History pagination did not advance."));
              cursor = oldest;
            }
          } catch (error) {
            if (!current() || controller.signal.aborted) return;
            clearChannel(channel);
            setFailures(old => [...old, `${channel.name || 'Channel'}: ${error instanceof Error ? error.message : 'History could not be loaded.'}`]);
          } finally { if (current()) setComplete(old => old + 1); }
        }
      }
      await Promise.all([worker(), worker()]);
      if (current()) { await synchronize(false); if (current()) setSyncing(false); }
    })();
    return () => {
      live = false; controller.abort(); clearTimeout(timer); unsubscribe?.();
      for (const transfer of transfers.current) transfer.abort();
      transfers.current.clear();
      for (const url of urls.current) URL.revokeObjectURL(url);
      urls.current.clear();
    };
    // IDs define authorization scope; no decrypted history is shared across dialogs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, attempt]);
  const matches = useMemo(() => {
    const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    const inScope = (id: string, person: string, date: string) => (!channelId || channelId === id) && (!author || author === person)
      && (!after || date.slice(0, 10) >= after) && (!before || date.slice(0, 10) <= before);
    const includes = (text: string) => terms.every(term => text.toLocaleLowerCase().includes(term));
    const messages = !documentId && (!contentType || contentType === 'messages') ? records.filter(message => !message.locked && inScope(message.channel_id, message.author_id, message.created_at) && includes(message.content)).map(message => ({kind: 'message' as const, id:message.id, date:message.created_at, message})) : [];
    const documents = !documentId && (!contentType || contentType === 'files') ? files.filter(file => inScope(file.channel_id, file.document_owner_id || file.uploader_id, file.created_at) && includes(file.filename)
      && (!fileType || (fileType === 'images' ? file.content_type.startsWith('image/') : file.content_type === fileType))).map(file => ({kind:'file' as const, id:file.id, date:file.created_at, file})) : [];
    const choices = !documentId && (!contentType || contentType === 'decisions') ? decisions.filter(decision => inScope(decision.channel_id, decision.created_by, decision.created_at) && includes(`${decision.title || ''} ${decision.content || ''}`)).map(decision => ({kind:'decision' as const, id:decision.id, date:decision.created_at, decision})) : [];
    const content = historySupplementalSources(sources, contentType).filter(source => sourceMatches(source, query, {channelId, author, after, before, documentId}))
      .map(source => ({kind:'source' as const, id:source.id, date:source.created_at, source}));
    return [...messages, ...documents, ...choices, ...content].sort((a,b) => b.date.localeCompare(a.date) || b.id.localeCompare(a.id));
  }, [query, channelId, author, after, before, records, files, decisions, sources, documentId, contentType, fileType]);
  async function openSource(source: SearchSource) {
    const owner = api.getAccessToken(), channel = channels.find(row => row.id === source.channel_id); if (!channel) return;
    const controller = new AbortController(); transfers.current.add(controller);
    setSourcePreview(null); setDownloadError(''); setDownloading(source.id);
    try { const latest = await verifySearchSource(source, channel, controller.signal); if (!controller.signal.aborted && owner === api.getAccessToken()) setSourcePreview(latest); }
    catch (failure) { if (!controller.signal.aborted && owner === api.getAccessToken()) { setSources(old => old.filter(row => row.kind !== source.kind || row.id !== source.id)); setDownloadError(failure instanceof Error ? failure.message : 'Source access changed. Refresh search.'); } }
    finally { transfers.current.delete(controller); if (!controller.signal.aborted && owner === api.getAccessToken()) setDownloading(''); }
  }
  async function download(file: ManagedFile) {
    const token = api.getAccessToken(), controller = new AbortController();
    transfers.current.add(controller); setDownloading(file.id); setDownloadError('');
    try {
      const blob = await downloadChannelFile(file, {signal:controller.signal});
      if (controller.signal.aborted || token !== api.getAccessToken()) return;
      const url = URL.createObjectURL(blob); urls.current.add(url);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); },1000);
    } catch (error) { if (!controller.signal.aborted && token === api.getAccessToken() && !wasCancelled(error)) setDownloadError(error instanceof Error ? error.message : 'Download failed.'); }
    finally { transfers.current.delete(controller); if (!controller.signal.aborted && token === api.getAccessToken()) setDownloading(''); }
  }
  const errors = [...failures, ...Object.values(relatedFailures)];
  return <div className="fc-overlay"><section ref={dialog} className="fc-history-search fc-dialog" role="dialog" aria-modal="true" aria-label={t("Search all history")} tabIndex={-1}>
    <header><div><span className="fc-kicker">{t("Find the thought")}</span><h2>{t("Search your history.")}</h2></div><button className="fc-icon-button" aria-label={t("Close history search")} onClick={onClose}><X size={20} /></button></header>
    <label className="fc-history-query"><Search size={19} /><input data-autofocus autoFocus aria-label={t("Search messages")} value={query} onChange={event => setQuery(event.target.value)} placeholder={t("Words, filenames, a decision…")} /></label>
    <div className="fc-history-filters"><label>{t("Channel")}<select className="fc-input" value={channelId} onChange={event => setChannelId(event.target.value)}><option value="">{t("All channels")}</option>{channels.map(channel => <option key={channel.id} value={channel.id}>{channel.name || t("Channel")}</option>)}</select></label><label>{t("Person")}<select className="fc-input" value={author} onChange={event => setAuthor(event.target.value)}><option value="">{t("Everyone")}</option>{Object.entries(names).sort((a,b) => a[1].localeCompare(b[1])).map(([id,name]) => <option key={id} value={id}>{name}</option>)}</select></label><label>{t("From")}<input className="fc-input" type="date" aria-label={t("Messages from date")} value={after} onChange={event => setAfter(event.target.value)} /></label><label>{t("Until")}<input className="fc-input" type="date" aria-label={t("Messages until date")} value={before} onChange={event => setBefore(event.target.value)} /></label></div>
    <div className="fc-history-filters fc-history-content-filters"><label>{t("Content type")}<select className="fc-input" aria-label={t("Search content type")} value={contentType} onChange={event => setContentType(event.target.value)}><option value="">{t("All content")}</option><option value="messages">{t("Messages")}</option><option value="files">{t("Filenames")}</option><option value="decisions">{t("Decisions")}</option><option value="document">{t("Document content")}</option><option value="project">{t("Projects")}</option><option value="task">{t("Tasks")}</option><option value="feed">{t("Activity feed")}</option><option value="file-content">{t("File content")}</option><option value="linked-document">{t("Linked documents")}</option><option value="calendar">{t("Calendar event")}</option><option value="clip">{t("Channel clips")}</option><option value="workflow">{t("Workflows")}</option></select></label>{(!contentType || contentType === 'files') && <label>{t("File type")}<select className="fc-input" aria-label={t("Search file type")} value={fileType} onChange={event => setFileType(event.target.value)}><option value="">{t("All file types")}</option><option value="images">{t("Images")}</option><option value="application/pdf">{t("PDF")}</option><option value="text/plain">{t("Text")}</option><option value="application/json">{t("JSON")}</option></select></label>}</div>
    <label className="fc-search-document-filter">{t("Document")}<select className="fc-input" aria-label={t("Search document")} value={documentId} onChange={event => setDocumentId(event.target.value)}><option value="">{t("All documents")}</option>{sources.filter(source => source.kind === 'document' || source.kind === 'file-content').map(source => <option key={`${source.kind}:${source.id}`} value={source.id}>{source.title}</option>)}</select></label>
    <EnterpriseSearchPanel workspaceIds={[...new Set(channels.map(channel => channel.workspace_id).filter((id): id is string => !!id))]} />
    <div className="fc-history-progress" role="status">{syncing && <Loader2 size={14} className="fc-account-spinner" />}<span>{syncing ? t("Loading full history · {value0}/{value1} channels · {value2} messages", { value0: String(complete), value1: String(channels.length), value2: String(records.length) }) : t("{value0} results · {value1}/{value2} channels loaded", { value0: String(matches.length), value1: String(Math.max(0,complete - failures.length)), value2: String(channels.length) })}</span></div>
    {records.some(message => message.locked) && <p className="fc-alert" role="status">{t("Some encrypted items are locked on this device and cannot be searched. Verify the device or unlock recovery, then retry history.")}</p>}
    {errors.length > 0 && <div className="fc-alert" role="alert"><p>{t("Some history could not be loaded. Results are incomplete.")}</p>{errors.map((failure,index) => <p key={`${index}:${failure}`}>{failure}</p>)}<button type="button" className="fc-chat-text-button" onClick={() => setAttempt(old => old + 1)}>{t("Retry history")}</button></div>}
    {downloadError && <div className="fc-alert" role="alert">{downloadError}</div>}
    <div className="fc-history-results">{matches.slice(0, 300).map(result => {
      if (result.kind === 'source') { const source = result.source; return <button key={historySourceKey(source)} className="fc-history-result" disabled={!!downloading} onClick={() => void openSource(source)}><span><strong>{source.title}</strong><small>{t(historySourceLabel(source.kind))} · #{channels.find(channel => channel.id === source.channel_id)?.name || t('Channel')} · {names[source.created_by] || t('Member')}{source.secure ? ' · ' + t('Encrypted · local search') : ''}</small></span><p>{source.text.slice(0, 320)}</p>{source.truncated && <small>{t('Text extraction is limited.')}</small>}<ArrowUpRight size={16} /></button>; }
      if (result.kind === 'message') {
        const message = result.message;
        return <button key={`message:${message.id}`} className="fc-history-result" onClick={() => onMessage(message)}><span><strong>{names[message.author_id] || t("Member")}</strong><small>#{channels.find(channel => channel.id === message.channel_id)?.name || t("Channel")} · {new Date(message.created_at).toLocaleString(currentLocale())}{message.parent_id ? t(" · Thread") : ''}</small></span><p>{message.content}</p><ArrowUpRight size={16} /></button>;
      }
      if (result.kind === 'decision') {
        const decision = result.decision;
        return <button key={`decision:${decision.id}`} className="fc-history-result fc-history-decision" onClick={() => { window.dispatchEvent(new CustomEvent('freecollab:open-decision', {detail:{channel_id:decision.channel_id,decision_id:decision.id}})); onClose(); }}><span><strong>{t("Decision · ")}{decision.status || t("Proposed")}</strong><small>#{channels.find(channel => channel.id === decision.channel_id)?.name || t("Channel")} · {names[decision.created_by] || t("Member")} · {new Date(decision.created_at).toLocaleString(currentLocale())}</small></span><h3>{decision.title}</h3><p>{decision.content}</p><ArrowUpRight size={16} /></button>;
      }
      const file = result.file;
      return <section key={`file:${file.id}`} className="fc-history-result fc-history-file" aria-label={t("File result {value0}", { value0: String(file.filename) })}><span><strong><FileText size={13} />{t("Filename · v")}{file.version_number || 1}</strong><small>#{channels.find(channel => channel.id === file.channel_id)?.name || t("Channel")} · {new Date(file.created_at).toLocaleString(currentLocale())}</small></span><p>{file.filename}</p><div>{canPreviewFile(file) && <button type="button" className="fc-button fc-panel-button" onClick={() => setPreview(file)} aria-label={t("Preview search result {value0}", { value0: String(file.filename) })}><Eye size={14} />{t("Preview")}</button>}<button type="button" className="fc-button fc-panel-button" disabled={!!downloading} onClick={() => void download(file)} aria-label={t("Download search result {value0}", { value0: String(file.filename) })}><Download size={14} />{t("Download")}</button></div></section>;
    })}{matches.length > 300 && <p>{t("Showing 300 results. Refine the search to find more.")}</p>}{!syncing && !matches.length && <p className="fc-chat-muted">{t("No matching results. Try different words or filters.")}</p>}</div>
    {sourcePreview && <section className="fc-search-source-preview" aria-label={t("Verified search source")}><header><strong>{sourcePreview.title}</strong><button type="button" className="fc-icon-button" aria-label={t("Close source preview")} onClick={() => setSourcePreview(null)}><X size={16} /></button></header><p className="fc-chat-muted">{t("Current access and source revision verified.")}</p><pre>{sourcePreview.text}</pre><button type="button" className="fc-button" onClick={() => { window.dispatchEvent(new CustomEvent('freecollab:open-search-source', {detail:{channel_id:sourcePreview.channel_id,kind:sourcePreview.kind,id:sourcePreview.id,parent_id:sourcePreview.parent_id}})); onClose(); }}>{t("Open in channel")}</button></section>}
    <footer>{t("Search happens on this device. Messages, current documents, tasks, feed posts and supported file contents are matched locally. External queries require a separate explicit provider action. Closing this window clears its decrypted history.")}</footer>
    {preview && <FilePreview key={preview.id} file={preview} onClose={() => setPreview(null)} />}
  </section></div>;
}
