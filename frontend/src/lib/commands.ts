export type CommandAction = 'create' | 'files' | 'members' | 'appearance';

export interface CommandChannel {
  id: string;
  name: string | null;
}

export interface CommandMessage {
  id: string;
  content: string;
  author_id: string;
  created_at: string;
  deleted?: boolean;
  parent_id?: string;
}

interface CommandBase {
  id: string;
  label: string;
  description: string;
}

export type CommandResult =
  | (CommandBase & { kind: 'channel'; channelId: string; current: boolean })
  | (CommandBase & { kind: 'message'; messageId: string; createdAt: string })
  | (CommandBase & { kind: 'action'; action: CommandAction });

interface CommandSearchOptions {
  query: string;
  channels: CommandChannel[];
  activeChannelId: string | null;
  messages: CommandMessage[];
  hasActiveChannel: boolean;
}

const ACTIONS: { action: CommandAction; label: string; description: string; aliases: string; needsChannel?: boolean }[] = [
  { action: 'create', label: 'Create a channel', description: 'Give the next conversation a home', aliases: 'new group conversation' },
  { action: 'files', label: 'Open files', description: 'Files shared in this channel', aliases: 'attachments upload documents', needsChannel: true },
  { action: 'members', label: 'Manage members', description: 'People and invitations in this channel', aliases: 'team invite people membership', needsChannel: true },
  { action: 'appearance', label: 'Appearance settings', description: 'Your colors, type and personal space', aliases: 'theme preferences font typography palette light dark system' },
];

export function normalizeCommandQuery(value: string): string {
  return value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
}

function matchScore(label: string, query: string, aliases = ''): number {
  if (!query) return 0;
  const normalized = normalizeCommandQuery(label);
  if (normalized === query) return 0;
  if (normalized.startsWith(query)) return 1;
  if (normalized.includes(query)) return 2;
  const searchable = normalized + ' ' + normalizeCommandQuery(aliases);
  return query.split(' ').every(word => searchable.includes(word)) ? 3 : -1;
}

function messageExcerpt(content: string, query: string): string {
  const text = content.replace(/\s+/g, ' ').trim();
  const firstWord = query.split(' ')[0];
  const match = normalizeCommandQuery(text).indexOf(firstWord);
  const start = Math.max(0, match - 45);
  const excerpt = text.slice(start, start + 150);
  return (start > 0 ? '…' : '') + excerpt + (start + 150 < text.length ? '…' : '');
}

/** Searches only data already supplied by the authenticated channel view. */
export function searchCommands({ query: input, channels, activeChannelId, messages, hasActiveChannel }: CommandSearchOptions): CommandResult[] {
  const query = normalizeCommandQuery(input);
  const channelQuery = query.startsWith('#') ? query.slice(1).trim() : query;
  const channelsOnly = query.startsWith('#');
  const channelResults = channels
    .map((channel, index) => ({ channel, index, score: matchScore(channel.name || 'Untitled channel', channelQuery) }))
    .filter(item => item.score >= 0)
    .sort((left, right) => left.score - right.score || Number(right.channel.id === activeChannelId) - Number(left.channel.id === activeChannelId) || left.index - right.index)
    .slice(0, query ? 10 : 8)
    .map(({ channel }): CommandResult => ({
      id: 'channel:' + channel.id, kind: 'channel', channelId: channel.id,
      label: channel.name || 'Untitled channel', description: channel.id === activeChannelId ? 'Current channel' : 'Go to channel',
      current: channel.id === activeChannelId,
    }));

  if (channelsOnly) return channelResults;

  const actionResults = ACTIONS
    .filter(action => !action.needsChannel || hasActiveChannel)
    .map((item, index) => ({ item, index, score: matchScore(item.label, query, item.aliases) }))
    .filter(item => item.score >= 0)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .map(({ item }): CommandResult => ({
      id: 'action:' + item.action, kind: 'action', action: item.action,
      label: item.label, description: item.description,
    }));

  const messageResults: CommandResult[] = query && hasActiveChannel
    ? messages.filter(message => !message.deleted && !message.parent_id && message.content.trim() && matchScore(message.content, query) >= 0)
      .slice(-6).reverse()
      .map(message => ({
        id: 'message:' + message.id, kind: 'message', messageId: message.id,
        label: messageExcerpt(message.content, query), description: 'Jump to message', createdAt: message.created_at,
      }))
    : [];

  return [...channelResults, ...actionResults, ...messageResults];
}

export function nextCommandIndex(current: number, direction: 1 | -1, count: number): number {
  if (count <= 0) return -1;
  if (current < 0) return direction === 1 ? 0 : count - 1;
  return ((current + direction) % count + count) % count;
}
