import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { useAccount } from '../account/AccountProvider';
import { t, useI18n } from '../i18n';
import { api, type Channel } from '../lib/api';
import { listChannelFiles, type ManagedFile } from '../lib/files-api';
import { FileThumbnail } from './FileGallery';
import { FilePreview, canPreviewFile } from './FilePreview';

/** Existing channel material, shown only in a room with space to read it. */
export function ChatSharedMaterial({ channel, workspaceId, onOpenFiles, onAvailabilityChange }: {
  channel: Channel;
  workspaceId?: string;
  onOpenFiles: () => void;
  onAvailabilityChange: (available: boolean) => void;
}) {
  useI18n();
  const { workspace } = useAccount();
  const [material, setMaterial] = useState<{ file: ManagedFile; token: string; workspaceId?: string } | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const availability = useRef(onAvailabilityChange);
  availability.current = onAvailabilityChange;

  useEffect(() => {
    let live = true;
    const token = api.getAccessToken();
    setMaterial(null); setPreviewOpen(false); availability.current(false);
    const purge = () => { live = false; setMaterial(null); setPreviewOpen(false); availability.current(false); };
    window.addEventListener('freecollab:auth-changed', purge);
    if (token && workspace?.id === workspaceId && (!channel.workspace_id || channel.workspace_id === workspaceId)) {
      void listChannelFiles(channel.id).then(files => {
        if (!live || token !== api.getAccessToken()) return;
        const file = files.filter(item => item.channel_id === channel.id)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
        if (file) { setMaterial({ file, token, workspaceId }); availability.current(true); }
      }).catch(() => { /* The channel files tool retains its full retry/error flow. */ });
    }
    return () => { live = false; availability.current(false); window.removeEventListener('freecollab:auth-changed', purge); };
  }, [channel.id, channel.workspace_id, workspaceId, workspace?.id]);

  // Check scope during render too: an account/workspace change must never paint
  // retained material while the previous effect is waiting to clean up.
  if (!material || material.token !== api.getAccessToken() || material.file.channel_id !== channel.id || material.workspaceId !== workspace?.id || material.workspaceId !== workspaceId) return null;
  const file = material.file;
  return <section className="fc-chat-shared-material" aria-label={t('Shared in this channel')}>
    <button type="button" className="fc-chat-material-preview" aria-label={t('Open {filename}', { filename: file.filename })} onClick={() => canPreviewFile(file) ? setPreviewOpen(true) : onOpenFiles()}>
      <FileThumbnail file={file} />
      <span className="fc-chat-material-caption"><strong>{file.filename}</strong><span>{t('Shared in this channel')}<ArrowUpRight size={13} /></span></span>
    </button>
    {previewOpen && <FilePreview key={file.id} file={file} onClose={() => setPreviewOpen(false)} />}
  </section>;
}
