import type { Message } from './api';
import type { EncryptedWorkSnapshot } from './encrypted-work-api';

export type ChannelReturnBaseline = { status: 'known'; lastReadMessageId: string | null; readAt: string | null } | { status: 'unavailable' };
export interface ChannelReturnSource {
  kind: 'message' | 'decision' | 'document' | 'task' | 'shared-work'; id: string;
  projectId?: string; resourceKind?: 'task' | 'document' | 'decision';
}
export interface ChannelReturnItem {
  source: ChannelReturnSource; title: string | null; occurredAt: string;
  reason: 'decision' | 'document' | 'mention' | 'reply' | 'assignment';
}
export interface ChannelReturnGroup { count: number; items: ChannelReturnItem[]; incomplete: boolean }
export interface ChannelReturnSnapshot {
  channel_id: string; since: string; generated_at: string;
  groups: { decisions: ChannelReturnGroup; documents: ChannelReturnGroup; attention: ChannelReturnGroup };
  incomplete: boolean; secure?: boolean;
}
export function returnTimestamp(value: string): number {
  return Date.parse(/(?:z|[+-]\d\d:\d\d)$/i.test(value) ? value : value + 'Z');
}
export function emptyReturnGroup(): ChannelReturnGroup { return { count: 0, items: [], incomplete: false }; }
export function finishReturnGroup(group: ChannelReturnGroup): ChannelReturnGroup {
  const unique = new Map<string, ChannelReturnItem>();
  for (const item of group.items) {
    const key = item.source.kind + ':' + item.source.id;
    const previous = unique.get(key);
    if (!previous || returnTimestamp(previous.occurredAt) < returnTimestamp(item.occurredAt)) unique.set(key, item);
  }
  const items = [...unique.values()].sort((a, b) => returnTimestamp(b.occurredAt) - returnTimestamp(a.occurredAt) || a.source.id.localeCompare(b.source.id));
  return { count: items.length, items: items.slice(0, 5), incomplete: group.incomplete };
}

/** Explicit selected mentions and replies to the viewer; prose is never classified. */
export function returnMessageAttention(messages: readonly Message[], channelId: string, userId: string, since: number): ChannelReturnGroup {
  const group = emptyReturnGroup(), byId = new Map(messages.filter(row => row.channel_id === channelId).map(row => [row.id, row]));
  for (const message of messages) {
    if (message.channel_id !== channelId || message.deleted || message.author_id === userId || !(returnTimestamp(message.created_at) > since)) continue;
    if (message.locked) { group.incomplete = true; continue; }
    const parent = message.parent_id ? byId.get(message.parent_id) : null;
    const reason = message.mentions?.includes(userId) ? 'mention' : parent?.author_id === userId && !parent.deleted && !parent.locked ? 'reply' : null;
    if (reason) group.items.push({ source: { kind: 'message', id: message.id }, title: null, occurredAt: message.created_at, reason });
  }
  return group;
}

/** The encrypted client has already checked room, device, sender and ancestry. */
export function returnEncryptedWork(view: EncryptedWorkSnapshot, since: number) {
  const decisions = emptyReturnGroup(), documents = emptyReturnGroup(), attention = emptyReturnGroup();
  for (const row of view.records) {
    if (row.invalid.length || row.conflicts) { decisions.incomplete = attention.incomplete = true; continue; }
    const revision = row.selected;
    if (revision.archived || revision.timestamp <= since) continue;
    const source = { kind: 'shared-work' as const, id: row.id, resourceKind: row.kind as 'decision' | 'task' };
    const item = { source, title: revision.fields.title, occurredAt: new Date(revision.timestamp).toISOString() };
    if (row.kind === 'decision') decisions.items.push({ ...item, reason: 'decision' });
    if (row.kind === 'task' && revision.fields.assignee_matrix_id === view.context.matrix_user_id && revision.fields.status !== 'done') attention.items.push({ ...item, reason: 'assignment' });
  }
  for (const row of view.legacyRecords) {
    if (row.invalid.length || row.conflicts) { documents.incomplete = attention.incomplete = true; continue; }
    if (row.selected.archived || row.selected.timestamp <= since) continue;
    const fields = row.selected.fields;
    if (row.kind === 'document') documents.items.push({ source: { kind: 'shared-work', id: row.id, resourceKind: 'document' }, title: null,
      occurredAt: new Date(row.selected.timestamp).toISOString(), reason: 'document' });
    if (row.kind === 'task' && 'assignee_matrix_id' in fields && fields.assignee_matrix_id === view.context.matrix_user_id && fields.status !== 'done') attention.items.push({ source: { kind: 'shared-work', id: row.id, resourceKind: 'task' }, title: fields.title,
      occurredAt: new Date(row.selected.timestamp).toISOString(), reason: 'assignment' });
  }
  if (view.warnings.length || view.quarantined_event_ids.length) decisions.incomplete = documents.incomplete = attention.incomplete = true;
  return { decisions, documents, attention };
}
