import { api, APIError, type Message } from '../lib/api';
import { normalizeMessageReferences, reconcileReferences, validateMessageReferences, type MessageReference } from '../lib/message-references';
import { attachmentOutbox, offlineLibrary } from './library';
import { assertOwner, fingerprint, listPrivate, owner, purgeOtherAccounts, removePrivate, savePrivate, type Owner } from './storage';

export interface QueuedMessage { id: string; channelId: string; content: string; mentions: string[]; references?: MessageReference[]; secure: boolean; createdAt: string; tokenFingerprint: string; state: 'queued' | 'review'; sendAt?: string; error?: string }
export interface PrivateDraft { channelId: string; content: string; references?: MessageReference[]; updatedAt: string; secure?: boolean }
export interface RichDraft { text: string; references: MessageReference[] }
export type SendResult = { kind: 'sent'; message: Message } | { kind: 'queued'; id: string };
const changed = () => window.dispatchEvent(new Event('freecollab:mobile-state'));
let installed = false, flushing: Promise<void> | null = null;
let installPrompt: (Event & { prompt(): Promise<void>; userChoice: Promise<{ outcome: string }> }) | null = null;
const challenges = new Map<string, { challenge: string; received: number }>();
const draftWrites = new Map<string, Promise<void>>();
const draftWriteKey = (channelId: string, expected: Owner) => expected.user + '\0' + expected.token + '\0' + channelId;

function changeDraft(channelId: string, update: (expected: Owner) => Promise<void>) {
  if (!channelId) return Promise.reject(new Error('Choose a conversation for this draft.'));
  const expected = owner(), key = draftWriteKey(channelId, expected);
  const pending = (draftWrites.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
    assertOwner(expected); await update(expected); assertOwner(expected);
  });
  draftWrites.set(key, pending);
  void pending.finally(() => { if (draftWrites.get(key) === pending) draftWrites.delete(key); }).catch(() => {});
  return pending;
}

async function writeDraft(channelId: string, value: RichDraft, secure: boolean | undefined, expected: Owner, rows?: Awaited<ReturnType<typeof listPrivate<PrivateDraft>>>) {
  if (value.text.length > 20000) throw new Error('This draft is too large.');
  const references = validateMessageReferences(value.text, value.references);
  if (!value.text) { await removePrivate('draft', channelId, expected); changed(); return; }
  const previous = rows || await listPrivate<PrivateDraft>('draft', expected);
  assertOwner(expected);
  if (previous.length >= 100 && !previous.some(row => row.id === channelId)) throw new Error('Remove an old draft before creating another.');
  await savePrivate('draft', channelId, { content: value.text, references, updatedAt: new Date().toISOString(), secure: secure ?? previous.find(row => row.id === channelId)?.value.secure }, expected);
  changed();
}

export const mobileDrafts = {
  async save(channelId: string, content: string, secure?: boolean) {
    return changeDraft(channelId, async expected => {
      const rows = await listPrivate<PrivateDraft>('draft', expected), previous = rows.find(row => row.id === channelId)?.value;
      const before = typeof previous?.content === 'string' ? previous.content : '';
      const references = reconcileReferences(before, content, normalizeMessageReferences(before, previous?.references));
      await writeDraft(channelId, { text: content, references }, secure, expected, rows);
    });
  },
  async saveRich(channelId: string, draft: RichDraft, secure?: boolean) {
    // Snapshot before yielding: callers may continue editing while encryption runs.
    const value = { text: draft.text, references: validateMessageReferences(draft.text, draft.references) };
    return changeDraft(channelId, expected => writeDraft(channelId, value, secure, expected));
  },
  async load(channelId: string): Promise<string> {
    return (await mobileDrafts.loadRich(channelId)).text;
  },
  async loadRich(channelId: string): Promise<RichDraft> {
    const expected = owner();
    await draftWrites.get(draftWriteKey(channelId, expected)); assertOwner(expected);
    const rows = await listPrivate<PrivateDraft>('draft', expected), value = rows.find(row => row.id === channelId)?.value;
    const text = typeof value?.content === 'string' && value.content.length <= 20000 ? value.content : '';
    return { text, references: normalizeMessageReferences(text, value?.references) };
  },
  clear: (channelId: string) => changeDraft(channelId, async expected => { await removePrivate('draft', channelId, expected); changed(); }),
  async list(): Promise<PrivateDraft[]> { return (await listPrivate<PrivateDraft>('draft')).map(row => ({ ...row.value, references: normalizeMessageReferences(row.value.content, row.value.references), channelId: row.id })); },
};

