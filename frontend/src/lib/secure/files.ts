import { encryptAttachment, decryptAttachment, type IEncryptedFile } from 'matrix-encrypt-attachment';
import type { ChannelFileProvider, ManagedFile } from '../files-api';
import type { MatrixEvent } from 'matrix-js-sdk';
import { secureMessaging } from './index';
import { FILE_EXPERIENCE_TYPES } from '../file-preview';

const FILE_EVENT = 'eu.freecollab.file';
const MAX_SIZE = 50 * 1024 * 1024;
const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf', 'text/plain', 'application/json', ...FILE_EXPERIENCE_TYPES, 'application/vnd.oasis.opendocument.text', 'application/vnd.oasis.opendocument.spreadsheet', 'application/vnd.oasis.opendocument.presentation']);
interface Receipt { file: ManagedFile; fingerprint: string }
interface FileIndex { files: ManagedFile[]; receipts: Map<string, Receipt>; deleted: Set<string>; recordingFailures: Map<string, unknown> }
function check(signal?: AbortSignal) { if (signal?.aborted) throw new DOMException('Transfer cancelled.', 'AbortError'); }
async function verifyRecording(channelId: string, event: MatrixEvent) {
  const content = event.getContent();
  if (content.meeting_capture !== undefined || typeof content.request_id === 'string' && content.request_id.startsWith('fc-capture-file-')) {
    if (!content.meeting_capture || typeof content.meeting_capture !== 'object' || Array.isArray(content.meeting_capture)
        || !String(content.request_id).startsWith('fc-capture-file-') || JSON.stringify(content.meeting_capture).length > 16384) throw new Error('The encrypted recording consent proof is invalid.');
    await (await import('../meeting-capture-proof')).verifyEncryptedRecordingFile(channelId, event);
  }
}
const accountId = (sender: string) => /^@fc_([^:]+):/.exec(sender)?.[1];
const domainOf = (sender: string) => sender.slice(sender.indexOf(':') + 1);

function authenticatedOwner(event: MatrixEvent, domain: string, peerServer?: string): string | null {
  const owner = event.getContent().owner_id;
  if (typeof owner !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(owner)) return null;
  if (event.getSender() === `@fc_${owner}:${domain}`) return owner;
  // The authorized room binding supplies one explicitly pinned peer domain.
  // Remote owners retain their domain and cannot claim a local document owner.
  return peerServer && event.getSender() === `@fc_${owner}:${peerServer}` ? event.getSender()! : null;
}

function asFile(channelId: string, event: MatrixEvent, domain: string, peerServer?: string): ManagedFile | null {
  const content = event.getContent(), owner = authenticatedOwner(event, domain, peerServer);
  if (!owner || event.getType() !== FILE_EVENT || !event.isEncrypted() || event.isRedacted() || !event.getId()?.startsWith('$') || content.deleted ||
      typeof content.document_id !== 'string' || !content.document_id || content.document_id.length > 256 ||
      !Number.isSafeInteger(content.version_number) || content.version_number < 1 || content.version_number > 1_000_000 ||
      typeof content.name !== 'string' || !content.name || content.name.length > 512 || !allowedTypes.has(content.content_type) ||
      !Number.isSafeInteger(content.size) || content.size < 0 || content.size > MAX_SIZE ||
      typeof content.file?.url !== 'string' || !/^mxc:\/\/[^/]+\/[^/?#]+$/.test(content.file.url) ||
      typeof content.file?.hashes?.sha256 !== 'string' || typeof content.file?.iv !== 'string' ||
      typeof content.file?.key?.k !== 'string' || content.file.key.kty !== 'oct' || content.file.v !== 'v2') return null;
  return { id: event.getId()!, channel_id: channelId, uploader_id: owner, filename: content.name, content_type: content.content_type, size: content.size,
    encrypted: true, created_at: new Date(event.getTs()).toISOString(), document_id: content.document_id, document_owner_id: owner,
    version_number: content.version_number, version_count: content.version_number, secure_locator: event.getId(), secure_room_id: event.getRoomId(), secure_url: content.file.url, encrypted_file: content.file };
}

async function index(channelId: string, signal?: AbortSignal): Promise<FileIndex> {
  check(signal);
  const { c, room, roomId, federatedPeerServer } = await secureMessaging.roomFor(channelId);
  const domain = domainOf(c.getUserId()!);
  // Metadata remains in encrypted room history. Do not create a server-side name index.
  while (room.oldState.paginationToken) {
    check(signal); const token = room.oldState.paginationToken;
    await c.scrollback(room, 100);
    if (token === room.oldState.paginationToken) break;
  }
  check(signal);
  const events = await secureMessaging.events(channelId), files: ManagedFile[] = [], receipts = new Map<string, Receipt>(), recordingFailures = new Map<string, unknown>();
  const documents = new Map<string, ManagedFile>(), deleted = new Set<string>();
  for (const event of events) {
    if (event.getType() !== FILE_EVENT || event.getRoomId() !== roomId || !event.isEncrypted() || event.isRedacted() || !event.getId()?.startsWith('$')) continue;
    const content = event.getContent(), owner = authenticatedOwner(event, domain, federatedPeerServer);
    if (!owner) continue;
    if (content.deleted === true) {
      if (typeof content.document_id === 'string' && documents.get(content.document_id)?.document_owner_id === owner) deleted.add(content.document_id);
      continue;
    }
    try { await verifyRecording(channelId, event); check(signal); } catch (error) { check(signal); recordingFailures.set(event.getId()!, error); continue; }
    const file = asFile(channelId, event, domain, federatedPeerServer);
    if (!file || deleted.has(file.document_id!)) continue;
    const previous = documents.get(file.document_id!);
    if (previous ? previous.document_owner_id !== owner || file.version_number !== previous.version_number! + 1 : file.version_number !== 1) continue;
    documents.set(file.document_id!, file); files.push(file);
    if (typeof content.request_id === 'string' && typeof content.fingerprint === 'string') receipts.set(`${owner}:${content.request_id}`, { file, fingerprint: content.fingerprint });
  }
  for (const file of files) file.version_count = documents.get(file.document_id!)!.version_number;
  return { files: files.filter(file => !deleted.has(file.document_id!)), receipts, deleted, recordingFailures };
}

async function fingerprint(channelId: string, documentId: string | undefined, file: File, bytes: ArrayBuffer) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  const value = JSON.stringify([channelId, documentId || '', file.name, file.type, hex]);
  const result = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, '0')).join('');
}

