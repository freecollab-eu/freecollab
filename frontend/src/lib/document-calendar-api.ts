import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { validateCalendar, type CalendarFields } from './calendar';
import { validateDocument, type DocumentFields } from './document-links';

export type ContentKind = 'documents' | 'calendar';
export interface ContentRecord<T> {
  id: string; channel_id: string; kind: ContentKind; created_by: string; created_at: string;
  revision: number; revised_by: string; revised_at: string; deleted: boolean;
  content_mode: 'server' | 'matrix'; matrix_event_id: string | null; fields?: T; resolution_error?: string;
}
export type DocumentRecord = ContentRecord<DocumentFields>;
export type CalendarRecord = ContentRecord<CalendarFields>;
const segment = encodeURIComponent;
const eventType = (kind: ContentKind) => 'eu.freecollab.integration.' + kind;
const validator = (kind: ContentKind) => kind === 'documents' ? validateDocument : validateCalendar;
const path = (channelId: string, kind: ContentKind) => `/channels/${segment(channelId)}/integrations/${kind}`;

async function resolve<T>(record: ContentRecord<T>): Promise<ContentRecord<T>> {
  try {
    if (record.content_mode === 'server') return { ...record, fields: record.deleted ? undefined : validator(record.kind)(record.fields) as T };
    if (!record.matrix_event_id) throw new Error('Encrypted integration reference is unavailable.');
    const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(record.channel_id, record.matrix_event_id);
    const { c, roomId } = await secure.roomFor(record.channel_id);
    const domain = c.getUserId()!.split(':').slice(1).join(':');
    const content = event.getContent();
    if (event.getRoomId() !== roomId || event.getType() !== eventType(record.kind) || event.isRedacted()
      || event.getSender() !== `@fc_${record.revised_by}:${domain}` || content.id !== record.id
      || content.revision !== record.revision || content.deleted !== record.deleted || content.kind !== record.kind)
      throw new Error('This encrypted integration revision could not be verified.');
    return { ...record, fields: record.deleted ? undefined : validator(record.kind)(content.fields) as T };
  } catch (error) { return { ...record, fields: undefined, resolution_error: error instanceof Error ? error.message : 'This encrypted item is unavailable.' }; }
}
async function list<T>(channelId: string, kind: ContentKind): Promise<ContentRecord<T>[]> {
  const token = api.getAccessToken();
  const records = await api.request<ContentRecord<T>[]>(path(channelId, kind));
  const result = await Promise.all(records.map(resolve<T>));
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  return result;
}
async function write<T>(channelId: string, kind: ContentKind, fields: T | null, previous?: ContentRecord<T>, draftId?: string) {
  const token = api.getAccessToken();
  const current = () => { if (!token || token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
  const id = previous?.id || draftId || crypto.randomUUID();
  const deleted = fields === null, revision = (previous?.revision || 0) + 1;
  const normalized = fields === null ? null : validator(kind)(fields);
  const secure = await getSecureMessaging();
  current();
  const encrypted = await secure.handles(channelId);
  current();
  let body: Record<string, unknown> = { id, expected_revision: previous?.revision || 0, deleted, ...(deleted ? {} : { fields: normalized }) };
  if (encrypted) {
    const event = await secure.sendEvent(channelId, eventType(kind), { id, kind, revision, deleted,
      ...(deleted ? {} : { fields: normalized }), ...(previous ? { previous_event_id: previous.matrix_event_id } : {}) });
    body = { id, expected_revision: previous?.revision || 0, deleted, matrix_event_id: event.event_id };
  }
  current();
  const result = await resolve(await api.request<ContentRecord<T>>(path(channelId, kind), { method: 'POST', body: JSON.stringify(body) }));
  current(); return result;
}
export const documentCalendarApi = {
  documents: (channelId: string) => list<DocumentFields>(channelId, 'documents'),
  calendar: (channelId: string) => list<CalendarFields>(channelId, 'calendar'),
  saveDocument: (channelId: string, fields: DocumentFields, previous?: DocumentRecord, draftId?: string) => write(channelId, 'documents', fields, previous, draftId),
  saveEvent: (channelId: string, fields: CalendarFields, previous?: CalendarRecord, draftId?: string) => write(channelId, 'calendar', fields, previous, draftId),
  removeDocument: (channelId: string, previous: DocumentRecord) => write<DocumentFields>(channelId, 'documents', null, previous),
  removeEvent: (channelId: string, previous: CalendarRecord) => write<CalendarFields>(channelId, 'calendar', null, previous),
  async history<T>(channelId: string, kind: ContentKind, id: string) {
    const rows = await api.request<ContentRecord<T>[]>(path(channelId, kind) + '/' + segment(id) + '/revisions');
    return Promise.all(rows.map(resolve<T>));
  },
};
