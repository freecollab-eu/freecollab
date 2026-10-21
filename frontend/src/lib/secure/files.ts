import { encryptAttachment, decryptAttachment, type IEncryptedFile } from 'matrix-encrypt-attachment';
import type { ChannelFileProvider, ManagedFile } from '../files-api';
import type { MatrixEvent } from 'matrix-js-sdk';
import { secureMessaging } from './index';
import { FILE_EXPERIENCE_TYPES } from '../file-preview';

const FILE_EVENT = 'eu.freecollab.file';
const MAX_SIZE = 50 * 1024 * 1024;
const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf', 'text/plain', 'application/json', ...FILE_EXPERIENCE_TYPES, 'application/vnd.oasis.opendocument.text', 'application/vnd.oasis.opendocument.spreadsheet', 'application/vnd.oasis.opendocument.presentation']);
interface Receipt { file: ManagedFile; fingerprint: string }
interface FileIndex { files: ManagedFile[]; receipts: Map<string, Receipt>; deleted: Set<string> }
function check(signal?: AbortSignal) { if (signal?.aborted) throw new DOMException('Transfer cancelled.', 'AbortError'); }
const accountId = (sender: string) => /^@fc_([^:]+):/.exec(sender)?.[1];
const domainOf = (sender: string) => sender.slice(sender.indexOf(':') + 1);

function authenticatedOwner(event: MatrixEvent, domain: string): string | null {
  const owner = event.getContent().owner_id;
  return typeof owner === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(owner) && event.getSender() === `@fc_${owner}:${domain}` ? owner : null;
}

