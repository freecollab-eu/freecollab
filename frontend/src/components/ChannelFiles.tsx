import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { Check, Download, Eye, FileJson, FileText, History, Image as ImageIcon, Plus, RotateCcw, ShieldCheck, Trash2, Upload, X } from 'lucide-react';
import { api } from '../lib/api';
import { deleteChannelFile, downloadChannelFile, listFileVersions, uploadChannelFile, wasCancelled, type ManagedFile } from '../lib/files-api';
import { FilePreview, canPreviewFile } from './FilePreview';
import { VersionProofs } from './VersionProofs';
import { ChannelSyncNotice } from './ChannelSyncNotice';
import '../styles/panels-files.css';
import { FileGallery } from './FileGallery';
import { FILE_EXPERIENCE_TYPES, normalizeExperienceFile } from '../lib/file-preview';

interface Props {
  channelId: string;
  files: ManagedFile[];
  loading: boolean;
  setFiles: Dispatch<SetStateAction<ManagedFile[]>>;
  onNotice: (notice: string) => void;
  onError: (error: string) => void;
}
interface UploadItem {
  id: string;
  file: File;
  documentId?: string;
  status: 'queued' | 'uploading' | 'done' | 'failed' | 'cancelled';
  progress: number;
  error?: string;
}
const officeTypes: Record<string, string> = {
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'Excel workbook',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PowerPoint presentation',
  'application/vnd.oasis.opendocument.text': 'OpenDocument text',
  'application/vnd.oasis.opendocument.spreadsheet': 'OpenDocument spreadsheet',
  'application/vnd.oasis.opendocument.presentation': 'OpenDocument presentation',
};
const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf', 'text/plain', 'application/json', ...Object.keys(officeTypes), ...FILE_EXPERIENCE_TYPES]);
const accept = [...allowedTypes].join(',');
const button = 'fc-button fc-panel-button';
const describe = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';
const size = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
const documentId = (file: ManagedFile) => file.document_id || file.id;
const fileType = (file: ManagedFile) => officeTypes[file.content_type] || (file.content_type.startsWith('image/') ? 'Image' : file.content_type === 'application/pdf' ? 'PDF' : file.content_type === 'application/json' ? 'JSON' : 'Text');
function validation(file: File) {
  if (file.size > 50 * 1024 * 1024) return 'Choose a file no larger than 50 MB.';
  if (!allowedTypes.has(file.type)) return 'Choose an image, PDF, text, JSON, Word, Excel, PowerPoint or OpenDocument file.';
  return undefined;
}

