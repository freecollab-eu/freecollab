import { api } from './api';

export type ConversationWorkResourceKind = 'document' | 'task' | 'decision';
export interface ConversationWorkReference {
  id: string;
  channel_id: string;
  source_message_id: string;
  resource_kind: ConversationWorkResourceKind;
  resource_id: string;
  project_id: string | null;
  created_by: string;
  created_at: string;
}
export interface ConversationWorkInput {
  source_message_id: string;
  resource_kind: ConversationWorkResourceKind;
  resource_id: string;
}
export interface ConversationWorkPage { items: ConversationWorkReference[]; next_offset: number | null }
export interface ConversationWorkListOptions { sourceMessageId?: string; limit?: number; offset?: number; signal?: AbortSignal }
const base = (channelId: string) => `/channels/${encodeURIComponent(channelId)}/conversation-work`;
const owner = () => ({ token: api.getAccessToken(), actor: api.getCurrentUserId() });
function current(request: ReturnType<typeof owner>) {
  if (!request.token || request.token !== api.getAccessToken() || request.actor !== api.getCurrentUserId())
    throw new Error('The signed-in account changed.');
}
function verify(reference: ConversationWorkReference, channelId: string) {
  if (reference.channel_id !== channelId || !reference.id || !reference.source_message_id || !reference.resource_id
      || !['document', 'task', 'decision'].includes(reference.resource_kind)
      || (reference.resource_kind === 'task' ? !reference.project_id : reference.project_id !== null))
    throw new Error('This conversation reference is unavailable.');
  return reference;
}

/** Persistent routing IDs only. Existing resource APIs remain the source of content and state. */
export const conversationWorkApi = {
  async list(channelId: string, options: ConversationWorkListOptions = {}): Promise<ConversationWorkPage> {
    const request = owner(); current(request);
    const query = new URLSearchParams();
    if (options.sourceMessageId) query.set('source_message_id', options.sourceMessageId);
    if (options.limit !== undefined) query.set('limit', String(options.limit));
    if (options.offset !== undefined) query.set('offset', String(options.offset));
    const result = await api.request<ConversationWorkPage>(base(channelId) + (query.size ? `?${query}` : ''), { signal: options.signal });
    current(request);
    return { items: result.items.map(row => {
      const verified = verify(row, channelId);
      if (options.sourceMessageId && verified.source_message_id !== options.sourceMessageId)
        throw new Error('This conversation reference is unavailable.');
      return verified;
    }), next_offset: result.next_offset };
  },
  async create(channelId: string, input: ConversationWorkInput): Promise<ConversationWorkReference> {
    const request = owner(); current(request);
    const result = await api.request<ConversationWorkReference>(base(channelId), { method: 'POST', body: JSON.stringify(input) });
    current(request);
    const verified = verify(result, channelId);
    if (verified.source_message_id !== input.source_message_id || verified.resource_kind !== input.resource_kind || verified.resource_id !== input.resource_id)
      throw new Error('This conversation reference is unavailable.');
    return verified;
  },
  async remove(channelId: string, referenceId: string): Promise<void> {
    const request = owner(); current(request);
    await api.request<void>(`${base(channelId)}/${encodeURIComponent(referenceId)}`, { method: 'DELETE' });
    current(request);
  },
};
