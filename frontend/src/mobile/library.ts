/** Explicit, bounded local copies. No private response enters the service worker. */
import { api, APIError, type Message } from '../lib/api';
import { downloadChannelFile, listChannelFiles, uploadChannelFile, type ManagedFile } from '../lib/files-api';
import { assertOwner, fingerprint, listPrivate, owner, removePrivate, savePrivate, type Owner } from './storage';

export const OFFLINE_LIMITS = { channels: 10, messages: 200, fileBytes: 5 * 1024 * 1024, totalBytes: 25 * 1024 * 1024, files: 20, queuedFiles: 10, maxAge: 7 * 86400000 };
export interface OfflineChannel { id: string; name: string; secure: boolean; messages: Message[]; authors?: Record<string, string>; savedAt: string; tokenFingerprint: string }
export interface OfflineFile { id: string; channelId: string; name: string; type: string; size: number; data: string; savedAt: string; tokenFingerprint: string }
export interface QueuedAttachment extends OfflineFile { secure: boolean; state: 'queued' | 'review'; documentId?: string; error?: string }
const changed = () => window.dispatchEvent(new Event('freecollab:mobile-state'));
let flushing: Promise<void> | null = null, serial = Promise.resolve();
async function exclusive<T>(expected: Owner, operation: () => Promise<T>): Promise<T> {
  const work = async () => { assertOwner(expected); return operation(); };
  if (navigator.locks) return navigator.locks.request('freecollab-offline-library:' + expected.user, work);
  const next = serial.then(work, work); serial = next.then(() => undefined, () => undefined); return next;
}
function embedded() { if (api.isMemorySession()) throw new Error('Offline history and files need the standalone app on this device.'); }
function fresh(row: { savedAt: string; tokenFingerprint: string }, hash: string) { return row.tokenFingerprint === hash && Date.now() - Date.parse(row.savedAt) <= OFFLINE_LIMITS.maxAge && Date.parse(row.savedAt) <= Date.now() + 60000; }
async function sameSession(expected: Owner) {
  const me = await api.request<{ id: string }>('/auth/me', { signal: AbortSignal.timeout(20000) });
  assertOwner(expected); if (me.id !== expected.user) throw new Error('The signed-in account changed.');
}
async function authority(channelId: string, expected: Owner, expectedSecure?: boolean) {
  await sameSession(expected);
  const members = await api.request<Array<{ user_id: string }>>('/channels/' + encodeURIComponent(channelId) + '/members', { signal: AbortSignal.timeout(20000) });
  assertOwner(expected); if (!members.some(member => member.user_id === expected.user)) throw new APIError('You no longer belong to this channel.', 403);
  const secure = await (await import('../lib/secure-client')).getSecureMessaging();
  const encrypted = await secure.handles(channelId); assertOwner(expected);
  if (expectedSecure !== undefined && expectedSecure !== encrypted) throw new APIError('This channel’s encryption mode changed.', 409);
  // The normal secure reader unlocks the current device and validates room access.
  if (encrypted) await secure.roomFor(channelId);
  assertOwner(expected); return encrypted;
}
async function history(channelId: string, expected: Owner) {
  const first = await api.getMessages(channelId, { limit: 100 }); assertOwner(expected);
  if (first.length < 100) return first;
  const second = await api.getMessages(channelId, { limit: 100, before: first[first.length - 1].id }); assertOwner(expected);
  return [...first, ...second].filter((row, index, rows) => rows.findIndex(item => item.id === row.id) === index);
}
async function authorNames(channelId: string, expected: Owner) {
  const members = await api.request<Array<{ user_id: string; name?: string }>>('/channels/' + encodeURIComponent(channelId) + '/members', { signal: AbortSignal.timeout(20000) });
  assertOwner(expected);
  return Object.fromEntries(members.slice(0, 2000).filter(member => typeof member.name === 'string').map(member => [member.user_id, member.name!.slice(0, 200)]));
}
async function purgeChannel(channelId: string, expected: Owner) {
  await removePrivate('offline-channel', channelId, expected);
  for (const row of await listPrivate<OfflineFile>('offline-file', expected)) if (row.value.channelId === channelId) await removePrivate('offline-file', row.id, expected);
}
async function channels(expected: Owner) {
  const hash = await fingerprint(expected.token), result: OfflineChannel[] = [];
  for (const row of await listPrivate<OfflineChannel>('offline-channel', expected)) {
    if (fresh(row.value, hash)) result.push(row.value); else await purgeChannel(row.id, expected);
  }
  return result;
}
async function fileRecords(expected: Owner) {
  const hash = await fingerprint(expected.token), result: OfflineFile[] = [];
  for (const row of await listPrivate<OfflineFile>('offline-file', expected)) {
    if (fresh(row.value, hash)) result.push(row.value); else await removePrivate('offline-file', row.id, expected);
  }
  return result;
}
async function encode(blob: Blob) {
  if (blob.size > OFFLINE_LIMITS.fileBytes) throw new Error('Offline files must be no larger than 5 MB.');
  const bytes = new Uint8Array(await blob.arrayBuffer()); let text = '';
  for (let index = 0; index < bytes.length; index += 16384) text += String.fromCharCode(...bytes.subarray(index, index + 16384));
  return btoa(text);
}
function decode(row: OfflineFile): Blob {
  if (row.size < 0 || row.size > OFFLINE_LIMITS.fileBytes || row.data.length > Math.ceil(OFFLINE_LIMITS.fileBytes / 3) * 4) throw new Error('The offline file is invalid.');
  const bytes = Uint8Array.from(atob(row.data), byte => byte.charCodeAt(0));
  if (bytes.byteLength !== row.size) throw new Error('The offline file is incomplete.');
  return new Blob([bytes], { type: row.type });
}
async function capacity(size: number, expected: Owner) {
  const saved = await fileRecords(expected), queued = await listPrivate<QueuedAttachment>('attachment-outbox', expected);
  if (saved.reduce((sum, row) => sum + row.size, 0) + queued.reduce((sum, row) => sum + row.value.size, 0) + size > OFFLINE_LIMITS.totalBytes)
    throw new Error('Offline files use up to 25 MB. Remove an old file before saving another.');
}