async function deliver(row: QueuedMessage, expected: Owner): Promise<Message> {
  assertOwner(expected);
  if (row.tokenFingerprint !== await fingerprint(expected.token) || Date.now() - Date.parse(row.createdAt) > 7 * 86400000)
    throw new Error('Your sign-in changed or this queue item expired. Check conversation history before sending it again.');
  const me = await api.request<{ id: string }>('/auth/me', { signal: AbortSignal.timeout(20000) });
  assertOwner(expected); if (me.id !== expected.user) throw new Error('The signed-in account changed.');
  const members = await api.request<Array<{ user_id: string }>>('/channels/' + encodeURIComponent(row.channelId) + '/members', { signal: AbortSignal.timeout(20000) });
  assertOwner(expected); if (!members.some(member => member.user_id === expected.user)) throw new Error('You no longer belong to this channel.');
  const secure = await (await import('../lib/secure-client')).getSecureMessaging();
  const encrypted = await secure.handles(row.channelId); assertOwner(expected);
  const references = validateMessageReferences(row.content, row.references || []);
  if (encrypted !== row.secure) throw new Error('This channel’s encryption mode changed. Review the queued message first.');
  if (encrypted) {
    const result = await secure.sendMessage(row.channelId, row.content, undefined, row.mentions, references, 'mobile-' + row.id);
    assertOwner(expected);
    return api.rememberMessage(result);
  }
  const result = await api.request<Message>('/mobile/offline/channels/' + encodeURIComponent(row.channelId) + '/messages', {
    method: 'POST', body: JSON.stringify({ client_id: row.id, content: row.content, mentions: row.mentions, references }), signal: AbortSignal.timeout(20000),
  });
  assertOwner(expected); return api.rememberMessage(result);
}
function isNetworkFailure(error: unknown) {
  return error instanceof TypeError || error instanceof DOMException && ['AbortError', 'TimeoutError', 'NetworkError'].includes(error.name);
}
async function queued(row: QueuedMessage, expected: Owner) { await savePrivate('outbox', row.id, row, expected); changed(); }

export const mobileOutbox = {
  async list(): Promise<QueuedMessage[]> { return (await listPrivate<QueuedMessage>('outbox')).map(row => row.value).sort((a, b) => a.createdAt.localeCompare(b.createdAt)); },
  async remove(id: string) { await removePrivate('outbox', id); changed(); },
  async schedule(channelId: string, content: string, mentions: string[], sendAt: string, references: MessageReference[] = []) {
    if (api.isMemorySession()) throw new Error('Scheduled encrypted messages need the standalone app on this trusted device.');
    const due = Date.parse(sendAt), expected = owner();
    if (!content.trim() || content.length > 10000 || mentions.length > 50 || !Number.isFinite(due) || due <= Date.now() || due > Date.now() + 7 * 86400000)
      throw new Error('Choose a future time within seven days and a message within the supported size.');
    const selectedMentions = [...mentions], checked = validateMessageReferences(content, references);
    if ((await listPrivate('outbox', expected)).length >= 100) throw new Error('The offline queue is full.');
    const row: QueuedMessage = { id: crypto.randomUUID(), channelId, content, mentions: selectedMentions, references: checked, secure: true,
      createdAt: new Date().toISOString(), sendAt: new Date(due).toISOString(),
      tokenFingerprint: await fingerprint(expected.token), state: 'queued' };
    await queued(row, expected); return row;
  },
  async sendOrQueue(channelId: string, content: string, mentions: string[] = [], secure = false, references: MessageReference[] = []): Promise<SendResult> {
    if (!content.trim() || content.length > 20000 || mentions.length > 50) throw new Error('Enter a message within the supported size.');
    const expected = owner(), selectedMentions = [...mentions], checked = validateMessageReferences(content, references);
    if ((await listPrivate('outbox', expected)).length >= 100) throw new Error('The offline queue is full. Review queued messages first.');
    const row: QueuedMessage = { id: crypto.randomUUID(), channelId, content, mentions: selectedMentions, references: checked, secure,
      createdAt: new Date().toISOString(), tokenFingerprint: await fingerprint(expected.token), state: 'queued' };
    if (api.isMemorySession()) {
      if (!navigator.onLine) throw new Error('Embedded collaboration needs a connection. Keep your draft open and try again online.');
      return { kind: 'sent', message: await deliver(row, expected) };
    }
    await queued(row, expected);
    if (!navigator.onLine) return { kind: 'queued', id: row.id };
    try {
      const message = await deliver(row, expected); await removePrivate('outbox', row.id, expected); changed();
      return { kind: 'sent', message };
    } catch (error) {
      assertOwner(expected);
      if (isNetworkFailure(error)) return { kind: 'queued', id: row.id };
      row.state = 'review'; row.error = error instanceof APIError && [401, 403].includes(error.status)
        ? 'Sign in again and check this conversation before sending.' : 'This message needs review before it can be sent.';
      await queued(row, expected); throw error;
    }
  },
  async flush() {
    if (flushing) return flushing;
    flushing = (async () => {
      if (!navigator.onLine || !api.getCurrentUserId()) return;
      const expected = owner();
      const work = async () => {
        for (const row of await mobileOutbox.list()) {
          assertOwner(expected); if (!navigator.onLine) break; if (row.state !== 'queued' || row.sendAt && Date.parse(row.sendAt) > Date.now()) continue;
          try {
            const message = await deliver(row, expected); await removePrivate('outbox', row.id, expected);
            window.dispatchEvent(new CustomEvent('freecollab:offline-message-sent', { detail: message })); changed();
          } catch (error) {
            assertOwner(expected); if (isNetworkFailure(error)) break;
            row.state = 'review'; row.error = 'Your sign-in, channel access or encryption state changed. Check history before sending again.';
            await queued(row, expected);
          }
        }
      };
      if (navigator.locks) await navigator.locks.request('freecollab-mobile-send:' + expected.user, work); else await work();
    })().finally(() => { flushing = null; });
    return flushing;
  },
};

