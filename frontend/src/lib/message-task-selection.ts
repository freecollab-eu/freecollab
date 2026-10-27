import type { Message } from './api';

export interface MessageTaskExcerpt {
  messageId: string;
  fingerprint: string;
  text: string;
  start: number;
  end: number;
  root: HTMLElement;
  range: Range;
  rect: { left: number; top: number; right: number; bottom: number; width: number; height: number };
}

export const taskSelectionFingerprint = (message: Message) => JSON.stringify([
  message.id, message.channel_id, message.author_id, message.created_at, message.edited_at || null,
  message.content, message.references || [], !!message.deleted, !!message.locked,
]);
const element = (node: Node | null): Element | null => node?.nodeType === 1 ? node as Element : node?.parentElement || null;

/** Read only a real source-text Range, never adjacent controls or message chrome. */
export function readMessageTaskSelection(selection: Selection | null, channelId: string, messages: readonly Message[]): MessageTaskExcerpt | null {
  if (!selection || selection.rangeCount !== 1 || selection.isCollapsed || !channelId) return null;
  try {
    const range = selection.getRangeAt(0);
    const root = element(range.startContainer)?.closest<HTMLElement>('[data-message-source-text]');
    if (!root || !root.isConnected || root !== element(range.endContainer)?.closest('[data-message-source-text]')
      || root.closest('[hidden], [inert], [aria-hidden="true"], input, textarea, [contenteditable="true"]')) return null;
    if (element(range.startContainer)?.closest('input, textarea, [contenteditable]:not([contenteditable="false"])')
      || element(range.endContainer)?.closest('input, textarea, [contenteditable]:not([contenteditable="false"])')) return null;
    const wrapper = root.closest<HTMLElement>('[data-message-source-id]');
    const id = wrapper?.dataset.messageSourceId;
    if (!id || !wrapper?.contains(root)) return null;
    const matches = messages.filter(message => message.id === id && message.channel_id === channelId);
    if (!matches.length || matches.some(message => taskSelectionFingerprint(message) !== taskSelectionFingerprint(matches[0]))) return null;
    const message = matches[0];
    if (message.deleted || message.locked || typeof message.content !== 'string') return null;
    const text = range.toString();
    if (!text.trim() || text.length > 12000) return null;
    const prefix = range.cloneRange();
    prefix.selectNodeContents(root); prefix.setEnd(range.startContainer, range.startOffset);
    const before = prefix.toString(), start = before.length, end = start + text.length;
    if (message.content.slice(0, start) !== before || message.content.slice(start, end) !== text) return null;
    const rect = range.getBoundingClientRect();
    if (![rect.left, rect.top, rect.right, rect.bottom, rect.width, rect.height].every(Number.isFinite) || rect.width <= 0 || rect.height <= 0) return null;
    return { messageId: id, fingerprint: taskSelectionFingerprint(message), text, start, end, root, range: range.cloneRange(),
      rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height } };
  } catch { return null; }
}

export function sameMessageTaskSelection(left: MessageTaskExcerpt, right: MessageTaskExcerpt) {
  return left.messageId === right.messageId && left.fingerprint === right.fingerprint && left.root === right.root
    && left.start === right.start && left.end === right.end && left.text === right.text;
}

export function messageTaskToolbarPosition(rect: MessageTaskExcerpt['rect'], viewport: { width: number; height: number }, size = { width: 196, height: 40 }) {
  const left = Math.max(8, Math.min(rect.left + rect.width / 2 - size.width / 2, viewport.width - size.width - 8));
  const above = rect.top - size.height - 8;
  const top = Math.max(8, Math.min(above >= 8 ? above : rect.bottom + 8, viewport.height - size.height - 8));
  return { left, top };
}