async function boundedCiphertext(response: Response, signal?: AbortSignal): Promise<ArrayBuffer> {
  if (Number(response.headers.get('content-length')) > MAX_SIZE) throw new Error('Encrypted attachment exceeds the file limit.');
  if (!response.body) { const data = await response.arrayBuffer(); if (data.byteLength > MAX_SIZE) throw new Error('Encrypted attachment exceeds the file limit.'); return data; }
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) { check(signal); const part = await reader.read(); if (part.done) break; size += part.value.byteLength; if (size > MAX_SIZE) throw new Error('Encrypted attachment exceeds the file limit.'); chunks.push(part.value); }
  } catch (error) { await reader.cancel().catch(() => undefined); throw error; }
  finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes.buffer;
}

/** Only IDs already verified in this encrypted room may become file references. */
export async function verifiedSecureFileReferenceIds(channelId: string, ids: string[]): Promise<Set<string>> {
  const requested = new Set(ids), state = await index(channelId);
  return new Set(state.files.filter(file => requested.has(file.id)).map(file => file.id));
}

/** Return the exact authenticated version, including older versions in this room. */
export async function getVerifiedSecureFileReference(channelId: string, id: string): Promise<ManagedFile | undefined> {
  return (await index(channelId)).files.find(file => file.id === id);
}

