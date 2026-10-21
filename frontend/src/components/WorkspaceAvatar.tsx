import { useI18n } from '../i18n';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { workspaceApi, type Workspace } from '../lib/workspace-api';

export function WorkspaceAvatar({ workspace, large = false }: { workspace: Workspace | null; large?: boolean }) {
  useI18n();
  const [url, setURL] = useState('');
  useEffect(() => {
    const accountId = api.getCurrentUserId();
    let active = true;
    let objectURL = '';
    setURL('');
    if (workspace?.logo_url) void workspaceApi.logo(workspace.id).then(blob => {
      if (!active || accountId !== api.getCurrentUserId()) return;
      objectURL = URL.createObjectURL(blob); setURL(objectURL);
    }).catch(() => {});
    return () => { active = false; if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [workspace]);
  const initials = workspace?.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'W';
  return <span className={`fc-workspace-avatar${large ? ' fc-workspace-avatar-large' : ''}`} aria-hidden="true">{url ? <img src={url} alt="" /> : initials}</span>;
}
