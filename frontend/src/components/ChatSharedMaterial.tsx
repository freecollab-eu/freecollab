import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, CheckSquare, FileText, Layers } from 'lucide-react';
import { useAccount } from '../account/AccountProvider';
import { t, useI18n } from '../i18n';
import { api, type Channel } from '../lib/api';
import { allowsChannelMaterial, readChannelMaterial, type ChannelMaterial } from '../lib/chat-shared-material';
import { documentOrganizationApi } from '../lib/document-organization-api';
import { listChannelFiles } from '../lib/files-api';
import { projectTasksApi } from '../lib/project-tasks-api';
import { getSecureMessaging } from '../lib/secure-client';
import { readSharedDocumentForSearch, sharedDocumentsApi } from '../lib/shared-documents-api';
import { FileThumbnail } from './FileGallery';
import { FilePreview, canPreviewFile } from './FilePreview';

/** Existing files, shared pages and an actionable step from this native room. */
export function ChatSharedMaterial({ channel, workspaceId, onOpenFiles, onOpenDocuments, onOpenTasks, onAvailabilityChange, refreshKey }: {
  channel: Channel;
  workspaceId?: string;
  onOpenFiles: () => void;
  onOpenDocuments: (documentId?: string, stackId?: string) => void;
  onOpenTasks: (taskId?: string, projectId?: string) => void;
  onAvailabilityChange: (available: boolean) => void;
  refreshKey?: number;
}) {
  useI18n();
  const { workspace } = useAccount();
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const scope = JSON.stringify([actor, token, workspaceId, workspace?.id, channel.id, channel.workspace_id, channel.secure, channel.matrix_room_id, channel.encrypted_channel_key]);
  const latestScope = useRef(scope); latestScope.current = scope;
  const invalidatedScope = useRef<string | null>(null);
  const [material, setMaterial] = useState<ChannelMaterial | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const availability = useRef(onAvailabilityChange); availability.current = onAvailabilityChange;
  const refresh = useRef<(() => void) | null>(null), lastRefresh = useRef(refreshKey);

  useEffect(() => {
    let live = true, request = 0, loading = false, queued = false;
    const abort = new AbortController();
    invalidatedScope.current = null;
    setMaterial(null); setPreviewOpen(false); availability.current(false);
    const current = () => live && !abort.signal.aborted && latestScope.current === scope && token === api.getAccessToken() && actor === api.getCurrentUserId();
    const purge = () => { live = false; invalidatedScope.current = scope; abort.abort(); setMaterial(null); setPreviewOpen(false); availability.current(false); };
    window.addEventListener('freecollab:auth-changed', purge);
    window.addEventListener('freecollab:workspace-changed', purge);
    const load = async () => {
      if (!current() || !token || !actor || workspace?.id !== workspaceId || !allowsChannelMaterial(channel, workspaceId)) return;
      if (loading) { queued = true; return; }
      loading = true;
      const attempt = ++request;
      try {
        const result = await readChannelMaterial({ channel, workspaceId: workspaceId!, actor, token }, {
          current: () => current() && attempt === request,
          native: async () => { const secure = await getSecureMessaging(); return current() && !await secure.handles(channel.id); },
          files: () => listChannelFiles(channel.id),
          documents: () => sharedDocumentsApi.list(channel.id),
          organization: () => documentOrganizationApi.list(channel.id),
          projects: () => projectTasksApi.projects(channel.id),
          tasks: project => projectTasksApi.tasks(channel.id, project.id),
          readDocument: document => readSharedDocumentForSearch(channel.id, document.id, abort.signal),
        });
        if (current() && attempt === request) { setMaterial(result); availability.current(!!result); }
      } catch { if (current() && attempt === request) { setMaterial(null); availability.current(false); } }
      finally { loading = false; if (queued && current()) { queued = false; void load(); } }
    };
    const reload = () => { if (!document.hidden) void load(); };
    refresh.current = reload;
    void load();
    window.addEventListener('focus', reload); document.addEventListener('visibilitychange', reload);
    const timer = window.setInterval(reload, 60000);
    return () => { live = false; abort.abort(); refresh.current = null; clearInterval(timer); availability.current(false); window.removeEventListener('freecollab:auth-changed', purge); window.removeEventListener('freecollab:workspace-changed', purge); window.removeEventListener('focus', reload); document.removeEventListener('visibilitychange', reload); };
  }, [scope]);
  useEffect(() => { if (lastRefresh.current !== refreshKey) { lastRefresh.current = refreshKey; refresh.current?.(); } }, [refreshKey]);

  // Effects run too late to protect the first render after a scope change.
  const inScope = () => !!material && material.scope.token === api.getAccessToken() && material.scope.actor === api.getCurrentUserId()
    && material.scope.channel.id === channel.id && material.scope.workspaceId === workspace?.id && material.scope.workspaceId === workspaceId
    && invalidatedScope.current !== scope && latestScope.current === scope && allowsChannelMaterial(channel, workspaceId);
  if (!inScope() || !material) return null;
  const { file, document: page, nextTask } = material;
  return <section className={'fc-chat-shared-material' + (file ? ' has-file' : '') + (page ? ' has-document' : '') + (nextTask ? ' has-task' : '')} aria-label={t('Shared in this channel')}>
    {file && <button type="button" className="fc-chat-material-preview" aria-label={t('Open {filename}', { filename: file.filename })} onClick={() => { if (inScope()) canPreviewFile(file) ? setPreviewOpen(true) : onOpenFiles(); }}>
      <FileThumbnail key={file.id} file={file} />
      <span className="fc-chat-material-caption"><strong>{file.filename}</strong><span>{t('Shared in this channel')}<ArrowUpRight size={13} /></span></span>
    </button>}
    {page && <article key={page.record.id} className="fc-chat-material-document" data-document-id={page.record.id}>
      <header className="fc-chat-material-eyebrow"><FileText size={14} aria-hidden="true" /><span>{t('Shared document')}</span><ArrowUpRight size={15} aria-hidden="true" /></header>
      <h3><button type="button" className="fc-chat-material-document-link" onClick={() => { if (inScope()) onOpenDocuments(page.record.id, page.stack?.id); }}>{page.record.title}</button></h3>
      {page.excerpt && <p className="fc-chat-material-excerpt">{page.excerpt}</p>}
      {page.stack && <button type="button" className="fc-chat-material-stack" onClick={() => { if (inScope()) onOpenDocuments(undefined, page.stack!.id); }}><Layers size={14} aria-hidden="true" /><span>{page.stack.title}</span><small>{page.stackDocumentCount} {t('Documents')}</small></button>}
      <footer className="fc-chat-material-footer"><button type="button" onClick={() => { if (inScope()) onOpenDocuments(page.record.id, page.stack?.id); }}>{t('Open document')}<ArrowUpRight size={14} aria-hidden="true" /></button></footer>
    </article>}
    {nextTask && <button type="button" className="fc-chat-material-next-step" data-material-task-id={nextTask.record.id} onClick={() => { if (inScope()) onOpenTasks(nextTask.record.id, nextTask.project.id); }}>
      <span className="fc-chat-material-eyebrow"><CheckSquare size={14} aria-hidden="true" />{t('Next step')}<ArrowUpRight size={15} aria-hidden="true" /></span>
      <strong>{nextTask.record.fields!.title}</strong>
      <small>{nextTask.project.fields!.title}<span aria-hidden="true"> · </span>{t(nextTask.record.fields!.status === 'doing' ? 'In progress' : 'To do')}</small>
    </button>}
    {previewOpen && file && <FilePreview key={file.id} file={file} onClose={() => setPreviewOpen(false)} />}
  </section>;
}
