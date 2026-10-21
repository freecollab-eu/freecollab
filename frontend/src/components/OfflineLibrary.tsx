import { useEffect, useRef, useState } from 'react';
import { Download, FileText, FolderDown, RefreshCw, ShieldCheck, Trash2, UploadCloud } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { listChannelFiles, type ManagedFile } from '../lib/files-api';
import { attachmentOutbox, offlineLibrary, type OfflineChannel, type OfflineFile, type QueuedAttachment } from '../mobile/library';
import { formatDate, t, useI18n } from '../i18n';
import '../styles/offline-library.css';

const describe = (error: unknown) => error instanceof Error ? error.message : 'Offline storage could not be read.';
const size = (bytes: number) => `${(bytes / 1048576).toFixed(1)} MB`;

export function OfflineLibrary({ channel }: { channel?: Channel }) {
  useI18n();
  const [channels, setChannels] = useState<OfflineChannel[]>([]), [files, setFiles] = useState<OfflineFile[]>([]), [queue, setQueue] = useState<QueuedAttachment[]>([]);
  const [selected, setSelected] = useState(''), [available, setAvailable] = useState<ManagedFile[]>([]);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const identity = useRef(api.getAccessToken()), generation = useRef(0), loadSequence = useRef(0), fileInput = useRef<HTMLInputElement>(null);
  const valid = (request: number) => request === generation.current && identity.current === api.getAccessToken();
  async function load() {
    const request = generation.current, sequence = ++loadSequence.current;
    try {
      const [library, attachments] = await Promise.all([offlineLibrary.list(), attachmentOutbox.list()]);
      if (!valid(request) || sequence !== loadSequence.current) return;
      setChannels(library.channels); setFiles(library.files); setQueue(attachments);
      if (selected && !library.channels.some(row => row.id === selected)) setSelected('');
    } catch (failure) { if (valid(request)) { setChannels([]); setFiles([]); setError(describe(failure)); } }
  }
  useEffect(() => {
    let mounted = true;
    const update = () => { if (mounted) void load(); };
    const auth = () => { if (identity.current === api.getAccessToken()) return; generation.current++; identity.current = api.getAccessToken(); setChannels([]); setFiles([]); setQueue([]); setAvailable([]); setSelected(''); setBusy(''); setError(''); setNotice(''); };
    void load(); window.addEventListener('freecollab:mobile-state', update); window.addEventListener('freecollab:auth-changed', auth); window.addEventListener('storage', auth);
    return () => { mounted = false; generation.current++; window.removeEventListener('freecollab:mobile-state', update); window.removeEventListener('freecollab:auth-changed', auth); window.removeEventListener('storage', auth); };
  }, []);
  useEffect(() => {
    const request = generation.current; let active = true; setAvailable([]);
    if (channel && navigator.onLine) void listChannelFiles(channel.id).then(rows => { if (active && valid(request)) setAvailable(rows.filter(file => file.size <= 5 * 1048576)); }).catch(() => {});
    return () => { active = false; };
  }, [channel?.id]);
  async function run(key: string, operation: () => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await operation(); if (valid(request)) await load(); }
    catch (failure) { if (valid(request)) setError(describe(failure)); }
    finally { if (valid(request)) setBusy(''); }
  }
  const current = channels.find(row => row.id === selected), enrolled = channels.some(row => row.id === channel?.id);
  const target = channels.find(row => row.id === channel?.id) || current;
  const bytes = files.reduce((sum, file) => sum + file.size, 0) + queue.reduce((sum, file) => sum + file.size, 0);
  return <section className="fc-account-section fc-offline-library" aria-labelledby="offline-library-heading">
    <div className="fc-account-section-heading"><div><span className="fc-kicker">{t('Only on this device')}</span><h3 id="offline-library-heading">{t('Offline library')}</h3></div><FolderDown size={22} /></div>
    <p className="fc-account-muted">{t('Choose what stays with you. History and files are encrypted in this browser and erased on sign-out. Saved copies expire after seven days and need the same sign-in.')}</p>
    <p className="fc-account-muted">{t('Offline copies can contain older content. Access changes and deletions can only be checked after reconnecting. Protect this device; an unlocked browser can read its saved copies.')}</p>
    <div className="fc-offline-summary"><span><ShieldCheck size={15} />{t('{count} of 10 channels', { count: channels.length })}</span><span>{t('{used} of 25 MB', { used: size(bytes) })}</span></div>
    {error && <p className="fc-alert" role="alert">{t(error)}</p>}{notice && <p className="fc-alert" role="status">{t(notice)}</p>}
    {channel && <div className="fc-offline-enroll"><div><strong>{channel.name || t('Channel')}</strong><small>{enrolled ? t('Saved for offline reading') : t('Not saved on this device')}</small></div><button type="button" className="fc-button" disabled={!!busy || !navigator.onLine} onClick={() => void run('save-channel', async () => { await offlineLibrary.enable(channel.id, channel.name || t('Channel'), !!channel.secure); setSelected(channel.id); setNotice('The latest 200 messages are saved privately on this device.'); })}><Download size={15} />{enrolled ? t('Refresh saved history') : t('Save this channel offline')}</button></div>}
    {!channels.length && <p className="fc-account-muted">{t('Open a channel online, then choose Save this channel offline. Nothing is saved automatically before you choose it.')}</p>}
    <ul className="fc-mobile-queue" aria-label={t('Saved offline channels')}>{channels.map(row => <li key={row.id}><div><button type="button" className="fc-text-button" aria-expanded={selected === row.id} onClick={() => setSelected(selected === row.id ? '' : row.id)}>{row.name}</button><small>{row.messages.length} {t('saved messages')} · {formatDate(row.savedAt, { dateStyle: 'medium', timeStyle: 'short' })}</small></div><button type="button" className="fc-icon-button" aria-label={t('Forget offline channel {name}', { name: row.name })} disabled={!!busy} onClick={() => void run(row.id, () => offlineLibrary.forget(row.id))}><Trash2 size={16} /></button></li>)}</ul>
    {current && <section className="fc-offline-history" aria-label={t('Saved history for {name}', { name: current.name })}><h4>{current.name}</h4><p className="fc-account-muted">{navigator.onLine ? t('Current access checked online.') : t('Saved copy · access has not been checked while offline.')}</p><ol>{[...current.messages].sort((a, b) => a.created_at.localeCompare(b.created_at)).map(message => <li key={message.id}><strong>{current.authors?.[message.author_id] || t('Member')}</strong><time dateTime={message.created_at}>{formatDate(message.created_at, { dateStyle: 'short', timeStyle: 'short' })}</time><p>{message.content}</p></li>)}</ol></section>}
    {target && <div className="fc-offline-file-picker"><h4>{t('Files for this channel')}</h4><p className="fc-account-muted">{t('Save up to twenty files, or queue up to ten uploads. Each file can be up to 5 MB.')}</p><label className="fc-button" htmlFor="offline-attachment-input"><UploadCloud size={15} />{t('Queue a file privately')}</label><input ref={fileInput} id="offline-attachment-input" type="file" disabled={!!busy} onChange={event => { const file = event.target.files?.[0]; if (file) void run('queue-file', async () => { await attachmentOutbox.enqueue(target.id, file); if (fileInput.current) fileInput.current.value = ''; setNotice('Attachment saved in the encrypted outbox. Current access is checked before sending.'); if (navigator.onLine) await attachmentOutbox.flush(); }); }} />{available.filter(file => file.channel_id === target.id && !files.some(saved => saved.id === file.id)).slice(0, 40).map(file => <div className="fc-offline-file-choice" key={file.id}><span><FileText size={15} />{file.filename}<small>{size(file.size)}</small></span><button type="button" className="fc-button" disabled={!!busy || !navigator.onLine} aria-label={t('Save offline {name}', { name: file.filename })} onClick={() => void run(file.id, () => offlineLibrary.saveFile(file))}><Download size={14} />{t('Save offline')}</button></div>)}</div>}
    <ul className="fc-mobile-queue" aria-label={t('Saved offline files')}>{files.map(file => <li key={file.id}><div><strong>{file.name}</strong><small>{size(file.size)} · {t('Encrypted local copy')}</small></div><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} aria-label={t('Open offline {name}', { name: file.name })} onClick={() => void run('open-' + file.id, async () => { const result = await offlineLibrary.fileBlob(file.id), url = URL.createObjectURL(result.blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = result.name; anchor.rel = 'noopener'; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); })}><Download size={15} />{t('Open saved file')}</button><button type="button" className="fc-icon-button" disabled={!!busy} aria-label={t('Forget offline file {name}', { name: file.name })} onClick={() => void run(file.id, () => offlineLibrary.removeFile(file.id))}><Trash2 size={15} /></button></div></li>)}</ul>
    {queue.length > 0 && <><h4>{t('Queued attachments')}</h4><ul className="fc-mobile-queue" aria-label={t('Queued attachments')}>{queue.map(row => <li key={row.id}><div><strong>{row.name}</strong><small>{size(row.size)} · {row.state === 'review' ? t('Needs review') : t('Waiting to send')}</small>{row.error && <p>{t(row.error)}</p>}</div><button type="button" className="fc-icon-button" disabled={!!busy} aria-label={t('Remove queued attachment {name}', { name: row.name })} onClick={() => void run(row.id, () => attachmentOutbox.remove(row.id))}><Trash2 size={15} /></button></li>)}</ul><button type="button" className="fc-button" disabled={!!busy || !navigator.onLine || !queue.some(row => row.state === 'queued')} onClick={() => void run('send-attachments', () => attachmentOutbox.flush())}><RefreshCw size={15} />{t('Send eligible attachments')}</button></>}
  </section>;
}
