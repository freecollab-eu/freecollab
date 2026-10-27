import { api, type Channel, type Message } from './api';
import { getSecureMessaging } from './secure-client';

/** Fetch a source under its current channel authority, independently of timeline caches. */
export async function resolveNavigationMessage(channel: Pick<Channel, 'id' | 'workspace_id' | 'secure'>, messageId: string, isCurrent: () => boolean): Promise<Message> {
  const token = api.getAccessToken(), actor = api.getCurrentUserId();
  const expected = { id: channel.id, workspace: channel.workspace_id, secure: channel.secure === true };
  const validId = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 1024 && !/[\s\u0000-\u001f\u007f]/.test(value);
  const unavailable = () => new Error('This source message is no longer available in this conversation.');
  const current = () => {
    if (!token || !actor || token !== api.getAccessToken() || actor !== api.getCurrentUserId() || !isCurrent()) throw unavailable();
  };
  current();
  if (!validId(expected.id) || /[/?#]/.test(expected.id) || !validId(messageId)) throw unavailable();
  const verifyChannel = async () => {
    current(); const fresh = await api.getChannel(expected.id); current();
    if (fresh.id !== expected.id || fresh.workspace_id !== expected.workspace || (fresh.secure === true) !== expected.secure) throw unavailable();
  };
  const verify = (message: Message, id: string) => {
    if (!message || message.id !== id || message.channel_id !== expected.id || message.deleted || message.locked
      || typeof message.content !== 'string' || !validId(message.author_id)
      || message.parent_id != null && (!validId(message.parent_id) || message.parent_id === message.id)) throw unavailable();
    return message;
  };
  await verifyChannel();
  let message: Message, parent: Message | undefined;
  if (expected.secure) {
    const secure = await getSecureMessaging(); current();
    message = verify(await secure.getMessage(expected.id, messageId), messageId); current();
    if (message.encrypted !== true) throw unavailable();
    // The secure adapter verifies its actual decrypted parent within the same
    // room and generation. No native message endpoint is used in this branch.
  } else {
    message = verify(await api.request<Message>('/messages/' + encodeURIComponent(messageId)), messageId); current();
    if (message.parent_id) {
      parent = verify(await api.request<Message>('/messages/' + encodeURIComponent(message.parent_id)), message.parent_id); current();
      if (parent.parent_id != null) throw unavailable();
    }
  }
  await verifyChannel(); current();
  verify(message, messageId);
  if (parent && (message.parent_id !== parent.id || verify(parent, parent.id).parent_id != null)) throw unavailable();
  return message;
}
