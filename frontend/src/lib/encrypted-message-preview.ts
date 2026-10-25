import type { Direction } from 'matrix-js-sdk';
import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { verifyEncryptedRoomEvent } from './encrypted-work-api';
import type { StackMessagePreview } from './document-organization-api';

const textTypes = new Set(['m.text', 'm.notice', 'm.emote']);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);

/** Current observed text, independently verified before it becomes a stack source. */
export async function verifiedEncryptedMessagePreview(channel: string, id: string): Promise<StackMessagePreview> {
  const token = api.getAccessToken(), actor = api.getCurrentUserId(); let live = true;
  const cancel = () => { live = false; }, current = () => {
    if (!live || !token || !actor || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) throw Error('The encrypted message account changed.');
  };
  window.addEventListener('freecollab:auth-changed', cancel); window.addEventListener('freecollab:session-expired', cancel);
  try {
    current();
    const original = await verifyEncryptedRoomEvent(channel, id, { requireCurrentParticipant: false }); current();
    if (original.event_id !== id || original.type !== 'm.room.message' || typeof original.content.body !== 'string'
        || !textTypes.has(String(original.content.msgtype)) || object(original.content['m.relates_to']) && original.content['m.relates_to'].rel_type === 'm.replace')
      throw Error('Choose a verified original text message from this room.');
    const secure = await getSecureMessaging(); current(); const { c, roomId } = await secure.roomFor(channel); current();
    // A null eventType retains undecryptable candidates. Asking the SDK for
    // m.room.message would filter them out before this independent verification.
    const related = await c.relations(roomId, id, 'm.replace', null, { dir: 'b' as Direction, limit: 100 }); current();
    if (!Array.isArray(related.events) || related.events.length > 100 || related.nextBatch != null && related.nextBatch !== '')
      throw Error('The complete encrypted edit history is unavailable. Refresh or open the original conversation.');
    const ids = related.events.map(event => event.getId());
    if (ids.some(value => typeof value !== 'string' || !value.startsWith('$') || value.length > 1024) || new Set(ids).size !== ids.length)
      throw Error('The encrypted edit history could not be verified.');
    const edits = [];
    for (const editId of ids) {
      const edit = await verifyEncryptedRoomEvent(channel, editId!, { requireCurrentParticipant: false }); current();
      const relation = edit.content['m.relates_to'], content = edit.content['m.new_content'];
      if (edit.event_id !== editId || edit.sender !== original.sender || edit.type !== 'm.room.message'
          || !object(relation) || relation.rel_type !== 'm.replace' || relation.event_id !== id
          || !object(content) || typeof content.body !== 'string' || !textTypes.has(String(content.msgtype)))
        throw Error('The encrypted message replacement could not be verified.');
      edits.push({ event: edit, text: content.body });
    }
    edits.sort((a, b) => a.event.timestamp - b.event.timestamp || (a.event.event_id < b.event.event_id ? -1 : a.event.event_id > b.event.event_id ? 1 : 0));
    const latest = edits[edits.length - 1], text = latest ? latest.text : original.content.body;
    if (!text || text.length > 20000) throw Error('This message exceeds the stack reference limit.');
    // A redaction or device removal during the relations read must not turn the
    // already-read base body into a valid fallback.
    const confirmed = await verifyEncryptedRoomEvent(channel, id, { requireCurrentParticipant: false }); current();
    if (confirmed.sender !== original.sender || JSON.stringify(confirmed.content) !== JSON.stringify(original.content)) throw Error('The source message changed. Review it again.');
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)); current();
    return { id, channel_id: channel, text, author_id: original.sender, created_at: new Date(original.timestamp).toISOString(),
      edited_at: latest ? new Date(latest.event.timestamp).toISOString() : null,
      expected_text_sha256: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('') };
  } finally { window.removeEventListener('freecollab:auth-changed', cancel); window.removeEventListener('freecollab:session-expired', cancel); }
}