export const offlineLibrary = {
  async list(): Promise<{ channels: OfflineChannel[]; files: OfflineFile[] }> {
    embedded(); const expected = owner();
    return exclusive(expected, async () => {
      let saved = await channels(expected);
      // Online display must pass current server authority and refresh content.
      if (navigator.onLine) {
        for (const row of saved) {
          try {
            await authority(row.id, expected, row.secure);
            const messages = await history(row.id, expected);
            row.messages = messages.filter(message => !message.deleted && !message.locked).slice(0, OFFLINE_LIMITS.messages);
            row.authors = await authorNames(row.id, expected);
            row.savedAt = new Date().toISOString(); await savePrivate('offline-channel', row.id, row, expected);
            const liveFiles = await listChannelFiles(row.id); assertOwner(expected);
            for (const file of await fileRecords(expected)) if (file.channelId === row.id && !liveFiles.some(current => current.id === file.id)) await removePrivate('offline-file', file.id, expected);
          } catch (error) {
            assertOwner(expected);
            if (!(error instanceof TypeError) && !(error instanceof DOMException && ['NetworkError', 'TimeoutError', 'AbortError'].includes(error.name))) await purgeChannel(row.id, expected);
            // A failed online check never displays retained content.
            saved = saved.filter(current => current.id !== row.id);
          }
        }
      }
      const allowed = new Set(saved.map(row => row.id));
      return { channels: saved, files: (await fileRecords(expected)).filter(row => allowed.has(row.channelId)) };
    });
  },
  async enable(channelId: string, name: string, expectedSecure: boolean) {
    embedded(); if (!navigator.onLine) throw new Error('Connect once to save this channel for offline reading.');
    const expected = owner();
    return exclusive(expected, async () => {
      const rows = await channels(expected);
      if (rows.length >= OFFLINE_LIMITS.channels && !rows.some(row => row.id === channelId)) throw new Error('Save up to ten channels for offline reading.');
      const secure = await authority(channelId, expected, expectedSecure);
      const messages = await history(channelId, expected);
      const row: OfflineChannel = { id: channelId, name: name.slice(0, 200), secure, messages: messages.filter(message => !message.deleted && !message.locked).slice(0, OFFLINE_LIMITS.messages), authors: await authorNames(channelId, expected), savedAt: new Date().toISOString(), tokenFingerprint: await fingerprint(expected.token) };
      await savePrivate('offline-channel', channelId, row, expected); changed();
    });
  },
  async capture(channelId: string, messages: Message[]) {
    if (api.isMemorySession() || !navigator.onLine) return;
    const expected = owner();
    await exclusive(expected, async () => {
      const row = (await channels(expected)).find(channel => channel.id === channelId); if (!row) return;
      await authority(channelId, expected, row.secure);
      row.messages = messages.filter(message => message.channel_id === channelId && !message.deleted && !message.locked).slice(-OFFLINE_LIMITS.messages);
      row.savedAt = new Date().toISOString(); await savePrivate('offline-channel', channelId, row, expected);
    });
  },
  async forget(channelId: string) { const expected = owner(); await exclusive(expected, () => purgeChannel(channelId, expected)); changed(); },
  async saveFile(file: ManagedFile) {
    embedded(); const expected = owner();
    await exclusive(expected, async () => {
      const channel = (await channels(expected)).find(row => row.id === file.channel_id);
      if (!channel) throw new Error('Save this channel for offline reading before saving its files.');
      await authority(channel.id, expected, channel.secure);
      const current = (await listChannelFiles(channel.id)).find(row => row.id === file.id); assertOwner(expected);
      if (!current || current.size > OFFLINE_LIMITS.fileBytes) throw new Error('Choose a current file no larger than 5 MB.');
      const rows = await fileRecords(expected);
      if (rows.some(row => row.id === file.id)) return;
      if (rows.length >= OFFLINE_LIMITS.files) throw new Error('Save up to twenty offline files.');
      await capacity(current.size, expected);
      const blob = await downloadChannelFile(current); assertOwner(expected);
      if (blob.size !== current.size) throw new Error('The downloaded file size could not be verified.');
      await authority(channel.id, expected, channel.secure);
      const row: OfflineFile = { id: current.id, channelId: current.channel_id, name: current.filename, type: current.content_type, size: blob.size, data: await encode(blob), savedAt: new Date().toISOString(), tokenFingerprint: await fingerprint(expected.token) };
      await savePrivate('offline-file', row.id, row, expected); changed();
    });
  },
  async fileBlob(id: string) {
    const listing = await offlineLibrary.list(), row = listing.files.find(file => file.id === id);
    if (!row) throw new Error('This offline file is no longer available.');
    return { blob: decode(row), name: row.name };
  },
  async removeFile(id: string) { await removePrivate('offline-file', id); changed(); },
};

