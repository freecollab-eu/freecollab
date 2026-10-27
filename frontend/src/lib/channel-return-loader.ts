import { api, type Channel } from './api';
import { getSecureMessaging } from './secure-client';
import { sharedDocumentsApi } from './shared-documents-api';
import { collaborationApi } from './collaboration-api';
import { EncryptedWorkClient } from './encrypted-work-api';
import { WORK_DOCUMENT_UPDATE, WORK_PROTOCOL, WORK_PROTOCOL_V2 } from './federated-work-model';
import { decodeWorkUpdate } from './federated-work-document';
import * as Y from 'yjs';
import { emptyReturnGroup, finishReturnGroup, returnEncryptedWork, returnMessageAttention, returnTimestamp,
  type ChannelReturnSnapshot, type ChannelReturnBaseline } from './channel-return-summary';

export interface ChannelReturnRequest {
  workspaceId: string; channel: Pick<Channel, 'id' | 'secure' | 'workspace_id'>;
  baseline: ChannelReturnBaseline; signal: AbortSignal; isCurrent: () => boolean;
}
export async function loadChannelReturnSummary(input: ChannelReturnRequest): Promise<ChannelReturnSnapshot> {
  const token = api.getAccessToken(), user = api.getCurrentUserId();
  const current = () => { if (!token || !user || token !== api.getAccessToken() || user !== api.getCurrentUserId() || input.signal.aborted || !input.isCurrent()) throw new Error('The conversation changed.'); };
  current();
  if (input.baseline.status !== 'known' || !input.baseline.readAt || !Number.isFinite(returnTimestamp(input.baseline.readAt))) throw new Error('The previous reading time is unavailable.');
  const since = returnTimestamp(input.baseline.readAt), channelId = input.channel.id;
  const channel = await api.getChannel(channelId); current();
  if (channel.id !== channelId || channel.workspace_id !== input.workspaceId || !!channel.secure !== !!input.channel.secure) throw new Error('The conversation changed.');
  if (!channel.secure) {
    const result = await api.request<ChannelReturnSnapshot>(`/read-receipts/channels/${encodeURIComponent(channelId)}/return-summary?since=${encodeURIComponent(input.baseline.readAt)}`, { signal: input.signal }); current();
    if (result.channel_id !== channelId || returnTimestamp(result.since) !== since) throw new Error('The return summary belongs to another reading position.');
    return result;
  }
  const secure = await getSecureMessaging(); current();
  if (!await secure.handles(channelId)) throw new Error('Unlock this encrypted conversation to see its return summary.');
  current();
  const work = new EncryptedWorkClient(channelId, { readOnly: true, eventLimit: 200 });
  const stop = () => work.stop(); input.signal.addEventListener('abort', stop, { once: true });
  try {
    const results = await Promise.allSettled([
      secure.getRecentMessages(channelId, { limit: 100, eventLimit: 200, signal: input.signal, isCurrent: input.isCurrent }),
      sharedDocumentsApi.encryptedSummaries(channelId, since, input.signal),
      work.inspect(),
      collaborationApi.encryptedDecisionSummaries(channelId, input.baseline.readAt, input.signal),
    ]); current();
    const groups = { decisions: emptyReturnGroup(), documents: emptyReturnGroup(), attention: emptyReturnGroup() };
    const [messages, documents, inspection, oldDecisions] = results;
    if (messages.status === 'fulfilled') groups.attention = returnMessageAttention(messages.value, channelId, user!, since);
    else groups.attention.incomplete = true;
    if (documents.status === 'fulfilled') {
      groups.documents.incomplete = documents.value.incomplete;
      groups.documents.items.push(...documents.value.items.map(row => ({ source: { kind: 'document' as const, id: row.id }, title: row.title || null, occurredAt: row.updated_at, reason: 'document' as const })));
    } else groups.documents.incomplete = true;
    if (oldDecisions.status === 'fulfilled') {
      groups.decisions.incomplete = oldDecisions.value.incomplete;
      groups.decisions.items.push(...oldDecisions.value.items.map(row => ({ source: { kind: 'decision' as const, id: row.id }, title: row.title || null, occurredAt: row.revised_at, reason: 'decision' as const })));
    } else groups.decisions.incomplete = true;
    if (inspection.status === 'fulfilled') {
      const { snapshot, events } = inspection.value;
      if (snapshot.context.channel_id !== channelId || snapshot.context.workspace_id !== input.workspaceId) throw new Error('The encrypted summary belongs to another workspace.');
      const verified = returnEncryptedWork(snapshot, since);
      for (const key of ['decisions', 'documents', 'attention'] as const) {
        groups[key].items.push(...verified[key].items); groups[key].incomplete ||= verified[key].incomplete;
      }
      // Count opaque edit activity without opening/reconstructing whole documents.
      // Inline Yjs updates are structurally checked; multipart bodies stay explicit gaps.
      for (const event of events) {
        if (event.type !== WORK_DOCUMENT_UPDATE || event.timestamp <= since) continue;
        const record = snapshot.legacyRecords.find(row => row.id === event.content.id && row.kind === 'document' && !row.invalid.length && !row.conflicts && !row.selected.archived);
        if (!record || event.content.root_event_id !== record.root_event_id || event.content.connection_id !== snapshot.context.connection_id
            || ![WORK_PROTOCOL, WORK_PROTOCOL_V2].includes(String(event.content.protocol))
            || Object.keys(event.content).some(key => !['protocol', 'connection_id', 'id', 'root_event_id', 'update_base64', 'parts', 'byte_size', 'state_hash'].includes(key))) { groups.documents.incomplete = true; continue; }
        try {
          if (event.content.parts || event.content.byte_size !== undefined || event.content.state_hash !== undefined) throw new Error('Multipart edit metadata needs the full reader.');
          Y.decodeUpdate(decodeWorkUpdate(event.content.update_base64, 16384));
          groups.documents.items.push({ source: { kind: 'shared-work', id: record.id, resourceKind: 'document' }, title: null,
            occurredAt: new Date(event.timestamp).toISOString(), reason: 'document' });
        } catch { groups.documents.incomplete = true; }
      }
    } else groups.decisions.incomplete = groups.documents.incomplete = groups.attention.incomplete = true;
    current();
    const latest = await api.getChannel(channelId); current();
    if (latest.id !== channelId || latest.workspace_id !== input.workspaceId || !latest.secure) throw new Error('The conversation changed.');
    // This is deliberately not an assertion that a bounded encrypted timeline is complete.
    return { channel_id: channelId, since: input.baseline.readAt, generated_at: new Date().toISOString(), secure: true, incomplete: true,
      groups: { decisions: finishReturnGroup(groups.decisions), documents: finishReturnGroup(groups.documents), attention: finishReturnGroup(groups.attention) } };
  } finally { input.signal.removeEventListener('abort', stop); work.stop(); }
}
