import { changedFieldReferences, feedReferenceTexts, validateFieldReferences, type FieldReferences } from './field-references';
import { api } from './api';
import { getSecureMessaging } from './secure-client';

export interface FeedPoll { id: string; nonce: string; question: string; options: { id: string; label: string }[]; multiple: boolean; closes_at: string | null }
export interface FeedPollRouting { id: string; option_ids: string[]; digest: string; multiple: boolean; closes_at: string | null }
export interface FeedPostFields { field_references?: FieldReferences; title: string; body: string; poll: FeedPoll | null }
export interface FeedCommentFields { field_references?: FieldReferences; body: string }
export interface FeedVoteFields { option_ids: string[] }
export interface FeedReactionFields { emoji: 'like' | 'heart' | 'celebrate' | null }
export type FeedKind = 'post' | 'comment' | 'vote' | 'reaction';
export interface FeedRecord<T> {
  id: string; kind: FeedKind; channel_id: string; post_id: string; created_by: string; created_at: string;
  revision: number; revised_by: string; revised_at: string; archived: boolean;
  poll: FeedPollRouting | null; poll_id: string | null; content_mode: 'server' | 'matrix'; matrix_event_id: string | null;
  fields?: T; seen_revision?: number; resolution_error?: string;
}
export type FeedPost = FeedRecord<FeedPostFields>;
export type FeedComment = FeedRecord<FeedCommentFields>;
export type FeedVote = FeedRecord<FeedVoteFields>;
export type FeedReaction = FeedRecord<FeedReactionFields>;
export interface FeedDetail { post: FeedPost; comments: FeedComment[]; votes: FeedVote[]; reactions: FeedReaction[] }
export interface FeedPage { items: FeedPost[]; next_before_id: string | null; following: string[] }
const opaque = /^[0-9a-f]{32}$/;
const identifier = () => crypto.randomUUID().replace(/-/g, '');
const root = (channel: string) => `/channels/${encodeURIComponent(channel)}/activity-feed`;
const segment = encodeURIComponent;
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every(key => keys.includes(key));
const failure = () => new Error('Activity content could not be verified.');

function stable(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (object(value)) return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable(value[key])).join(',') + '}';
  return JSON.stringify(value);
}

export async function pollRouting(poll: FeedPoll): Promise<FeedPollRouting> {
  const normalized = { ...poll, closes_at: poll.closes_at ? new Date(poll.closes_at).toISOString() : null };
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(stable(normalized)));
  return { id: poll.id, option_ids: poll.options.map(option => option.id), multiple: poll.multiple,
    closes_at: normalized.closes_at, digest: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') };
}

function validPoll(value: unknown): value is FeedPoll {
  if (!object(value) || !exact(value, ['id', 'nonce', 'question', 'options', 'multiple', 'closes_at'])
    || typeof value.id !== 'string' || !opaque.test(value.id) || typeof value.nonce !== 'string' || !opaque.test(value.nonce)
    || typeof value.question !== 'string' || !value.question.trim() || value.question.length > 240 || typeof value.multiple !== 'boolean'
    || value.closes_at !== null && (typeof value.closes_at !== 'string' || !Number.isFinite(Date.parse(value.closes_at)))
    || !Array.isArray(value.options) || value.options.length < 2 || value.options.length > 8) return false;
  const ids = new Set<string>();
  for (const option of value.options) {
    if (!object(option) || !exact(option, ['id', 'label']) || typeof option.id !== 'string' || !opaque.test(option.id)
      || ids.has(option.id) || typeof option.label !== 'string' || !option.label.trim() || option.label.length > 160) return false;
    ids.add(option.id);
  }
  return true;
}

function validReferences(value: Record<string, unknown>, post: boolean) {
  try {
    if (typeof value.body !== 'string' || post && typeof value.title !== 'string') return false;
    validateFieldReferences(feedReferenceTexts(value as unknown as FeedPostFields | FeedCommentFields), value.field_references); return true;
  } catch { return false; }
}

