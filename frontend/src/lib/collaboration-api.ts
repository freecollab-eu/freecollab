import { api, type Message } from './api';
import { getSecureMessaging } from './secure-client';

export type CollaborationTab = 'pins' | 'bookmarks' | 'decisions' | 'notifications';
export interface ChannelNotificationSettings {
  level: 'all' | 'mentions' | 'muted';
  email_digest: 'none' | 'daily' | 'weekly';
}
export interface SavedItem {
  channel_id: string;
  item_id: string;
  kind: 'message' | 'matrix';
  created_at: string;
  created_by?: string;
  available: boolean;
  message_id: string | null;
  matrix_event_id: string | null;
  message?: Message;
  resolution_error?: string;
  completed_at?: string | null;
}
export interface DecisionFields {
  title: string;
  content: string;
  status: 'proposed' | 'accepted' | 'superseded';
  responsible_user_id: string | null;
  decision_date: string | null;
  source_message_id: string | null;
}
export interface Decision extends Partial<DecisionFields> {
  id: string;
  channel_id: string;
  created_by: string;
  created_at: string;
  revision: number;
  revision_id: string;
  revised_by: string;
  revised_at: string;
  content_mode: 'server' | 'matrix';
  matrix_event_id: string | null;
  resolution_error?: string;
  /** Native-only derived routing metadata; encrypted events retain their verified path. */
  discussion_message_id?: string | null;
}

const segment = encodeURIComponent;
const decisionType = 'eu.freecollab.decision';
const secure = getSecureMessaging;
const accountId = (sender: string) => sender.startsWith('@fc_') ? sender.slice(4).split(':')[0] : sender;
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'This encrypted item could not be opened.';
type SavedState = { bookmarks: Set<string>; pins: Set<string> };
const savedStateLoads = new Map<string, Promise<SavedState>>();
const savedStateKey = (channelId: string) => `${api.getCurrentUserId()}:${channelId}`;
const invalidateSavedState = (channelId: string) => savedStateLoads.delete(savedStateKey(channelId));
window.addEventListener('freecollab:auth-changed', () => savedStateLoads.clear());

async function target(channelId: string, messageId: string) {
  return await (await secure()).handles(channelId) ? { matrix_event_id: messageId } : { message_id: messageId };
}

async function resolveSaved(item: SavedItem): Promise<SavedItem> {
  if (!item.matrix_event_id) return item;
  try {
    const client = await secure();
    const event = await client.getDecryptedEvent(item.channel_id, item.matrix_event_id);
    const { roomId } = await client.roomFor(item.channel_id);
    if (event.getRoomId() !== roomId || event.getType() !== 'm.room.message' || event.isRedacted()) throw new Error('This saved message is unavailable.');
    const content = event.getContent();
    if (typeof content.body !== 'string' || content['m.relates_to']?.rel_type === 'm.replace') throw new Error('This reference is not a message.');
    const recent = (await client.getMessages(item.channel_id)).find(message => message.id === item.item_id);
    return { ...item, available: true, message: recent || {
      id: item.item_id, channel_id: item.channel_id, author_id: accountId(event.getSender() || ''),
      content: content.body, encrypted: true, created_at: new Date(event.getTs()).toISOString(),
      parent_id: content['m.relates_to']?.rel_type === 'm.thread' ? content['m.relates_to'].event_id : undefined,
    } };
  } catch (error) { return { ...item, available: false, resolution_error: errorMessage(error) }; }
}

async function resolveDecision(record: Decision): Promise<Decision> {
  if (record.content_mode !== 'matrix' || !record.matrix_event_id) return record;
  try {
    const client = await secure();
    const event = await client.getDecryptedEvent(record.channel_id, record.matrix_event_id);
    const { roomId } = await client.roomFor(record.channel_id);
    const content = event.getContent();
    if (event.getRoomId() !== roomId || event.getType() !== decisionType || event.isRedacted()
      || accountId(event.getSender() || '') !== record.revised_by
      || content.revision !== record.revision || (record.revision > 1 && content.decision_id !== record.id)
      || typeof content.title !== 'string' || !content.title.trim() || content.title.length > 200
      || typeof content.content !== 'string' || !content.content.trim() || content.content.length > 20000
      || !['proposed', 'accepted', 'superseded'].includes(content.status)) throw new Error('This encrypted decision could not be verified.');
    return { ...record, title: content.title, content: content.content, status: content.status,
      responsible_user_id: typeof content.responsible_user_id === 'string' ? content.responsible_user_id : null,
      decision_date: typeof content.decision_date === 'string' ? content.decision_date : null,
      source_message_id: typeof content.source_message_id === 'string' ? content.source_message_id : null };
  } catch (error) { return { ...record, resolution_error: errorMessage(error) }; }
}

