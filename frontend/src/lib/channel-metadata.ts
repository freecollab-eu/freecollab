/** Shared bounds for the real channel-details form and verified encrypted labels. */
export interface ChannelMetadataRecord {
  channel_id: string;
  version: number;
  secure: boolean;
  authoritative: boolean;
  name: string | null;
  description: string | null;
  matrix_event_id: string | null;
  updated_at: string | null;
  updated_by: string | null;
  can_manage: boolean;
  management: 'channel' | 'conversation' | 'connection';
}

export interface ChannelMetadata extends Omit<ChannelMetadataRecord, 'name' | 'description'> {
  name: string;
  description: string;
}

const controls = /[\u0000-\u001f\u007f-\u009f]/;
const descriptionControls = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/;
const invalidUnicode = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
export function channelMetadataFields(name: unknown, description: unknown): { name: string; description: string } {
  if (typeof name !== 'string' || !name.trim() || name.length > 100 || controls.test(name) || invalidUnicode.test(name)) throw new Error('Use a channel name of 1–100 characters without control characters.');
  if (typeof description !== 'string' || description.length > 1000 || invalidUnicode.test(description) || descriptionControls.test(description.replace(/\r\n?/g, '\n'))) throw new Error('Use a channel description of at most 1,000 characters.');
  return { name: name.trim(), description: description.replace(/\r\n?/g, '\n').trim() };
}

export function nativeChannelMetadata(record: ChannelMetadataRecord): ChannelMetadata {
  if (record.secure || record.matrix_event_id) throw new Error('Encrypted channel details require verified decryption.');
  const fields = record.name === null && record.version === 0 ? { name: '', description: '' } : channelMetadataFields(record.name, record.description);
  return { ...record, ...fields };
}

export interface ChannelMetadataEvent {
  id: string | undefined;
  roomId: string | undefined;
  sender: string | undefined;
  type: string;
  encrypted: boolean;
  redacted: boolean;
  failed: boolean;
  content: Record<string, unknown>;
}

/** Registration is the authority: an uploaded but uncommitted event is unusable. */
export function verifiedChannelMetadata(record: ChannelMetadataRecord, roomId: string, ownMatrixId: string, event: ChannelMetadataEvent): ChannelMetadata {
  const domain = ownMatrixId.split(':').slice(1).join(':');
  if (!record.secure || !record.authoritative || !record.matrix_event_id || !record.updated_by || !domain || !Number.isSafeInteger(record.version) || record.version < 1
      || !event.encrypted || event.redacted || event.failed || event.id !== record.matrix_event_id || event.roomId !== roomId
      || event.type !== 'eu.freecollab.channel.metadata' || event.sender !== '@fc_' + record.updated_by + ':' + domain
      || event.content.registration_required !== true || event.content.channel_id !== record.channel_id || event.content.version !== record.version) {
    throw new Error('Encrypted channel details could not be verified.');
  }
  return { ...record, ...channelMetadataFields(event.content.name, event.content.description) };
}

/** Compatibility applies only to the original name-only, unregistered format. */
export function legacyChannelName(content: Record<string, unknown>): string | null {
  if (Object.keys(content).length !== 1 || !Object.prototype.hasOwnProperty.call(content, 'name')) return null;
  try { return channelMetadataFields(content.name, '').name; } catch { return null; }
}

/** A slower snapshot or legacy event must never replace a committed revision. */
export function applyChannelMetadata<T extends { id: string; secure?: boolean; metadata_version?: number }>(channel: T, metadata: ChannelMetadata): T & { name?: string; description?: string } {
  if (channel.id !== metadata.channel_id || !!channel.secure !== metadata.secure || (channel.metadata_version ?? 0) > metadata.version) return channel;
  return { ...channel, name: metadata.name, description: metadata.description, metadata_version: metadata.version };
}

/** Channel lists have no revision; retain confirmed labels until a fresh metadata read. */
export function retainChannelMetadata<T extends { id: string; name?: string | null; description?: string; secure?: boolean; metadata_version?: number }>(snapshot: T, known?: T): T {
  if (!known || known.id !== snapshot.id || !!known.secure !== !!snapshot.secure || known.metadata_version === undefined || (!known.secure && known.metadata_version === 0)) return snapshot;
  return { ...snapshot, name: known.name, description: known.description, metadata_version: known.metadata_version };
}
