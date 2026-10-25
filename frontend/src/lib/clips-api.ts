import { validateFieldReferences, type FieldReferences } from './field-references';
import { validateResourceReferenceTargets } from './resource-reference-targets';
import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { uploadChannelFile, listChannelFiles, downloadChannelFile, type ManagedFile } from './files-api';
import { nativeRecordingProof, authorizeNativeRecording } from './meeting-capture-proof';
import { EncryptedWorkClient, loadEncryptedRoomContext, publishEncryptedRoomPointer } from './encrypted-work-api';
import { verifyEncryptedRoomEvents } from './encrypted-room-events';
import { getVerifiedSecureFileReference } from './secure/files';

export interface ClipCaption { start: number; end: number; text: string }
export interface ClipFields { field_references?: FieldReferences; title: string; duration: number; transcript?: string; captions?: ClipCaption[] }
export interface ChannelClip { id: string; channel_id: string; created_by: string; created_at: string; content_mode: 'server' | 'matrix'; matrix_event_id: string | null; fields?: ClipFields; file?: ManagedFile | null; resolution_error?: string; federated?: boolean; can_remove?: boolean }
export interface ClipPublicationOptions { publicationReceipt?: string; id?: string; captureId?: string; generation?: number;
  expectedContentMode?:'server'|'matrix';
  signal?: AbortSignal; beforePublish?: () => void | Promise<void>; meetingCaptureProof?: Record<string, unknown> }