async function decisionPayload(channelId: string, fields: DecisionFields, previous?: Decision) {
  if (!await (await secure()).handles(channelId)) return fields;
  const event = await (await secure()).sendEvent(channelId, decisionType, {
    ...fields, revision: previous ? previous.revision + 1 : 1,
    ...(previous ? { decision_id: previous.id, previous_event_id: previous.matrix_event_id } : {}),
  });
  return { matrix_event_id: event.event_id };
}

async function subscriptionPath(channelId: string, messageId: string) {
  return await (await secure()).handles(channelId)
    ? `/threads/channels/${segment(channelId)}/events/${segment(messageId)}/subscription`
    : `/threads/messages/${segment(messageId)}/subscription`;
}

export const collaborationApi = {
  savedState(channelId: string): Promise<SavedState> {
    const key = savedStateKey(channelId);
    let pending = savedStateLoads.get(key);
    if (!pending) {
      pending = Promise.all([api.request<SavedItem[]>('/bookmarks'), api.request<SavedItem[]>(`/channels/${segment(channelId)}/pins`)])
        .then(([bookmarks, pins]) => ({ bookmarks: new Set(bookmarks.filter(item => item.channel_id === channelId).map(item => item.item_id)), pins: new Set(pins.map(item => item.item_id)) }))
        .catch(error => { savedStateLoads.delete(key); throw error; });
      savedStateLoads.set(key, pending);
    }
    return pending;
  },
  async pins(channelId: string) {
    const records = await api.request<SavedItem[]>(`/channels/${segment(channelId)}/pins`);
    return Promise.all(records.map(resolveSaved));
  },
  async pin(channelId: string, messageId: string) {
    const record = await api.request<SavedItem>(`/channels/${segment(channelId)}/pins`, { method: 'POST', body: JSON.stringify(await target(channelId, messageId)) });
    invalidateSavedState(channelId); return record;
  },
  async unpin(channelId: string, itemId: string) {
    await api.request(`/channels/${segment(channelId)}/pins/${segment(itemId)}`, { method: 'DELETE' });
    invalidateSavedState(channelId);
  },
  async bookmarks() {
    const records = await api.request<SavedItem[]>('/bookmarks');
    return Promise.all(records.map(resolveSaved));
  },
  async bookmark(channelId: string, messageId: string) {
    const record = await api.request<SavedItem>('/bookmarks', { method: 'POST', body: JSON.stringify({ channel_id: channelId, ...await target(channelId, messageId) }) });
    invalidateSavedState(channelId); return record;
  },
  async removeBookmark(channelId: string, itemId: string) {
    await api.request(`/bookmarks/${segment(channelId)}/${segment(itemId)}`, { method: 'DELETE' });
    invalidateSavedState(channelId);
  },
  async decisions(channelId: string) {
    const records = await api.request<Decision[]>(`/channels/${segment(channelId)}/decisions`);
    return Promise.all(records.map(resolveDecision));
  },
  async revisions(decisionId: string) {
    const records = await api.request<Decision[]>(`/decisions/${segment(decisionId)}/revisions`);
    return Promise.all(records.map(resolveDecision));
  },
  async createDecision(channelId: string, fields: DecisionFields) {
    const payload = await decisionPayload(channelId, fields);
    return resolveDecision(await api.request<Decision>(`/channels/${segment(channelId)}/decisions`, { method: 'POST', body: JSON.stringify(payload) }));
  },
  async reviseDecision(previous: Decision, fields: DecisionFields) {
    const payload = await decisionPayload(previous.channel_id, fields, previous);
    return resolveDecision(await api.request<Decision>(`/decisions/${segment(previous.id)}/revisions`, { method: 'POST', body: JSON.stringify({ ...payload, expected_revision: previous.revision }) }));
  },
  settings(channelId: string) { return api.request<ChannelNotificationSettings>(`/notifications/channels/${segment(channelId)}/settings`); },
  setSettings(channelId: string, value: ChannelNotificationSettings) { return api.request<ChannelNotificationSettings>(`/notifications/channels/${segment(channelId)}/settings`, { method: 'PUT', body: JSON.stringify(value) }); },
  async threadWatch(channelId: string, messageId: string) { return api.request<{ watching: boolean }>(await subscriptionPath(channelId, messageId)); },
  async setThreadWatch(channelId: string, messageId: string, watching: boolean) { return api.request<{ watching: boolean }>(await subscriptionPath(channelId, messageId), { method: 'PUT', body: JSON.stringify({ watching }) }); },
};
