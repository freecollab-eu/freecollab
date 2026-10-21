import { api, type FileInfo } from './api';

export interface ManagedFile extends FileInfo {
  document_id?: string;
  document_owner_id?: string;
  version_number?: number;
  version_count?: number;
  secure_locator?: string;
  secure_room_id?: string;
  secure_url?: string;
  encrypted_file?: unknown;
}

export interface FileTransferOptions {
  signal?: AbortSignal;
  onProgress?: (percent: number) => void;
  documentId?: string;
  requestId?: string;
}

/** Secure implementations own encryption/decryption and authenticated metadata. */
export interface ChannelFileProvider {
  handles: (channelId: string) => boolean | Promise<boolean>;
  list: (channelId: string) => Promise<ManagedFile[]>;
  upload: (channelId: string, file: File, options: FileTransferOptions) => Promise<ManagedFile>;
  download: (file: ManagedFile, options?: Pick<FileTransferOptions, 'signal'>) => Promise<Blob>;
  versions: (file: ManagedFile) => Promise<ManagedFile[]>;
  delete: (file: ManagedFile) => Promise<void>;
}

let secureProvider: ChannelFileProvider | null = null;

export function registerSecureFileProvider(provider: ChannelFileProvider): () => void {
  secureProvider = provider;
  return () => { if (secureProvider === provider) secureProvider = null; };
}

async function providerFor(channelId: string): Promise<ChannelFileProvider | null> {
  // Resolve this account's binding before transmitting file bytes. The loader
  // also clears the prior account's cached bindings when its token changes.
  const { getSecureMessaging } = await import('./secure-client');
  await getSecureMessaging();
  if (!secureProvider) throw new Error('File transfer is not ready. Please try again.');
  const provider = secureProvider;
  // A failed secure lookup must propagate, never fall back to plaintext upload.
  return provider && await provider.handles(channelId) ? provider : null;
}

export function wasCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function abortError() { return new DOMException('Upload cancelled.', 'AbortError'); }

function expireCurrentSession(requestToken: string | null) {
  if (requestToken && requestToken === api.getAccessToken()) window.dispatchEvent(new Event('freecollab:session-expired'));
}

function uploadNative(channelId: string, file: File, options: FileTransferOptions): Promise<ManagedFile> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) { reject(abortError()); return; }
    const xhr = new XMLHttpRequest();
    let settled = false;
    const token = api.getAccessToken();
    const endpoint = options.documentId
      ? `/files/${encodeURIComponent(options.documentId)}/versions`
      : `/files/channels/${encodeURIComponent(channelId)}`;
    xhr.open('POST', `${api.getBaseUrl()}${endpoint}`);
    xhr.responseType = 'json';
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    if (options.requestId) xhr.setRequestHeader('Idempotency-Key', options.requestId);
    const cancel = () => xhr.abort();
    const cleanup = () => { settled = true; options.signal?.removeEventListener('abort', cancel); };
    options.signal?.addEventListener('abort', cancel, { once: true });
    xhr.upload.onprogress = event => {
      if (!settled && event.lengthComputable) options.onProgress?.(Math.min(99, Math.round(event.loaded / event.total * 100)));
    };
    xhr.onload = () => {
      if (settled) return;
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300 && xhr.response && typeof xhr.response.id === 'string') {
        options.onProgress?.(100);
        resolve(xhr.response as ManagedFile);
      } else {
        if (xhr.status === 401) expireCurrentSession(token);
        reject(new Error(typeof xhr.response?.detail === 'string' ? xhr.response.detail : 'Upload failed. Please try again.'));
      }
    };
    xhr.onerror = () => { if (!settled) { cleanup(); reject(new Error('Upload interrupted. Check your connection and retry.')); } };
    xhr.onabort = () => { if (!settled) { cleanup(); reject(abortError()); } };
    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });
}

export async function listChannelFiles(channelId: string): Promise<ManagedFile[]> {
  const provider = await providerFor(channelId);
  return provider ? provider.list(channelId) : api.getFiles(channelId);
}

export async function uploadChannelFile(channelId: string, file: File, options: FileTransferOptions = {}): Promise<ManagedFile> {
  const provider = await providerFor(channelId);
  if (options.signal?.aborted) throw abortError();
  return provider ? provider.upload(channelId, file, options) : uploadNative(channelId, file, options);
}

export async function downloadChannelFile(file: ManagedFile, options: Pick<FileTransferOptions, 'signal'> = {}): Promise<Blob> {
  const provider = await providerFor(file.channel_id);
  if (provider) return provider.download(file, options);
  if (file.encrypted || file.secure_locator) throw new Error('Unlock this encrypted channel before opening its files.');
  const token = api.getAccessToken();
  const response = await fetch(`${api.getBaseUrl()}/files/${encodeURIComponent(file.id)}`, {
    signal: options.signal,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    if (response.status === 401) expireCurrentSession(token);
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Download failed. Please try again.');
  }
  return response.blob();
}

export async function listFileVersions(file: ManagedFile): Promise<ManagedFile[]> {
  const provider = await providerFor(file.channel_id);
  return provider ? provider.versions(file) : api.request(`/files/${encodeURIComponent(file.id)}/versions`);
}

export async function deleteChannelFile(file: ManagedFile): Promise<void> {
  const provider = await providerFor(file.channel_id);
  if (provider) await provider.delete(file);
  else await api.deleteFile(file.id);
}