export function ChannelFiles({ channelId, files, setFiles, loading, onNotice, onError }: Props) {
  useI18n();
  const [view, setView] = useState<'list' | 'grid'>('list');
  const [queue, setQueue] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<ManagedFile | null>(null);
  const [history, setHistory] = useState<{ file: ManagedFile; versions: ManagedFile[]; loading: boolean; error?: string } | null>(null);
  const [busy, setBusy] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const versionInput = useRef<HTMLInputElement>(null);
  const versionTarget = useRef<ManagedFile | null>(null);
  const generation = useRef(0);
  const active = useRef<{ id: string; controller: AbortController } | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const objectUrls = useRef(new Set<string>());
  const dragDepth = useRef(0);
  const currentUser = api.getCurrentUserId();
  const ordered = [...files].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  const pending = queue.filter(item => item.status === 'queued' || item.status === 'uploading').length;

  useEffect(() => {
    return () => {
      ++generation.current;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
      for (const url of objectUrls.current) URL.revokeObjectURL(url);
      objectUrls.current.clear();
    };
  }, [channelId, currentUser]);

  function update(id: string, values: Partial<UploadItem>) {
    setQueue(items => items.map(item => item.id === id ? { ...item, ...values } : item));
  }

  useEffect(() => {
    if (active.current) return;
    const item = queue.find(record => record.status === 'queued');
    if (!item) return;
    const current = generation.current;
    const controller = new AbortController();
    active.current = { id: item.id, controller };
    controllers.current.add(controller);
    update(item.id, { status: 'uploading', progress: 0, error: undefined });
    const valid = () => current === generation.current && currentUser === api.getCurrentUserId();
    void (async () => {
      const invalid = validation(item.file);
      if (invalid) throw new Error(invalid);
      const saved = await uploadChannelFile(channelId, item.file, {
        signal: controller.signal, requestId: item.id, documentId: item.documentId,
        onProgress: progress => { if (valid() && !controller.signal.aborted) update(item.id, { progress }); },
      });
      if (!valid()) return;
      if (controller.signal.aborted) { update(item.id, { status: 'cancelled', progress: 0 }); return; }
      setFiles(records => {
        const existing = records.find(record => documentId(record) === documentId(saved));
        if (existing && (existing.version_number || 1) > (saved.version_number || 1)) return records;
        return [saved, ...records.filter(record => documentId(record) !== documentId(saved))];
      });
      update(item.id, { status: 'done', progress: 100 });
      onNotice(`${saved.filename} ${item.documentId ? `saved as version ${saved.version_number || 1}` : 'uploaded'}.`);
      setHistory(value => value && documentId(value.file) === documentId(saved) ? null : value);
    })().catch(err => {
      if (valid()) update(item.id, { status: controller.signal.aborted || wasCancelled(err) ? 'cancelled' : 'failed', error: controller.signal.aborted || wasCancelled(err) ? undefined : describe(err) });
    }).finally(() => {
      controllers.current.delete(controller);
      if (active.current?.id === item.id) active.current = null;
      if (valid()) setQueue(items => [...items]);
    });
  }, [queue, channelId, currentUser]);

  function enqueue(selected: FileList | File[], target?: ManagedFile) {
    const additions = Array.from(selected).map(normalizeExperienceFile).map(file => {
      const error = validation(file);
      return { id: crypto.randomUUID().replace(/-/g, ''), file, documentId: target ? documentId(target) : undefined,
        status: error ? 'failed' as const : 'queued' as const, progress: 0, error };
    });
    setQueue(items => [...items, ...additions]);
    onNotice(''); onError('');
    if (input.current) input.current.value = '';
    if (versionInput.current) versionInput.current.value = '';
  }

  function cancel(id: string) {
    if (active.current?.id === id) active.current.controller.abort();
    update(id, { status: 'cancelled' });
  }

  async function download(file: ManagedFile) {
    const current = generation.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    setBusy(file.id); onError('');
    try {
      const blob = await downloadChannelFile(file, { signal: controller.signal });
      if (current !== generation.current || controller.signal.aborted || currentUser !== api.getCurrentUserId()) return;
      const url = URL.createObjectURL(blob);
      objectUrls.current.add(url);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = file.filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => { URL.revokeObjectURL(url); objectUrls.current.delete(url); }, 1000);
      onNotice(`${file.filename} downloaded.`);
    } catch (err) { if (current === generation.current && !wasCancelled(err)) onError(describe(err)); }
    finally { controllers.current.delete(controller); if (current === generation.current) setBusy(''); }
  }

  async function showHistory(file: ManagedFile) {
    if (history?.file.id === file.id) { setHistory(null); return; }
    const current = generation.current;
    setHistory({ file, versions: [], loading: true });
    try {
      const versions = await listFileVersions(file);
      if (current === generation.current) setHistory(value => value?.file.id === file.id ? { file, versions, loading: false } : value);
    } catch (err) { if (current === generation.current) setHistory(value => value?.file.id === file.id ? { ...value, loading: false, error: describe(err) } : value); }
  }

  async function remove(file: ManagedFile) {
    const count = file.version_count || 1;
    if (!window.confirm(`Delete ${file.filename}${count > 1 ? ` and all ${count} versions` : ''}?`)) return;
    const current = generation.current;
    setBusy(file.id); onError('');
    try {
      await deleteChannelFile(file);
      if (current !== generation.current) return;
      setFiles(records => records.filter(record => documentId(record) !== documentId(file)));
      setHistory(null); onNotice(`${file.filename} deleted.`);
    } catch (err) { if (current === generation.current) onError(describe(err)); }
    finally { if (current === generation.current) setBusy(''); }
  }

  return <div className="fc-files-workspace">
    <ChannelSyncNotice channelId={channelId} kind="files" />
    <section className={`fc-file-upload fc-file-dropzone ${dragging ? 'is-dragging' : ''}`} aria-labelledby="upload-heading"
      onDragEnter={event => { event.preventDefault(); if (event.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true); } }}
      onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onDragLeave={event => { event.preventDefault(); if (--dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false); } }}
      onDrop={event => { event.preventDefault(); dragDepth.current = 0; setDragging(false); if (event.dataTransfer.files.length) enqueue(event.dataTransfer.files); }}>
      <span className="fc-file-upload-icon"><Upload size={20} strokeWidth={1.5} /></span><div><h3 id="upload-heading">{t("Share something useful.")}</h3><p>{t("Drop files here, or choose below.")}<br />{t("Images, PDF, Office, code, CSV, audio and video · Up to 50 MB each")}</p></div>
      <label htmlFor="channel-upload">{t("Choose one or more files")}</label><input id="channel-upload" ref={input} type="file" multiple aria-label={t("Choose a file")} accept={accept} className="fc-file-input" onChange={event => { if (event.target.files?.length) enqueue(event.target.files); }} />
      <p className="fc-file-access"><ShieldCheck size={12} />{files.some(file => file.encrypted) ? t("Encrypted files open only on your device.") : t("Only channel members can download these files.")}</p>
    </section>
    <input ref={versionInput} type="file" aria-label={t("Choose a new version")} accept={accept} className="fc-file-version-input" tabIndex={-1} onChange={event => { if (event.target.files?.length && versionTarget.current) enqueue(event.target.files, versionTarget.current); versionTarget.current = null; }} />
    {queue.length > 0 && <section className="fc-upload-queue" aria-labelledby="upload-queue-heading"><header><h3 id="upload-queue-heading">{t("Uploads")}{pending > 0 ? t(" · {value0} remaining", { value0: String(pending) }) : ''}</h3>{!pending && <button type="button" className="fc-text-button" onClick={() => setQueue([])}>{t("Clear list")}</button>}</header><ul>{queue.map(item => <li key={item.id} className={`fc-upload-item is-${item.status}`}><div className="fc-upload-item-heading"><div><strong>{t("Upload · ")}{item.file.name}</strong><span>{size(item.file.size)}{item.documentId ? t(" · New version") : ''}</span></div>{item.status === 'done' ? <Check size={17} aria-label={t("Upload acknowledged")} /> : item.status === 'queued' || item.status === 'uploading' ? <button type="button" className="fc-icon-button" aria-label={t("Cancel upload {value0}", { value0: String(item.file.name) })} onClick={() => cancel(item.id)}><X size={16} /></button> : <button type="button" className="fc-text-button" onClick={() => update(item.id, { status: 'queued', error: undefined, progress: 0 })} aria-label={t("Retry upload {value0}", { value0: String(item.file.name) })}><RotateCcw size={13} />{t("Retry")}</button>}</div>{item.status === 'uploading' && <div role="progressbar" aria-label={t("Uploading {value0}", { value0: String(item.file.name) })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.progress} className="fc-upload-progress"><span style={{ width: `${item.progress}%` }} /></div>}<p role={item.status === 'failed' ? 'alert' : 'status'}>{item.status === 'queued' ? t("Waiting…") : item.status === 'uploading' ? item.progress >= 99 ? t("Waiting for confirmation…") : t("Uploading · {value0}%", { value0: String(item.progress) }) : item.status === 'done' ? t("Uploaded") : item.status === 'failed' ? item.error : t("Cancelled locally. Refresh to check delivery, or retry safely.")}</p></li>)}</ul></section>}
    {loading && !files.length && !queue.length ? <div className="fc-panel-loading" role="status"><span className="fc-panel-spinner" /><span>{t("Loading files…")}</span></div> : files.length === 0 ? <div className="fc-panel-empty"><span className="fc-empty-file-icon"><FileText size={24} strokeWidth={1.25} /></span><h3>{t("No files yet.")}</h3><p>{t("Useful references, all in one place. Choose files above to share them with this channel.")}</p></div> : <section className="fc-shared-files" aria-labelledby="shared-files-heading"><div className="fc-shared-files-heading"><h3 id="shared-files-heading">{t("Recently shared")}</h3><span>{size(files.reduce((sum, file) => sum + file.size, 0))} {t(" total")}</span></div><div className="fc-file-view-controls" role="group" aria-label={t("File view")}><button type="button" className={button} aria-pressed={view === 'list'} onClick={() => setView('list')}>{t("List")}</button><button type="button" className={button} aria-pressed={view === 'grid'} onClick={() => setView('grid')}>{t("Gallery")}</button></div>{view === 'grid' ? <FileGallery files={ordered} onPreview={setPreview} /> : <ul className="fc-file-list">{ordered.map(file => {
      const Icon = file.content_type.startsWith('image/') ? ImageIcon : file.content_type === 'application/json' ? FileJson : FileText;
      const owner = (file.document_owner_id || file.uploader_id) === currentUser;
      return <li key={documentId(file)} className="fc-file-row"><div className="fc-file-identity"><span className="fc-file-kind-icon"><Icon size={22} strokeWidth={1.45} /></span><div className="fc-file-copy"><p>{file.filename}</p><span>{fileType(file)} · {size(file.size)} {t(" · v")}{file.version_number || 1}<span aria-hidden="true"> · </span><time dateTime={file.created_at}>{new Date(file.created_at).toLocaleDateString(currentLocale(), { month: 'short', day: 'numeric' })}</time></span><small className="fc-file-protection">{file.encrypted ? <><ShieldCheck size={11} />{t("End-to-end encrypted")}</> : t("Standard channel file · stored without encryption")}</small></div></div>
        <div className="fc-file-actions fc-file-primary-actions">{<button type="button" className={button} onClick={() => setPreview(file)} aria-label={t("Preview {value0}", { value0: String(file.filename) })}><Eye size={14} />{canPreviewFile(file) ? t("Preview") : t("Details & comments")}</button>}<button type="button" className={button} disabled={!!busy} onClick={() => void download(file)} aria-label={t("Download {value0}", { value0: String(file.filename) })}><Download size={14} />{t("Download")}</button>{owner && <button type="button" className={`${button} fc-file-delete fc-danger-action`} disabled={!!busy || !!pending} onClick={() => void remove(file)} aria-label={t("Delete {value0}", { value0: String(file.filename) })} title={t("Delete file")}><Trash2 size={14} /></button>}</div>
        <div className="fc-file-secondary-actions"><button type="button" className="fc-text-button" onClick={() => void showHistory(file)} aria-expanded={history?.file.id === file.id}><History size={13} />{file.version_count || 1} {(file.version_count || 1) === 1 ? t("version") : t("versions")}</button>{owner && <button type="button" className="fc-text-button" onClick={() => { versionTarget.current = file; versionInput.current?.click(); }}><Plus size={13} />{t("New version")}</button>}</div>
        <VersionProofs file={file} />
        {history?.file.id === file.id && <section className="fc-file-history" aria-label={t("Version history for {value0}", { value0: String(file.filename) })}><h4>{t("Version history")}</h4>{history.loading ? <p role="status">{t("Loading versions…")}</p> : history.error ? <p role="alert">{history.error}</p> : <ol>{history.versions.map(version => <li key={version.id}><div><strong>{t("Version ")}{version.version_number || 1}{version.id === file.id ? t(" · Current") : ''}</strong><span>{version.filename}</span><small>{size(version.size)} · <time dateTime={version.created_at}>{new Date(version.created_at).toLocaleString(currentLocale())}</time></small></div><div>{canPreviewFile(version) && <button type="button" className="fc-icon-button" aria-label={t("Preview version {value0} of {value1}", { value0: String(version.version_number || 1), value1: String(version.filename) })} onClick={() => setPreview(version)}><Eye size={15} /></button>}<button type="button" className="fc-icon-button" aria-label={t("Download version {value0} of {value1}", { value0: String(version.version_number || 1), value1: String(version.filename) })} disabled={!!busy} onClick={() => void download(version)}><Download size={15} /></button></div><VersionProofs file={version} /></li>)}</ol>}</section>}
      </li>;
    })}</ul>}</section>}
    {preview && <FilePreview key={preview.id} file={preview} onClose={() => setPreview(null)} onVersionSaved={() => { const token = api.getAccessToken(); void api.getFiles(channelId).then(rows => { if (token === api.getAccessToken()) { setFiles(rows); setPreview(null); setHistory(null); } }).catch(error => onError(describe(error))); }} />}
  </div>;
}