export async function installApplication() {
  if (!installPrompt) throw new Error('Use your browser’s Install app or Add to Home Screen action.');
  await installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; changed();
}
export function mobileCapabilities() {
  return { online: navigator.onLine, installPrompt: !!installPrompt, installed: matchMedia('(display-mode: standalone)').matches,
    serviceWorker: 'serviceWorker' in navigator, push: 'PushManager' in window && 'Notification' in window,
    notificationPermission: 'Notification' in window ? Notification.permission : 'unsupported' };
}

export async function initializeMobile() {
  if (installed) return; installed = true;
  const sync = () => {
    api.syncStoredSession();
    void purgeOtherAccounts(api.getCurrentUserId()).catch(() => {}); changed();
    if (navigator.onLine && api.getCurrentUserId()) { void mobileOutbox.flush().catch(() => {}); void attachmentOutbox.flush().catch(() => {}); }
  };
  window.addEventListener('freecollab:auth-changed', sync); window.addEventListener('storage', sync);
  window.addEventListener('online', sync); window.addEventListener('offline', changed);
  window.addEventListener('online', () => { if (api.getCurrentUserId() && !api.isMemorySession()) void offlineLibrary.list().catch(() => {}); });
  window.setInterval(() => { if (navigator.onLine && api.getCurrentUserId()) { void mobileOutbox.flush().catch(() => {}); void attachmentOutbox.flush().catch(() => {}); } }, 15000);
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event as typeof installPrompt; changed(); });
  window.addEventListener('appinstalled', () => { installPrompt = null; changed(); });
  navigator.serviceWorker?.addEventListener('message', event => {
    const data = event.data;
    if (data?.type === 'push_confirmation' && typeof data.subscription_id === 'string' && typeof data.challenge === 'string'
      && /^[a-f0-9]{32}$/.test(data.subscription_id) && /^[A-Za-z0-9_-]{32,128}$/.test(data.challenge)) {
      if (challenges.size > 10) challenges.clear();
      challenges.set(data.subscription_id, { challenge: data.challenge, received: Date.now() }); changed();
    }
  });
  if ('serviceWorker' in navigator && import.meta.env.PROD) {
    try { await navigator.serviceWorker.register('/service-worker.js', { scope: '/', updateViaCache: 'none' }); }
    catch { /* Browser settings may refuse installation; the online app remains usable. */ }
  }
  sync();
}

export function takePushConfirmation(id: string) {
  const result = challenges.get(id); if (!result || Date.now() - result.received > 300000) return null;
  challenges.delete(id); return result.challenge;
}

export async function subscribePush(): Promise<void> {
  const expected = owner();
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) throw new Error('This browser does not support Web Push.');
  const config = await api.request<{ enabled: boolean; application_server_key: string | null }>('/mobile/config');
  assertOwner(expected); if (!config.enabled || !config.application_server_key) throw new Error('Push delivery has not been configured by this installation’s operator.');
  if (await Notification.requestPermission() !== 'granted') throw new Error('Notifications were not enabled. You can change this in browser settings.');
  const existing = await navigator.serviceWorker.getRegistration('/');
  if (!existing) throw new Error('Open the installed production app online once before enabling notifications.');
  const registration = await navigator.serviceWorker.ready; assertOwner(expected);
  const previous = await registration.pushManager.getSubscription(); if (previous) await previous.unsubscribe();
  const raw = atob(config.application_server_key.replace(/-/g, '+').replace(/_/g, '/'));
  const key = Uint8Array.from(raw, character => character.charCodeAt(0));
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  assertOwner(expected);
  const result = await api.request<{ id: string }>('/mobile/subscriptions', { method: 'POST', body: JSON.stringify({ endpoint: subscription.endpoint, keys: subscription.toJSON().keys }) });
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    assertOwner(expected);
    const challenge = takePushConfirmation(result.id);
    if (challenge) { await api.request('/mobile/subscriptions/' + encodeURIComponent(result.id) + '/confirm', { method: 'POST', body: JSON.stringify({ challenge }) }); assertOwner(expected); return; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('The browser confirmation did not arrive. Keep this app open and try again.');
}

export async function disablePush(id: string) {
  const expected = owner(); await api.request('/mobile/subscriptions/' + encodeURIComponent(id), { method: 'DELETE' });
  assertOwner(expected);
}
