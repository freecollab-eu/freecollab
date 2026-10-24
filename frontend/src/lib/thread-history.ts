import type { Message } from './api';

/** Older pages cannot overwrite newer socket edits or cross a thread boundary. */
export function mergeThreadReplies(current: Message[], incoming: Message[], channelId: string, parentId: string): Message[] {
  if (incoming.some(reply => reply.channel_id !== channelId || reply.parent_id !== parentId)) throw new Error('The thread response belongs to another conversation.');
  const rows = new Map(incoming.map(reply => [reply.id, reply]));
  for (const reply of current) if (reply.channel_id === channelId && reply.parent_id === parentId) rows.set(reply.id, reply);
  const time = (value: string) => Date.parse(/[zZ]$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z');
  return [...rows.values()].sort((a, b) => time(a.created_at) - time(b.created_at) || a.id.localeCompare(b.id));
}