export const attachmentOutbox = {
  async list() { return (await listPrivate<QueuedAttachment>('attachment-outbox')).map(row => row.value).sort((a, b) => a.savedAt.localeCompare(b.savedAt)); },
  async enqueue(channelId: string, file: File, documentId?: string, requestId: string = crypto.randomUUID()) {
    embedded(); const expected = owner();
    await exclusive(expected, async () => {
      const channel = (await channels(expected)).find(row => row.id === channelId);
      if (!channel) throw new Error('Save this channel for offline reading before queuing files.');
      const rows = await attachmentOutbox.list();
      if (rows.some(row => row.id === requestId)) return;
      if (rows.length >= OFFLINE_LIMITS.queuedFiles) throw new Error('The attachment queue holds up to ten files.');
      if (file.size > OFFLINE_LIMITS.fileBytes) throw new Error('Offline files must be no larger than 5 MB.');
      await capacity(file.size, expected);
      const row: QueuedAttachment = { id: requestId, channelId, name: file.name, type: file.type, size: file.size, data: await encode(file), savedAt: new Date().toISOString(), tokenFingerprint: await fingerprint(expected.token), secure: channel.secure, state: 'queued', documentId };
      await savePrivate('attachment-outbox', row.id, row, expected); changed();
    });
  },
  async remove(id: string) { await removePrivate('attachment-outbox', id); changed(); },
  async flush() {
    if (flushing) return flushing;
    flushing = (async () => {
      if (!navigator.onLine || !api.getCurrentUserId() || api.isMemorySession()) return;
      const expected = owner();
      const work = async () => {
        for (const row of await attachmentOutbox.list()) {
          assertOwner(expected); if (!navigator.onLine) break; if (row.state !== 'queued') continue;
          try {
            if (!fresh(row, await fingerprint(expected.token))) throw new Error('Your sign-in changed or this queued file expired.');
            await authority(row.channelId, expected, row.secure);
            if (row.documentId) {
              const currentFiles = await listChannelFiles(row.channelId); assertOwner(expected);
              if (!currentFiles.some(file => (file.document_id || file.id) === row.documentId)) throw new Error('The original document is no longer available in this channel.');
            }
            const saved = await uploadChannelFile(row.channelId, new File([decode(row)], row.name, { type: row.type }), { requestId: row.id, documentId: row.documentId });
            assertOwner(expected); await removePrivate('attachment-outbox', row.id, expected);
            window.dispatchEvent(new CustomEvent('freecollab:offline-file-sent', { detail: saved })); changed();
          } catch (error) {
            assertOwner(expected);
            // Ambiguous transfers retain the same idempotency key for retry.
            if (!navigator.onLine || error instanceof TypeError || error instanceof DOMException && ['NetworkError', 'TimeoutError', 'AbortError'].includes(error.name) || error instanceof Error && /Upload interrupted/.test(error.message)) break;
            row.state = 'review'; row.error = 'Your sign-in, channel access or encryption state changed. Remove this item and review the channel before sending again.';
            await savePrivate('attachment-outbox', row.id, row, expected); changed();
          }
        }
      };
      if (navigator.locks) await navigator.locks.request('freecollab-attachment-send:' + expected.user, work); else await work();
    })().finally(() => { flushing = null; });
    return flushing;
  },
};
