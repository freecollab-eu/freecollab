import { normalizeMessageReferences, validateMessageReferences, type MessageReference } from '../message-references';

export const REFERENCE_CONTENT_KEY = 'eu.freecollab.references';
export interface ReferenceResolution {
  items: { kind: MessageReference['kind']; id: string; available: boolean; user_ids?: string[] }[];
}
type ReferenceIdentity = Pick<MessageReference, 'kind' | 'id'>;
const identifier = (id: unknown): id is string => typeof id === 'string' && id.length > 0 && id.length <= 128 && !/[\s\u0000-\u001f\u007f]/.test(id);

/** Resolve only opaque identities; neither the draft nor its displayed labels leave the encrypted client. */
export async function prepareMessageReferences(
  content: string, input: MessageReference[], legacyMentions: string[],
  resolve: (references: ReferenceIdentity[]) => Promise<ReferenceResolution>,
): Promise<{ references: MessageReference[]; mentions: string[] }> {
  const references = validateMessageReferences(content, input);
  if (!Array.isArray(legacyMentions) || legacyMentions.length > 50 || legacyMentions.some(id => !identifier(id))) throw new Error('Invalid message recipients.');
  const mentions = new Set(legacyMentions);
  const keys = [...new Map(references.map(reference => [reference.kind + '\0' + reference.id, { kind: reference.kind, id: reference.id }])).values()];
  if (keys.length) {
    const response = await resolve(keys);
    if (!response || !Array.isArray(response.items) || response.items.length > 50) throw new Error('Current reference access could not be verified.');
    for (const identity of keys) {
      const rows = response.items.filter(row => row?.kind === identity.kind && row.id === identity.id);
      if (rows.length !== 1 || rows[0].available !== true) throw new Error('A selected reference is no longer available in this conversation.');
      if (identity.kind === 'person' || identity.kind === 'group') {
        const recipients = rows[0].user_ids;
        if (!Array.isArray(recipients) || recipients.length > 50 || recipients.some(id => !identifier(id))) throw new Error('Current group members could not be verified.');
        for (const id of recipients) mentions.add(id);
      }
    }
  }
  if (mentions.size > 50) throw new Error('This message would notify more than 50 people. Choose a smaller group.');
  return { references, mentions: [...mentions] };
}

/** This payload must only be passed to the verified encrypted send path. */
export function encryptedMessageContent(content: string, mentions: string[], references: MessageReference[], parentId?: string, matrixUserIds: string[] = []): Record<string, unknown> {
  return { msgtype: 'm.text', body: content, 'eu.freecollab.mentions': [...mentions],
    [REFERENCE_CONTENT_KEY]: validateMessageReferences(content, references),
    ...(matrixUserIds.length ? { 'm.mentions': { user_ids: [...matrixUserIds] } } : {}),
    ...(parentId ? { 'm.relates_to': { rel_type: 'm.thread', event_id: parentId, is_falling_back: true, 'm.in_reply_to': { event_id: parentId } } } : {}) };
}

/** Bad/legacy metadata displays as literal text and never becomes a guessed recipient. */
export function encryptedContentReferences(content: string, payload: Record<string, unknown>): MessageReference[] {
  return normalizeMessageReferences(content, payload[REFERENCE_CONTENT_KEY]);
}