function asFile(channelId: string, event: MatrixEvent, domain: string): ManagedFile | null {
  const content = event.getContent(), owner = authenticatedOwner(event, domain);
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
  const { c, room, roomId } = await secureMessaging.roomFor(channelId);
  const domain = domainOf(c.getUserId()!);
  // Metadata remains in encrypted room history. Do not create a server-side name index.
  while (room.oldState.paginationToken) {
    check(signal); const token = room.oldState.paginationToken;
    await c.scrollback(room, 100);
    if (token === room.oldState.paginationToken) break;
  }
  check(signal);
  const events = await secureMessaging.events(channelId), files: ManagedFile[] = [], receipts = new Map<string, Receipt>();
  const documents = new Map<string, ManagedFile>(), deleted = new Set<string>();
  for (const event of events) {
    if (event.getType() !== FILE_EVENT || event.getRoomId() !== roomId || !event.isEncrypted() || event.isRedacted() || !event.getId()?.startsWith('$')) continue;
    const content = event.getContent(), owner = authenticatedOwner(event, domain);
    if (!owner) continue;
    if (content.deleted === true) {
      if (typeof content.document_id === 'string' && documents.get(content.document_id)?.document_owner_id === owner) deleted.add(content.document_id);
      continue;
    }
    const file = asFile(channelId, event, domain);
    if (!file || deleted.has(file.document_id!)) continue;
    const previous = documents.get(file.document_id!);
    if (previous ? previous.document_owner_id !== owner || file.version_number !== previous.version_number! + 1 : file.version_number !== 1) continue;
    documents.set(file.document_id!, file); files.push(file);
    if (typeof content.request_id === 'string' && typeof content.fingerprint === 'string') receipts.set(`${owner}:${content.request_id}`, { file, fingerprint: content.fingerprint });
  }
  for (const file of files) file.version_count = documents.get(file.document_id!)!.version_number;
  return { files: files.filter(file => !deleted.has(file.document_id!)), receipts, deleted };
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

export const secureFileProvider: ChannelFileProvider = {
  handles: channelId => secureMessaging.handles(channelId),
  async list(channelId) {
    const { files } = await index(channelId), latest = new Map<string, ManagedFile>();
    for (const file of files) latest.set(file.document_id!, file);
    return [...latest.values()].reverse();
  },
  async upload(channelId, file, options) {
    check(options.signal);
    if (file.size > MAX_SIZE) throw new Error('Maximum file size is 50 MB.');
    if (!allowedTypes.has(file.type)) throw new Error('This file type is not supported.');
    const { c, roomId } = await secureMessaging.roomFor(channelId, true), owner = accountId(c.getUserId()!);
    if (!owner) throw new Error('Encrypted file identity is unavailable.');
    const bytes = await file.arrayBuffer(), hash = await fingerprint(channelId, options.documentId, file, bytes);
    check(options.signal);
    const requestId = options.requestId || crypto.randomUUID(), state = await index(channelId, options.signal);
    const receipt = state.receipts.get(`${owner}:${requestId}`);
    if (receipt) {
      if (receipt.fingerprint !== hash) throw new Error('This upload request was already used for another file.');
      if (state.deleted.has(receipt.file.document_id!)) throw new Error('The previous upload is no longer available.');
      options.onProgress?.(100); return receipt.file;
    }
    const versions = options.documentId ? state.files.filter(row => row.document_id === options.documentId) : [];
    if (options.documentId && (!versions.length || versions.some(row => row.document_owner_id !== owner))) throw new Error('Only the document owner can upload a new version.');
    options.onProgress?.(5);
    const encrypted = await encryptAttachment(bytes); check(options.signal); options.onProgress?.(35);
    const controller = new AbortController(), abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    check(options.signal);
    let uploaded: { content_uri: string };
    try {
      // The homeserver receives anonymous ciphertext, never filename or media type.
      uploaded = await c.uploadContent(new Blob([encrypted.data], { type: 'application/octet-stream' }), {
        type: 'application/octet-stream', includeFilename: false, abortController: controller,
      });
    } finally { options.signal?.removeEventListener('abort', abort); }
    check(options.signal); options.onProgress?.(85);
    const documentId = options.documentId || `${owner}:${requestId}`, version = versions.reduce((max, row) => Math.max(max, row.version_number || 1), 0) + 1;
    const metadata = { document_id: documentId, owner_id: owner, version_number: version, request_id: requestId, fingerprint: hash,
      name: file.name, content_type: file.type, size: file.size, file: { ...encrypted.info, url: uploaded.content_uri } };
    const result = await secureMessaging.sendEvent(channelId, FILE_EVENT, metadata, `file-${requestId}`);
    check(options.signal);
    // A retried Matrix transaction may return its original event. Acknowledge
    // only that authenticated event, rather than claiming the new bytes arrived.
    const event = await secureMessaging.getDecryptedEvent(channelId, result.event_id), actual = asFile(channelId, event, domainOf(c.getUserId()!));
    if (!actual || event.getRoomId() !== roomId || event.getContent().fingerprint !== hash || event.getContent().request_id !== requestId) throw new Error('The encrypted upload confirmation could not be verified. Refresh and retry safely.');
    const confirmed = (await index(channelId, options.signal)).files.find(row => row.id === actual.id);
    if (!confirmed) throw new Error('Another version completed first, or this document was deleted. Refresh and choose the file again.');
    check(options.signal); options.onProgress?.(100); return confirmed;
  },
  async download(file, options = {}) {
    check(options.signal);
    const state = await index(file.channel_id, options.signal), stored = state.files.find(row => row.id === file.id);
    if (!stored) throw new Error('This encrypted file is no longer available.');
    const { c, roomId } = await secureMessaging.roomFor(file.channel_id);
    // Re-read the authenticated room event; injected locators and keys are ignored.
    const event = await secureMessaging.getDecryptedEvent(file.channel_id, stored.id), actual = asFile(file.channel_id, event, domainOf(c.getUserId()!));
    if (!actual?.secure_url || event.getRoomId() !== roomId) throw new Error('Encrypted attachment metadata is unavailable.');
    const parsed = /^mxc:\/\/([^/]+)\/([^/?#]+)$/.exec(actual.secure_url);
    if (!parsed) throw new Error('Invalid encrypted attachment locator.');
    const response = await fetch(c.getHomeserverUrl() + '/_matrix/client/v1/media/download/' + encodeURIComponent(parsed[1]) + '/' + encodeURIComponent(parsed[2]), { headers: { Authorization: 'Bearer ' + c.getAccessToken() }, signal: options.signal });
    if (!response.ok) throw new Error('Encrypted attachment could not be downloaded.');
    const bytes = await decryptAttachment(await boundedCiphertext(response, options.signal), actual.encrypted_file as IEncryptedFile);
    check(options.signal);
    if (bytes.byteLength !== actual.size) throw new Error('Encrypted attachment size verification failed.');
    return new Blob([bytes], { type: actual.content_type });
  },
  async versions(file) { return (await index(file.channel_id)).files.filter(row => row.document_id === file.document_id).sort((a, b) => b.version_number! - a.version_number!); },
  async delete(file) {
    const { c } = await secureMessaging.roomFor(file.channel_id, true), owner = accountId(c.getUserId()!);
    const stored = (await index(file.channel_id)).files.find(row => row.id === file.id);
    if (!stored || stored.document_owner_id !== owner) throw new Error('Only the document owner can remove this file.');
    await secureMessaging.sendEvent(file.channel_id, FILE_EVENT, { document_id: stored.document_id, owner_id: owner, deleted: true });
  },
};
