/** Entry decisions use authorized, chronological root messages. They never mark read. */
export interface ConversationEntryScope {
  userId: string | null;
  credential: string | null;
  workspaceId: string | null;
  channelId: string | null;
  secure: boolean;
}

export interface ConversationEntryMessage {
  id: string;
  channel_id: string;
  author_id: string;
  parent_id?: string;
  deleted?: boolean;
  locked?: boolean;
}

/** In-memory only: credentials must not be placed in DOM attributes or storage. */
export interface ConversationReadingPosition {
  scope: ConversationEntryScope;
  top: number;
  following: boolean;
}

export interface ConversationEntryOptions {
  scope: ConversationEntryScope;
  /** Oldest to newest, preserving the order supplied by the authorized adapter. */
  messages: readonly ConversationEntryMessage[];
  pendingSource?: { channelId: string; messageId: string } | null;
  remembered?: ConversationReadingPosition | null;
  /** A missing receipt and an unavailable receipt request are different states. */
  readState: { status: 'known'; lastReadMessageId: string | null } | { status: 'unavailable' };
  hasOlder: boolean;
}

export type ConversationEntry =
  | { kind: 'source'; messageId: string; loaded: boolean }
  | { kind: 'position'; top: number }
  | { kind: 'latest'; reason: 'remembered-follow' | 'no-unread' | 'empty' }
  | { kind: 'unread'; messageId: string }
  | { kind: 'history'; beforeId: string; lastReadMessageId: string | null }
  | { kind: 'unavailable'; reason: 'incomplete-scope' | 'read-state-unavailable' | 'read-boundary-unavailable' | 'read-content-unavailable'; oldestLoadedMessageId: string | null };

const completeScope = (scope: ConversationEntryScope) =>
  !!scope.userId && !!scope.credential && !!scope.workspaceId && !!scope.channelId;

export function sameConversationEntryScope(left: ConversationEntryScope, right: ConversationEntryScope): boolean {
  return completeScope(left) && completeScope(right)
    && left.userId === right.userId && left.credential === right.credential
    && left.workspaceId === right.workspaceId && left.channelId === right.channelId
    && left.secure === right.secure;
}

/**
 * Explicit source > scoped remembered choice > genuine unread boundary > latest.
 * `history` asks the caller to page through the same adapter, with its own bound.
 * At that bound the caller keeps an honest earlier-history affordance; neither
 * `history` nor `unavailable` is permission to mark the full channel as read.
 */
export function chooseConversationEntry(options: ConversationEntryOptions): ConversationEntry {
  const { scope, pendingSource, remembered, readState, hasOlder } = options;
  if (!completeScope(scope)) return { kind: 'unavailable', reason: 'incomplete-scope', oldestLoadedMessageId: null };
  const roots = options.messages.filter(message => message.channel_id === scope.channelId && !message.parent_id);
  if (pendingSource?.channelId === scope.channelId && pendingSource.messageId)
    return { kind: 'source', messageId: pendingSource.messageId, loaded: roots.some(message => message.id === pendingSource.messageId) };
  const oldestLoadedMessageId = roots[0]?.id || null;
  const receiptId = readState.status === 'known' ? readState.lastReadMessageId : null;
  const boundary = receiptId ? roots.findIndex(message => message.id === receiptId) : -1;
  const validMemory = remembered && sameConversationEntryScope(scope, remembered.scope)
    && Number.isFinite(remembered.top) && remembered.top >= 0;
  // A remembered reading position is navigation, never a claim that content is
  // readable. Automatic following/unread selection must not cross locked rows.
  if (validMemory && !remembered.following) return { kind: 'position', top: remembered.top };
  const newest = roots.slice().reverse().find(message => !message.deleted);
  if (newest?.locked || roots.slice(boundary + 1).some(message => !message.deleted && message.locked))
    return { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId };
  if (validMemory) return { kind: 'latest', reason: 'remembered-follow' };
  if (readState.status === 'unavailable') return { kind: 'unavailable', reason: 'read-state-unavailable', oldestLoadedMessageId };
  if (!roots.length) return { kind: 'latest', reason: 'empty' };

  if (hasOlder && (!receiptId || boundary < 0))
    return { kind: 'history', beforeId: roots[0].id, lastReadMessageId: receiptId };
  if (receiptId && boundary < 0)
    return { kind: 'unavailable', reason: 'read-boundary-unavailable', oldestLoadedMessageId };

  // Counts differ between native and secure transports. Only the real message
  // ordering and receipt identity establish which incoming root is first unread.
  const firstUnread = roots.slice(boundary + 1).find(message => !message.deleted && message.author_id !== scope.userId);
  return firstUnread ? { kind: 'unread', messageId: firstUnread.id } : { kind: 'latest', reason: 'no-unread' };
}
