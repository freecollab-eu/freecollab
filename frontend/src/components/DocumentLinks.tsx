import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, FileSymlink, History, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { api } from '../lib/api';
import { documentCalendarApi, type DocumentRecord } from '../lib/document-calendar-api';
import { documentProviders, type DocumentFields, type DocumentProvider } from '../lib/document-links';
import '../styles/integration-content.css';

interface Props { channelId: string; isAdmin?: boolean }
interface Editor { id: string; fields: DocumentFields; previous?: DocumentRecord }
const empty = (): Editor => ({ id: crypto.randomUUID(), fields: { title: '', provider: 'nextcloud', url: '', notes: '' } });
const describe = (error: unknown) => error instanceof Error ? error.message : 'The document action could not be completed.';

export function DocumentLinks({ channelId, isAdmin = false }: Props) {
  useI18n();
  const [rows, setRows] = useState<DocumentRecord[]>([]), [editor, setEditor] = useState<Editor | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [history, setHistory] = useState<{ id: string; rows: DocumentRecord[] } | null>(null), [removing, setRemoving] = useState('');
  const generation = useRef(0), identity = useRef(api.getAccessToken());
  const current = (request: number) => request === generation.current && identity.current === api.getAccessToken();
  async function load(request = generation.current) {
    setLoading(true); setError('');
    try { const result = await documentCalendarApi.documents(channelId); if (current(request)) setRows(result); }
    catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setLoading(false); }
  }
  useEffect(() => {
    const request = ++generation.current; identity.current = api.getAccessToken();
    setRows([]); setEditor(null); setHistory(null); setRemoving(''); setError(''); setNotice(''); setBusy('');
    void load(request);
    const signedOut = () => { ++generation.current; setRows([]); setEditor(null); setHistory(null); setRemoving(''); setLoading(false); setBusy(''); };
    window.addEventListener('freecollab:auth-changed', signedOut);
    return () => { ++generation.current; window.removeEventListener('freecollab:auth-changed', signedOut); };
  }, [channelId]);
  async function run(key: string, action: (request: number) => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await action(request); } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setBusy(''); }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!editor) return;
    const pending = editor;
    await run('save', async request => {
      const saved = await documentCalendarApi.saveDocument(channelId, pending.fields, pending.previous, pending.id);
      if (!current(request)) return;
      if (saved.resolution_error) throw new Error(saved.resolution_error);
      setRows(previous => [saved, ...previous.filter(item => item.id !== saved.id)]); setEditor(null); setHistory(null);
      setNotice(t("Document link saved. Opening it uses the document provider’s own access controls."));
    });
  }
  async function remove(row: DocumentRecord) {
    await run('remove:' + row.id, async request => {
      await documentCalendarApi.removeDocument(channelId, row); if (!current(request)) return;
      setRows(previous => previous.filter(item => item.id !== row.id)); setRemoving(''); setHistory(null); setNotice(t("Document link removed. The external document is unchanged."));
    });
  }
  async function showHistory(row: DocumentRecord) {
    if (history?.id === row.id) { setHistory(null); return; }
    await run('history:' + row.id, async request => {
      const result = await documentCalendarApi.history<DocumentFields>(channelId, 'documents', row.id);
      if (current(request)) setHistory({ id: row.id, rows: result });
    });
  }
  return <section className="fc-integration-content" aria-label={t("Channel document links")}>
    <div className="fc-integration-content-heading"><div><h3>{t("Documents, in context.")}</h3><p>{t("Link material from Nextcloud, Google Drive or SharePoint. FreeCollab opens the original document when you choose it.")}</p></div><button className="fc-icon-button" type="button" aria-label={t("Refresh document links")} disabled={!!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    {error && <div className="fc-integration-content-error" role="alert">{error}<button type="button" className="fc-text-button" onClick={() => void load()}>{t("Reload links")}</button></div>}
    {notice && <p className="fc-integration-content-notice" role="status">{notice}</p>}
    <button type="button" className="fc-button fc-integration-content-new" disabled={!!busy} onClick={() => { setEditor(empty()); setError(''); setNotice(''); setRemoving(''); }}><Plus size={15} />{t("Add document link")}</button>
    {editor && <form className="fc-integration-content-editor" onSubmit={save} aria-label={editor.previous ? t("Edit document link") : t("Add document link")}>
      <div className="fc-integration-editor-heading"><h4>{editor.previous ? t("Edit document link") : t("Add document link")}</h4><button className="fc-icon-button" type="button" aria-label={t("Cancel document editing")} disabled={!!busy} onClick={() => setEditor(null)}><X size={16} /></button></div>
      <label htmlFor="integration-document-title">{t("Document title")}</label><input autoFocus id="integration-document-title" className="fc-input" value={editor.fields.title} maxLength={200} required disabled={!!busy} onChange={e => setEditor({ ...editor, fields: { ...editor.fields, title: e.target.value } })} />
      <label htmlFor="integration-document-provider">{t("Document provider")}</label><select id="integration-document-provider" className="fc-input" value={editor.fields.provider} disabled={!!busy} onChange={e => setEditor({ ...editor, fields: { ...editor.fields, provider: e.target.value as DocumentProvider } })}>{Object.entries(documentProviders).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select>
      <label htmlFor="integration-document-url">{t("Document URL")}</label><input id="integration-document-url" className="fc-input" type="url" value={editor.fields.url} maxLength={4096} required disabled={!!busy} placeholder={editor.fields.provider === 'nextcloud' ? 'https://cloud.example.com/s/…' : editor.fields.provider === 'google_drive' ? 'https://docs.google.com/document/d/…' : 'https://team.sharepoint.com/sites/…'} onChange={e => setEditor({ ...editor, fields: { ...editor.fields, url: e.target.value } })} />
      <p className="fc-integration-content-caption">{t("Use the HTTPS document link without query parameters, account credentials or a fragment. Links are never fetched automatically.")}</p>
      <label htmlFor="integration-document-notes">{t("Notes")}</label><textarea id="integration-document-notes" className="fc-input" rows={3} value={editor.fields.notes} maxLength={4000} disabled={!!busy} onChange={e => setEditor({ ...editor, fields: { ...editor.fields, notes: e.target.value } })} />
      <button className="fc-button fc-button-primary" type="submit" disabled={!!busy}>{busy === 'save' && <Loader2 size={15} className="fc-integration-spinner" />}{t("Save document link")}</button>
    </form>}
    {loading ? <p className="fc-integration-content-loading" role="status"><Loader2 size={16} className="fc-integration-spinner" />{t("Loading document links…")}</p> : !rows.length ? <div className="fc-integration-content-empty"><FileSymlink size={26} /><p>{t("Keep your team’s source documents alongside the conversation.")}</p></div> : <ul className="fc-integration-content-list">{rows.map(row => <li key={row.id} className="fc-integration-content-card">
      {row.fields ? <><div className="fc-integration-content-meta"><span>{documentProviders[row.fields.provider]}</span><span>{t("Version ")}{row.revision}</span></div><h4>{row.fields.title}</h4>{row.fields.notes && <p className="fc-integration-content-detail">{row.fields.notes}</p>}<a className="fc-text-button" href={row.fields.url} target="_blank" rel="noopener noreferrer">{t("Open document")}<ArrowUpRight size={14} /></a></> : <p className="fc-integration-content-caption">{row.resolution_error || t("Unlock recovery to read this document link.")}</p>}
      <div className="fc-integration-content-actions"><button type="button" className="fc-text-button" disabled={!!busy} onClick={() => void showHistory(row)}><History size={14} />{t("History")}</button>{(isAdmin || row.created_by === api.getCurrentUserId()) && <>{row.fields && <button type="button" className="fc-text-button" disabled={!!busy} onClick={() => { setEditor({ id: row.id, fields: row.fields!, previous: row }); setError(''); }}><Pencil size={14} />{t("Edit")}</button>}<button type="button" className="fc-text-button" disabled={!!busy} onClick={() => setRemoving(row.id)}><Trash2 size={14} />{t("Remove")}</button></>}</div>
      {removing === row.id && <div className="fc-integration-content-confirm"><p>{t("Remove this link from the channel?")}</p><button className="fc-button" type="button" disabled={!!busy} onClick={() => setRemoving('')}>{t("Keep link")}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => void remove(row)}>{t("Remove link")}</button></div>}
      {history?.id === row.id && <ol className="fc-integration-content-history" aria-label={t("Document link history")}>{history.rows.map(revision => <li key={revision.revision}><span>{t("Version ")}{revision.revision} · {new Date(revision.revised_at).toLocaleString(currentLocale())}</span><strong>{revision.deleted ? t("Link removed") : revision.fields?.title || revision.resolution_error || t("Encrypted revision unavailable")}</strong>{revision.fields && <small>{documentProviders[revision.fields.provider]} · {revision.fields.url}</small>}</li>)}</ol>}
    </li>)}</ul>}
  </section>;
}
