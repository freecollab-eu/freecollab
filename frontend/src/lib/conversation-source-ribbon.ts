import type { Message } from './api';

/** A short quote of an actual, available message in this conversation. */
export function conversationSourcePreview(message: Message | null | undefined, channelId: string) {
  if (!message?.id || !channelId || message.channel_id !== channelId || message.deleted || message.locked) return null;
  const content = message.content.replace(/\s+/gu, ' ').trim();
  const characters = Array.from(content);
  const timestamp = new Date(message.created_at);
  return {
    id: message.id,
    quote: characters.length > 180 ? characters.slice(0, 179).join('') + '…' : content,
    createdAt: Number.isFinite(timestamp.getTime()) ? timestamp.toISOString() : null,
  };
}