export const secureFileProvider: ChannelFileProvider = {
  handles: channelId => secureMessaging.handles(channelId),
  async list(channelId) {
    const { files } = await index(channelId), latest = new Map<string, ManagedFile>();
    for (const file of files) latest.set(file.document_id!, file);
    return [...latest.values()].reverse();
  },
  async upload(channelId, file, options) {
    check(options.signal);
    if (options.meetingCaptureProof && (!options.requestId?.startsWith('fc-capture-file-') || !options.beforePublish)
        || options.requestId?.startsWith('fc-capture-file-') && (!options.meetingCaptureProof || !options.beforePublish)) throw new Error('Recording files require a current consent proof and publication review.');
    if (options.meetingCaptureProof && JSON.stringify(options.meetingCaptureProof).length > 16384) throw new Error('The recording consent proof exceeds its bound.');
    if (file.size > MAX_SIZE) throw new Error('Maximum file size is 50 MB.');
    if (!allowedTypes.has(file.type)) throw new Error('This file type is not supported.');
    const { c, roomId, assertCurrent } = await secureMessaging.roomFor(channelId, true), owner = accountId(c.getUserId()!);
    const current = () => { check(options.signal); assertCurrent(); };
    current();
    if (!owner) throw new Error('Encrypted file identity is unavailable.');
    const bytes = await file.arrayBuffer(); current();
    const hash = await fingerprint(channelId, options.documentId, file, bytes); current();
    const requestId = options.requestId || crypto.randomUUID(), state = await index(channelId, options.signal);
    current();
    const receipt = state.receipts.get(`${owner}:${requestId}`);
    if (receipt) {
      if (receipt.fingerprint !== hash) throw new Error('This upload request was already used for another file.');
      if (state.deleted.has(receipt.file.document_id!)) throw new Error('The previous upload is no longer available.');
      options.onProgress?.(100); return receipt.file;
    }
    const versions = options.documentId ? state.files.filter(row => row.document_id === options.documentId) : [];
    if (options.documentId && (!versions.length || versions.some(row => row.document_owner_id !== owner))) throw new Error('Only the document owner can upload a new version.');
    options.onProgress?.(5);
    const encrypted = await encryptAttachment(bytes); current(); options.onProgress?.(35);
    const controller = new AbortController(), abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    current();
    let uploaded: { content_uri: string };
    try {
      // The homeserver receives anonymous ciphertext, never filename or media type.
      uploaded = await c.uploadContent(new Blob([encrypted.data], { type: 'application/octet-stream' }), {
        type: 'application/octet-stream', includeFilename: false, abortController: controller,
      });
    } finally { options.signal?.removeEventListener('abort', abort); }
    current(); options.onProgress?.(85);
    const documentId = options.documentId || `${owner}:${requestId}`, version = versions.reduce((max, row) => Math.max(max, row.version_number || 1), 0) + 1;
    const metadata = { document_id: documentId, owner_id: owner, version_number: version, request_id: requestId, fingerprint: hash,
      name: file.name, content_type: file.type, size: file.size, file: { ...encrypted.info, url: uploaded.content_uri },
      ...(options.meetingCaptureProof ? {meeting_capture:options.meetingCaptureProof} : {}) };
    const authorize = async () => { current(); await options.beforePublish?.(); current(); };
    current();
    const result = await secureMessaging.sendEvent(channelId, FILE_EVENT, metadata, requestId.startsWith('fc-capture-file-') ? requestId : `file-${requestId}`, {authorize,signal:options.signal});
    current();
    // A retried Matrix transaction may return its original event. Acknowledge
    // only that authenticated event, rather than claiming the new bytes arrived.
    const event = await secureMessaging.getDecryptedEvent(channelId, result.event_id); current();
    const actual = asFile(channelId, event, domainOf(c.getUserId()!));
    if (!actual || event.getRoomId() !== roomId || event.getContent().fingerprint !== hash || event.getContent().request_id !== requestId) throw new Error('The encrypted upload confirmation could not be verified. Refresh and retry safely.');
    const confirmation = await index(channelId, options.signal), confirmed = confirmation.files.find(row => row.id === actual.id);
    current();
    if (confirmation.recordingFailures.has(actual.id)) throw confirmation.recordingFailures.get(actual.id);
    if (!confirmed) throw new Error('Another version completed first, or this document was deleted. Refresh and choose the file again.');
    current(); options.onProgress?.(100); return confirmed;
  },
  async download(file, options = {}) {
    check(options.signal);
    const { c, roomId, federatedPeerServer, assertCurrent } = await secureMessaging.roomFor(file.channel_id);
    const current = () => { check(options.signal); assertCurrent(); };
    current();
    const state = await index(file.channel_id, options.signal), stored = state.files.find(row => row.id === file.id);
    current();
    if (!stored) throw new Error('This encrypted file is no longer available.');
    // Re-read the authenticated room event; injected locators and keys are ignored.
    const event = await secureMessaging.getDecryptedEvent(file.channel_id, stored.id); current();
    const actual = asFile(file.channel_id, event, domainOf(c.getUserId()!), federatedPeerServer);
    if (!actual?.secure_url || event.getRoomId() !== roomId) throw new Error('Encrypted attachment metadata is unavailable.');
    await verifyRecording(file.channel_id, event); current();
    const parsed = /^mxc:\/\/([^/]+)\/([^/?#]+)$/.exec(actual.secure_url);
    if (!parsed) throw new Error('Invalid encrypted attachment locator.');
    const response = await fetch(c.getHomeserverUrl() + '/_matrix/client/v1/media/download/' + encodeURIComponent(parsed[1]) + '/' + encodeURIComponent(parsed[2]), { headers: { Authorization: 'Bearer ' + c.getAccessToken() }, signal: options.signal });
    current();
    if (!response.ok) throw new Error('Encrypted attachment could not be downloaded.');
    const ciphertext = await boundedCiphertext(response, options.signal); current();
    const bytes = await decryptAttachment(ciphertext, actual.encrypted_file as IEncryptedFile); current();
    if (bytes.byteLength !== actual.size) throw new Error('Encrypted attachment size verification failed.');
    return new Blob([bytes], { type: actual.content_type });
  },
  async versions(file) { return (await index(file.channel_id)).files.filter(row => row.document_id === file.document_id).sort((a, b) => b.version_number! - a.version_number!); },
  async delete(file) {
    const { c, assertCurrent } = await secureMessaging.roomFor(file.channel_id, true), owner = accountId(c.getUserId()!);
    assertCurrent();
    const stored = (await index(file.channel_id)).files.find(row => row.id === file.id);
    assertCurrent();
    if (!stored || stored.document_owner_id !== owner) throw new Error('Only the document owner can remove this file.');
    await secureMessaging.sendEvent(file.channel_id, FILE_EVENT, { document_id: stored.document_id, owner_id: owner, deleted: true }, undefined, { authorize: assertCurrent });
    assertCurrent();
  },
};