function validFields(kind: FeedKind, value: unknown): boolean {
  if (!object(value)) return false;
  if (kind === 'post') return validReferences(value, true) && exact(value, ['title', 'body', 'poll', 'field_references']) && typeof value.title === 'string' && !!value.title.trim()
    && value.title.length <= 160 && typeof value.body === 'string' && value.body.length <= 20000 && (value.poll === null || validPoll(value.poll));
  if (kind === 'comment') return validReferences(value, false) && exact(value, ['body', 'field_references']) && typeof value.body === 'string' && !!value.body.trim() && value.body.length <= 4000;
  if (kind === 'reaction') return exact(value, ['emoji']) && [null, 'like', 'heart', 'celebrate'].includes(value.emoji as string | null);
  return exact(value, ['option_ids']) && Array.isArray(value.option_ids) && value.option_ids.length <= 8
    && value.option_ids.every(item => typeof item === 'string' && opaque.test(item)) && new Set(value.option_ids).size === value.option_ids.length;
}

async function resolve<T>(row: FeedRecord<T>): Promise<FeedRecord<T>> {
  const token = api.getAccessToken();
  try {
    let fields = row.fields;
    if (row.content_mode === 'matrix') {
      if (!row.matrix_event_id || fields !== undefined) throw failure();
      const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(row.channel_id, row.matrix_event_id);
      const { c, roomId } = await secure.roomFor(row.channel_id), content = event.getContent();
      const domain = c.getUserId()!.split(':').slice(1).join(':');
      if (event.getRoomId() !== roomId || event.isRedacted() || event.getType() !== 'eu.freecollab.activity.' + row.kind
        || event.getSender() !== `@fc_${row.revised_by}:${domain}` || content.id !== row.id || content.kind !== row.kind
        || content.post_id !== row.post_id || content.revision !== row.revision || content.archived !== row.archived
        || content.poll_id !== row.poll_id) throw failure();
      fields = content.fields as T;
    } else if (row.content_mode !== 'server' || row.matrix_event_id) throw failure();
    if (!validFields(row.kind, fields)) throw failure();
    if (row.kind === 'post') {
      const poll = (fields as FeedPostFields).poll;
      const route = poll ? await pollRouting(poll) : null;
      const metadata = row.poll ? { ...row.poll, closes_at: row.poll.closes_at ? new Date(row.poll.closes_at).toISOString() : null } : null;
      if (stable(route) !== stable(metadata)) throw failure();
    }
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return { ...row, fields, resolution_error: undefined };
  } catch (error) {
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return { ...row, fields: undefined, resolution_error: error instanceof Error ? error.message : 'Unlock this device to read encrypted activity.' };
  }
}

