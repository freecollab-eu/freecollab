import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { uploadChannelFile, listChannelFiles, downloadChannelFile, type ManagedFile } from './files-api';

export interface ClipCaption { start: number; end: number; text: string }
export interface ClipFields { title: string; duration: number; transcript?: string; captions?: ClipCaption[] }
export interface ChannelClip { id: string; channel_id: string; created_by: string; created_at: string; content_mode: 'server' | 'matrix'; matrix_event_id: string | null; fields?: ClipFields; file?: ManagedFile | null; resolution_error?: string }
const path = (channel: string) => '/channels/' + encodeURIComponent(channel) + '/clips';
const EVENT = 'eu.freecollab.clip';
const owned = (token: string | null) => { if (!token || token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };

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
    return { ...row, fields: data.fields as ClipFields, file: file || null };
  } catch (failure) { return { ...row, resolution_error: failure instanceof Error ? failure.message : 'Encrypted clip unavailable.' }; }
}

export const clipsApi = {
  async list(channel: string) {
    const owner = api.getAccessToken(), rows = await api.request<ChannelClip[]>(path(channel));
    const result = await Promise.all(rows.map(resolve)); owned(owner); return result;
  },
  async create(channel: string, blob: Blob, fields: ClipFields, onProgress?: (percent: number) => void) {
    const owner = api.getAccessToken(); owned(owner);
    const id = crypto.randomUUID(), secure = await getSecureMessaging(), encrypted = await secure.handles(channel); owned(owner);
    const mime = blob.type.split(';')[0], extension = mime.includes('mp4') ? 'mp4' : mime.includes('ogg') ? 'ogg' : 'webm';
    const file = await uploadChannelFile(channel, new File([blob], 'clip-' + id + '.' + extension, { type: mime }), { requestId: id, onProgress }); owned(owner);
    if (file.encrypted !== encrypted || await secure.handles(channel) !== encrypted) throw new Error('Channel encryption changed. Reload before publishing this clip.');
    owned(owner);
    let body: Record<string, unknown> = { id, file_id: file.id, ...fields };
    if (encrypted) {
      const event = await secure.sendEvent(channel, EVENT, { id, channel_id: channel, fields, file_event_id: file.id }, id); owned(owner);
      body = { id, matrix_event_id: event.event_id };
    }
    const row = await api.request<ChannelClip>(path(channel), { method: 'POST', body: JSON.stringify(body) }); owned(owner); return resolve(row);
  },
  remove: (row: ChannelClip) => api.request(path(row.channel_id) + '/' + encodeURIComponent(row.id), { method: 'DELETE' }),
  download: (row: ChannelClip) => { if (!row.file) throw new Error('The recording file is no longer available.'); return downloadChannelFile(row.file); },
};

const stamp = (seconds: number) => new Date(Math.round(seconds * 1000)).toISOString().slice(11, 23);
export function clipVtt(captions: ClipCaption[]) {
  return 'WEBVTT\n\n' + captions.map((row, index) => `${index + 1}\n${stamp(row.start)} --> ${stamp(row.end)}\n${row.text.replace(/-->/g, '→').replace(/[<>]/g, '')}\n`).join('\n');
}