const path = (channel: string) => '/channels/' + encodeURIComponent(channel) + '/clips';
const EVENT = 'eu.freecollab.clip';
const owned = (token: string | null) => { if (!token || token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };

async function federatedClip(channel: string, eventId: string): Promise<ChannelClip> {
  const token=api.getAccessToken(),actor=api.getCurrentUserId();
  const context = await loadEncryptedRoomContext(channel), [event] = await verifyEncryptedRoomEvents(channel,[eventId]), data = event.content;
  const fields = data.fields as ClipFields | undefined;
  if (event.type !== EVENT || data.protocol !== 'freecollab-clip/2' || data.room_id !== context.room_id || data.connection_id !== context.connection_id
      || typeof data.file_event_id !== 'string' || !data.file_event_id.startsWith('$') || !fields || typeof fields.title !== 'string' || fields.title.length > 200
      || !Number.isFinite(fields.duration) || fields.duration <= 0 || fields.duration > 300
      || fields.transcript !== undefined && (typeof fields.transcript !== 'string' || fields.transcript.length > 30000)
      || fields.captions !== undefined && (!Array.isArray(fields.captions) || fields.captions.length > 300 || fields.captions.some(row => !Number.isFinite(row.start) || !Number.isFinite(row.end)
        || row.start < 0 || row.end < row.start || row.end > fields.duration+1 || typeof row.text !== 'string' || row.text.length > 2000))) throw new Error('The encrypted peer clip metadata is invalid.');
  const [attachment] = await verifyEncryptedRoomEvents(channel,[data.file_event_id]);
  if (attachment.type !== 'eu.freecollab.file' || attachment.sender !== event.sender) throw new Error('The peer clip attachment author differs from its verified clip author.');
  const file = await getVerifiedSecureFileReference(channel,data.file_event_id);
  if (!file) throw new Error('The encrypted peer recording file is unavailable or its consent proof needs review.');
  const field_references = validateFieldReferences({title:fields.title,transcript:fields.transcript||''},fields.field_references);
  owned(token); if(actor!==api.getCurrentUserId())throw new Error('The signed-in clip account changed.');
  return {id:eventId,channel_id:channel,created_by:event.sender,created_at:new Date(event.timestamp).toISOString(),content_mode:'matrix',matrix_event_id:eventId,
    fields:{...fields,...(field_references?{field_references}:{})},file,federated:true,can_remove:event.sender===context.matrix_user_id};
}

async function resolve(row: ChannelClip): Promise<ChannelClip> {
  if (row.content_mode === 'server') return row;
  try {
    const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(row.channel_id, row.matrix_event_id!);
    const { c, roomId } = await secure.roomFor(row.channel_id), data = event.getContent();
    const domain = c.getUserId()!.split(':').slice(1).join(':');
    if (!event.isEncrypted() || event.isRedacted() || event.getRoomId() !== roomId || event.getType() !== EVENT
        || event.getSender() !== `@fc_${row.created_by}:${domain}` || data.id !== row.id || data.channel_id !== row.channel_id
        || typeof data.fields?.title !== 'string' || data.fields.title.length > 200 || typeof data.file_event_id !== 'string'
        || !Number.isFinite(data.fields.duration) || data.fields.duration <= 0 || data.fields.duration > 300
        || (data.fields.transcript !== undefined && (typeof data.fields.transcript !== 'string' || data.fields.transcript.length > 30000)))
      throw new Error('Encrypted clip metadata could not be verified.');
    const file = (await listChannelFiles(row.channel_id)).find(item => item.id === data.file_event_id);
    if (file && file.uploader_id !== row.created_by) throw new Error('The recording author could not be verified.');
    if (data.fields.captions !== undefined && (!Array.isArray(data.fields.captions) || data.fields.captions.length > 300
        || data.fields.captions.some((caption: ClipCaption) => !Number.isFinite(caption.start) || !Number.isFinite(caption.end)
          || caption.start < 0 || caption.end < caption.start || caption.end > data.fields.duration + 1
          || typeof caption.text !== 'string' || caption.text.length > 2000))) throw new Error('Encrypted captions could not be verified.');
    const field_references = validateFieldReferences({ title: data.fields.title, transcript: data.fields.transcript || "" }, data.fields.field_references);
    return { ...row, fields: { ...data.fields, field_references } as ClipFields, file: file || null };
  } catch (failure) { return { ...row, resolution_error: failure instanceof Error ? failure.message : 'Encrypted clip unavailable.' }; }
}

export const clipsApi = {
  async get(channel: string, id: string) {
    const secure = await getSecureMessaging();
    if (await secure.handles(channel) && (await loadEncryptedRoomContext(channel)).mode === 'federated') return federatedClip(channel,id);
    const owner = api.getAccessToken(), row = await api.request<ChannelClip>(path(channel) + '/' + encodeURIComponent(id));
    const result = await resolve(row); owned(owner); return result;
  },
  async list(channel: string) {
    const initialToken=api.getAccessToken(),secure = await getSecureMessaging();
    if (await secure.handles(channel) && (await loadEncryptedRoomContext(channel)).mode === 'federated') {
      const client = new EncryptedWorkClient(channel);
      try { const events = await client.verifiedEvents([EVENT]);
        const results = await Promise.allSettled(events.slice(-100).map(event=>federatedClip(channel,event.event_id)));
        owned(initialToken); return results.filter((row):row is PromiseFulfilledResult<ChannelClip>=>row.status==='fulfilled').map(row=>row.value).sort((a,b)=>b.created_at.localeCompare(a.created_at));
      } finally { client.stop(); }
    }
    const owner = api.getAccessToken(), rows = await api.request<ChannelClip[]>(path(channel));
    const result = await Promise.all(rows.map(resolve)); owned(owner); return result;
  },
  async create(channel: string, blob: Blob, fields: ClipFields, onProgress?: (percent: number) => void, options?: ClipPublicationOptions) {
    const owner = api.getAccessToken(); owned(owner);
    const id = options?.id || crypto.randomUUID(), secure = await getSecureMessaging(), encrypted = await secure.handles(channel); owned(owner);
    if(options?.expectedContentMode&&encrypted!==(options.expectedContentMode==='matrix'))throw new Error('Channel encryption changed since recording. The private recording was not uploaded.');
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(id)) throw new Error('Invalid recording identifier.');
    const field_references = validateFieldReferences({ title: fields.title, transcript: fields.transcript || "" }, fields.field_references);
    fields = { ...fields, ...(field_references ? { field_references } : {}) }; if (!field_references) delete fields.field_references;
    await validateResourceReferenceTargets(channel, { title: fields.title, transcript: fields.transcript || "" }, field_references, owner, encrypted); owned(owner);
    const mime = blob.type.split(';')[0], extension = mime.includes('mp4') ? 'mp4' : mime.includes('ogg') ? 'ogg' : 'webm';
    const recording = !!options?.publicationReceipt || !!options?.meetingCaptureProof;
    if (options?.publicationReceipt && (!options.captureId || !options.generation)) throw new Error('Recording publication needs its exact consent generation.');
    const beforePublish = async () => {
      owned(owner); if (options?.signal?.aborted) throw new DOMException('Recording publication cancelled.', 'AbortError');
      await options?.beforePublish?.(); owned(owner);
      if(options?.meetingCaptureProof){const {verifyMatrixRecordingProof}=await import('./matrix-meeting-capture');const approved=await verifyMatrixRecordingProof(channel,options.meetingCaptureProof),publishing=await secure.roomFor(channel);if(publishing.c.getDeviceId()!==approved.proof.recorder_device_id||publishing.c.getUserId()!==approved.host.sender)throw new Error('Publish this recording from its original verified recording device.');owned(owner);}
      if (options?.publicationReceipt) await authorizeNativeRecording(channel, options.captureId!, options.publicationReceipt, id);
      owned(owner); if (options?.signal?.aborted) throw new DOMException('Recording publication cancelled.', 'AbortError');
    };
    const proof = options?.meetingCaptureProof || (options?.publicationReceipt && encrypted ? await nativeRecordingProof(channel, options.captureId!, options.generation!, options.publicationReceipt, id) : undefined);
    if (proof && !encrypted) throw new Error('Encrypted recording consent cannot publish to a plaintext channel.');
    await beforePublish();
    const file = await uploadChannelFile(channel, new File([blob], 'clip-' + id + '.' + extension, { type: mime }), {
      requestId: recording && encrypted ? 'fc-capture-file-' + id : id, onProgress, signal: options?.signal, beforePublish,
      ...(options?.publicationReceipt ? { meetingCapture: { receiptId: options.publicationReceipt, clipId: id } } : {}),
      ...(proof ? { meetingCaptureProof: { ...proof } } : {}),
    }); owned(owner);
    if (file.encrypted !== encrypted || await secure.handles(channel) !== encrypted) throw new Error('Channel encryption changed. Reload before publishing this clip.');
    owned(owner);
    let body: Record<string, unknown> = { id, file_id: file.id, ...fields };
    if (encrypted) {
      const context = await loadEncryptedRoomContext(channel);
      const event = await secure.sendEvent(channel, EVENT, { id, channel_id: channel, fields, file_event_id: file.id,
        protocol:'freecollab-clip/2',room_id:context.room_id,connection_id:context.connection_id,
        ...(proof ? { meeting_capture: proof } : {}) }, recording ? 'fc-capture-clip-' + id : id,
        { authorize: beforePublish, signal: options?.signal }); owned(owner);
      if (context.mode === 'federated') { await publishEncryptedRoomPointer(channel,event.event_id); owned(owner); return federatedClip(channel,event.event_id); }
      body = { id, matrix_event_id: event.event_id };
    }
    await beforePublish();
    if (options?.publicationReceipt) body.publication_receipt = options.publicationReceipt;
    const row = await api.request<ChannelClip>(path(channel), { method: 'POST', body: JSON.stringify(body) }); owned(owner); return resolve(row);
  },
  async remove(row: ChannelClip) {
    if (row.federated) { const context=await loadEncryptedRoomContext(row.channel_id); if(context.matrix_user_id!==row.created_by)throw new Error('Only the verified clip author can remove this entry.');
      const secure=await getSecureMessaging(),{c,roomId}=await secure.roomFor(row.channel_id); await c.redactEvent(roomId,row.matrix_event_id!); return; }
    return api.request(path(row.channel_id) + '/' + encodeURIComponent(row.id), { method: 'DELETE' });
  },
  download: (row: ChannelClip) => { if (!row.file) throw new Error('The recording file is no longer available.'); return downloadChannelFile(row.file); },
};

const stamp = (seconds: number) => new Date(Math.round(seconds * 1000)).toISOString().slice(11, 23);
export function clipVtt(captions: ClipCaption[]) {
  return 'WEBVTT\n\n' + captions.map((row, index) => `${index + 1}\n${stamp(row.start)} --> ${stamp(row.end)}\n${row.text.replace(/-->/g, '→').replace(/[<>]/g, '')}\n`).join('\n');
}
