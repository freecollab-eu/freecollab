import { api } from './api';

interface PendingPhoto { userId: string; token: string; promise: Promise<Blob>; controller: AbortController; readers: number; settled: boolean }
const pendingPhotos = new Map<string, PendingPhoto>();
function clearPendingPhotos(event?: Event) {
  const userId = event?.type === 'freecollab:avatar-changed' ? (event as CustomEvent<{ user_id: string }>).detail?.user_id : null;
  for (const [key, entry] of pendingPhotos) if (!userId || entry.userId === userId) {
    pendingPhotos.delete(key); entry.controller.abort();
  }
}
window.addEventListener('freecollab:auth-changed', clearPendingPhotos);
window.addEventListener('freecollab:workspace-changed', clearPendingPhotos);
window.addEventListener('freecollab:avatar-changed', clearPendingPhotos);

/** Share only a pending byte read. Each consumer owns its URL and cancellation. */
function readPhoto(userId: string, workspaceId?: string, signal?: AbortSignal): Promise<Blob> {
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  const token = api.getAccessToken() || '';
  const key = JSON.stringify([token, workspaceId || '', userId]);
  let entry = pendingPhotos.get(key);
  if (!entry) {
    const controller = new AbortController();
    const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    entry = { userId, token, controller, readers: 0, settled: false,
      promise: api.requestBlob(`/users/${encodeURIComponent(userId)}/avatar${query}`, { signal: controller.signal }) };
    // Bound bookkeeping even if a large member directory mounts at once.
    if (pendingPhotos.size < 256) pendingPhotos.set(key, entry);
    const current = entry;
    const complete = () => { current.settled = true; if (pendingPhotos.get(key) === current) pendingPhotos.delete(key); };
    void current.promise.then(complete, complete);
  }
  const shared = entry;
  shared.readers++;
  return new Promise<Blob>((resolve, reject) => {
    let completed = false;
    const release = () => {
      completed = true; signal?.removeEventListener('abort', abort); shared.readers--;
      if (!shared.readers && !shared.settled) {
        if (pendingPhotos.get(key) === shared) pendingPhotos.delete(key);
        shared.controller.abort();
      }
    };
    const abort = () => { if (!completed) { release(); reject(new DOMException('Aborted', 'AbortError')); } };
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    void shared.promise.then(blob => {
      if (completed) return;
      release();
      if (shared.controller.signal.aborted || shared.token !== api.getAccessToken()) reject(new DOMException('Session changed', 'AbortError'));
      else resolve(blob);
    }, error => { if (!completed) { release(); reject(error); } });
  });
}

export const avatarApi = {
  read: readPhoto,
  async upload(photo: Blob) {
    const form = new FormData();
    form.append('file', photo, 'profile-photo.png');
    return api.uploadMultipart<{ has_avatar: boolean }>('/account/avatar', form);
  },
  remove() { return api.request<void>('/account/avatar', { method: 'DELETE' }); },
};

/** Pixel-only square preview; source filename, metadata and external URLs never leave this device. */
export async function prepareProfilePhoto(file: File): Promise<Blob> {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024)
    throw new Error('Choose a PNG, JPEG or WebP photo up to 5 MB.');
  const image = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    if (image.width < 1 || image.height < 1 || image.width > 8192 || image.height > 8192)
      throw new Error('Choose a photo no larger than 8192 × 8192 pixels.');
    const side = Math.min(image.width, image.height);
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 256;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('This browser could not prepare your photo.');
    context.drawImage(image, (image.width - side) / 2, (image.height - side) / 2, side, side, 0, 0, 256, 256);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => {
      if (blob && blob.size <= 1024 * 1024) resolve(blob);
      else reject(new Error('This browser could not prepare your photo.'));
    }, 'image/png'));
  } finally { image.close(); }
}

export function announceAvatarChange(userId: string) {
  window.dispatchEvent(new CustomEvent('freecollab:avatar-changed', { detail: { user_id: userId } }));
}
