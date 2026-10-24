import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { avatarApi } from '../lib/avatar-api';
import '../styles/avatar.css';

interface Props {
  userId: string;
  name?: string;
  workspaceId?: string;
  size?: number;
  className?: string;
  onPhotoState?: (present: boolean) => void;
}
interface Photo { url: string; token: string; userId: string; workspaceId?: string; revision: number }
const initials = (name: string) => name.trim().split(/\s+/).slice(0, 2).map(part => Array.from(part)[0]).join('').toUpperCase() || '?';

/** A real account photo or initials. Every byte is fetched with the current session and scope. */
export function Avatar({ userId, name = '', workspaceId, size, className = '', onPhotoState }: Props) {
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [revision, setRevision] = useState(0);
  const [credential, setCredential] = useState(api.getAccessToken());
  const [eventScope, setEventScope] = useState<string | undefined>();
  const scope = workspaceId || eventScope;
  useEffect(() => {
    const invalidate = () => { setPhoto(null); setCredential(api.getAccessToken()); setRevision(value => value + 1); };
    const changed = (event: Event) => {
      if ((event as CustomEvent<{ user_id: string }>).detail?.user_id === userId) invalidate();
    };
    const workspace = (event: Event) => {
      setEventScope((event as CustomEvent<{ workspace_id: string | null }>).detail?.workspace_id || undefined);
      invalidate();
    };
    window.addEventListener('freecollab:auth-changed', invalidate);
    window.addEventListener('freecollab:workspace-changed', workspace);
    window.addEventListener('freecollab:avatar-changed', changed);
    return () => {
      window.removeEventListener('freecollab:auth-changed', invalidate);
      window.removeEventListener('freecollab:workspace-changed', workspace);
      window.removeEventListener('freecollab:avatar-changed', changed);
    };
  }, [userId]);
  useEffect(() => {
    const controller = new AbortController();
    let active = true, url = '';
    setPhoto(null);
    onPhotoState?.(false);
    if (credential && userId && credential === api.getAccessToken()) {
      void avatarApi.read(userId, scope, controller.signal).then(blob => {
        if (!active || credential !== api.getAccessToken() || blob.type !== 'image/png' || blob.size > 1024 * 1024) return;
        url = URL.createObjectURL(blob);
        setPhoto({ url, token: credential, userId, workspaceId: scope, revision });
        onPhotoState?.(true);
      }).catch(() => { /* Missing, unsupported or no-longer-shared photos use initials. */ });
    }
    return () => { active = false; controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [userId, scope, credential, revision, onPhotoState]);
  const visible = photo && photo.userId === userId && photo.token === api.getAccessToken()
    && photo.workspaceId === scope && photo.revision === revision;
  return <span className={`fc-profile-avatar ${className}`} data-user-id={userId} aria-hidden="true" style={size ? { width: size, height: size, fontSize: size * .34 } : undefined}>
    {visible ? <img src={photo.url} alt="" draggable={false} onError={() => { URL.revokeObjectURL(photo.url); setPhoto(null); onPhotoState?.(false); }} /> : initials(name)}
  </span>;
}