async function write<T>(channel: string, kind: FeedKind, fields: T, previous?: FeedRecord<T>, options?: { id?: string; postId?: string; archived?: boolean; pollId?: string | null }): Promise<FeedRecord<T>> {
  if (!validFields(kind, fields)) throw new Error('Check the activity fields before saving.');
  const token = api.getAccessToken(), current = () => { if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
  const actor = api.getCurrentUserId(); if (!actor) throw new Error('Sign in before publishing activity.');
  const postId = kind === 'post' ? previous?.id || options?.id || identifier() : options?.postId;
  if (!postId) throw new Error('Choose an activity post.');
  const id = kind === 'post' ? postId : kind === 'comment' ? previous?.id || options?.id || identifier() : postId + ':' + actor + ':' + kind;
  const revision = (previous?.revision || 0) + 1, archived = options?.archived ?? previous?.archived ?? false;
  const pollId = options?.pollId || null;
  const secure = await getSecureMessaging(); current(); const encrypted = await secure.handles(channel); current();
  const mentioned = kind === "post" || kind === "comment" ? changedFieldReferences(feedReferenceTexts(fields as FeedPostFields | FeedCommentFields), (fields as FeedPostFields | FeedCommentFields).field_references, previous?.fields ? feedReferenceTexts(previous.fields as unknown as FeedPostFields | FeedCommentFields) : undefined, (previous?.fields as unknown as FeedPostFields | FeedCommentFields | undefined)?.field_references) : [];
  if (mentioned.length) {
    const { messageReferencesApi } = await import('./message-references-api'); current();
    const resolved = await messageReferencesApi.resolve(channel, mentioned, undefined, encrypted); current();
    if (resolved.some(row => !row.available)) throw new Error('A selected reference is no longer available in this conversation.');
  }
  const route = kind === 'post' && (fields as FeedPostFields).poll ? await pollRouting((fields as FeedPostFields).poll!) : null; current();
  let payload: Record<string, unknown> = { expected_revision: revision - 1, fields };
  if (kind === 'post' || kind === 'comment') payload = { ...payload, id, archived };
  else payload.poll_id = pollId;
  if (encrypted) {
    const event = await secure.sendEvent(channel, 'eu.freecollab.activity.' + kind, { id, kind, post_id: postId, revision, archived, poll_id: pollId, fields }); current();
    delete payload.fields; payload.matrix_event_id = event.event_id;
    if (kind === 'post') payload.poll = route;
  }
  const path = root(channel) + (kind === 'post' ? '' : '/' + segment(postId) + (kind === 'comment' ? '/comments' : '/' + kind));
  const saved = await api.request<FeedRecord<T>>(path, { method: 'POST', body: JSON.stringify(payload) }); current();
  const result = await resolve(saved); current(); return result;
}

export const activityFeedApi = {
  identifier,
  async posts(channel: string, options: { following?: boolean; archived?: boolean; beforeId?: string } = {}) {
    const token = api.getAccessToken(), query = new URLSearchParams({ following: String(!!options.following), archived: String(!!options.archived) });
    if (options.beforeId) query.set('before_id', options.beforeId);
    const page = await api.request<FeedPage>(root(channel) + '?' + query), items = await Promise.all(page.items.map(resolve<FeedPostFields>));
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); return { ...page, items };
  },
  async detail(channel: string, postId: string): Promise<FeedDetail> {
    const token = api.getAccessToken(), record = await api.request<FeedDetail>(root(channel) + '/' + segment(postId));
    const [post, comments, votes, reactions] = await Promise.all([resolve(record.post), Promise.all(record.comments.map(resolve<FeedCommentFields>)),
      Promise.all(record.votes.map(resolve<FeedVoteFields>)), Promise.all(record.reactions.map(resolve<FeedReactionFields>))]);
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    const verifiedVotes = votes.map(vote => {
      const ids = vote.fields?.option_ids, poll = post.fields?.poll;
      if (vote.resolution_error) return vote;
      if (!poll || vote.poll_id !== poll.id || !ids || ids.some(id => !poll.options.some(option => option.id === id)) || !poll.multiple && ids.length > 1)
        return { ...vote, fields: undefined, resolution_error: 'This poll response could not be verified.' };
      return vote;
    });
    return { post, comments, votes: verifiedVotes, reactions };
  },
  savePost: (channel: string, fields: FeedPostFields, previous?: FeedPost, options?: { id?: string; archived?: boolean }) => write(channel, 'post', fields, previous, options),
  saveComment: (channel: string, postId: string, fields: FeedCommentFields, previous?: FeedComment, archived = false) => write(channel, 'comment', fields, previous, { postId, archived }),
  vote: (channel: string, post: FeedPost, option_ids: string[], previous?: FeedVote) => write(channel, 'vote', { option_ids }, previous, { postId: post.id, pollId: post.poll?.id }),
  react: (channel: string, postId: string, emoji: FeedReactionFields['emoji'], previous?: FeedReaction) => write(channel, 'reaction', { emoji }, previous, { postId }),
  async history(channel: string, postId: string, commentId?: string) {
    const token = api.getAccessToken(), rows = await api.request<FeedRecord<FeedPostFields | FeedCommentFields>[]>(root(channel) + '/' + segment(postId)
      + (commentId ? '/comments/' + segment(commentId) : '') + '/revisions');
    const result = await Promise.all(rows.map(resolve)); if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); return result;
  },
  follow: (channel: string, target: string, following: boolean) => api.request(root(channel) + '/following/' + segment(target), { method: 'PUT', body: JSON.stringify({ following }) }),
  read: (channel: string, post: FeedPost) => api.request(root(channel) + '/' + segment(post.id) + '/read', { method: 'PUT', body: JSON.stringify({ seen_revision: post.revision }) }),
};
