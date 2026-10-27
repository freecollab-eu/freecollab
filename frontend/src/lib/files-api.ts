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
  meetingCapture?: { receiptId: string; clipId: string };
  meetingCaptureProof?: Record<string, unknown>;
  beforePublish?: () => void | Promise<void>;
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

function assertFileOwner(token: string | null) {
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed. Please try again.');
}

async function providerFor(channelId: string, token: string | null): Promise<ChannelFileProvider | null> {
  // Resolve this account's binding before transmitting file bytes. The loader
  // also clears the prior account's cached bindings when its token changes.
  const { getSecureMessaging } = await import('./secure-client');
  assertFileOwner(token);
  await getSecureMessaging();
  assertFileOwner(token);
  if (!secureProvider) throw new Error('File transfer is not ready. Please try again.');
  const provider = secureProvider;
  // A failed secure lookup must propagate, never fall back to plaintext upload.
  const secure = await provider.handles(channelId);
  assertFileOwner(token);
  return secure ? provider : null;
}

export function wasCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function abortError() { return new DOMException('Upload cancelled.', 'AbortError'); }

function expireCurrentSession(requestToken: string | null) {
  if (requestToken && requestToken === api.getAccessToken()) window.dispatchEvent(new Event('freecollab:session-expired'));
}

function uploadNative(channelId: string, file: File, options: FileTransferOptions, token: string | null): Promise<ManagedFile> {
  return new Promise((resolve, reject) => {
    assertFileOwner(token);
    if (options.signal?.aborted) { reject(abortError()); return; }
    const xhr = new XMLHttpRequest();
    let settled = false;
    const endpoint = options.documentId
      ? `/files/${encodeURIComponent(options.documentId)}/versions`
      : `/files/channels/${encodeURIComponent(channelId)}`;
    xhr.open('POST', `${api.getBaseUrl()}${endpoint}`);
    xhr.responseType = 'json';
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    if (options.requestId) xhr.setRequestHeader('Idempotency-Key', options.requestId);
    if (options.meetingCapture) {
      xhr.setRequestHeader('X-Meeting-Capture-Receipt', options.meetingCapture.receiptId);
      xhr.setRequestHeader('X-Meeting-Capture-Clip-ID', options.meetingCapture.clipId);
    }
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
        try { assertFileOwner(token); } catch (error) { reject(error); return; }
        if(token===api.getAccessToken()&&Array.isArray(xhr.response.policy_warnings)&&xhr.response.policy_warnings.length)
          window.dispatchEvent(new CustomEvent('freecollab:policy-warning',{detail:{count:xhr.response.policy_warnings.length}}));
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
  const token = api.getAccessToken(), provider = await providerFor(channelId, token);
  assertFileOwner(token);
  const files = await (provider ? provider.list(channelId) : api.getFiles(channelId));
  assertFileOwner(token);
  return files;
}

export async function uploadChannelFile(channelId: string, file: File, options: FileTransferOptions = {}): Promise<ManagedFile> {
  const token = api.getAccessToken(), provider = await providerFor(channelId, token);
  assertFileOwner(token);
  if (options.signal?.aborted) throw abortError();
  if (provider) {
    const saved = await provider.upload(channelId, file, options);
    assertFileOwner(token);
    return saved;
  }
  await options.beforePublish?.();
  assertFileOwner(token);
  if (options.signal?.aborted) throw abortError();
  return uploadNative(channelId, file, options, token);
}

export async function downloadChannelFile(file: ManagedFile, options: Pick<FileTransferOptions, 'signal'> = {}): Promise<Blob> {
  const token = api.getAccessToken(), provider = await providerFor(file.channel_id, token);
  assertFileOwner(token);
  if (provider) {
    const blob = await provider.download(file, options);
    assertFileOwner(token);
    return blob;
  }
  if (file.encrypted || file.secure_locator) throw new Error('Unlock this encrypted channel before opening its files.');
  const response = await fetch(`${api.getBaseUrl()}/files/${encodeURIComponent(file.id)}`, {
    signal: options.signal,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    if (response.status === 401) expireCurrentSession(token);
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Download failed. Please try again.');
  }
  const blob = await response.blob();
  assertFileOwner(token);
  return blob;
}

export async function listFileVersions(file: ManagedFile): Promise<ManagedFile[]> {
  const token = api.getAccessToken(), provider = await providerFor(file.channel_id, token);
  assertFileOwner(token);
  const versions = await (provider ? provider.versions(file) : api.request<ManagedFile[]>(`/files/${encodeURIComponent(file.id)}/versions`));
  assertFileOwner(token);
  return versions;
}

export async function deleteChannelFile(file: ManagedFile): Promise<void> {
  const token = api.getAccessToken(), provider = await providerFor(file.channel_id, token);
  assertFileOwner(token);
  if (provider) await provider.delete(file);
  else await api.deleteFile(file.id);
  assertFileOwner(token);
}
