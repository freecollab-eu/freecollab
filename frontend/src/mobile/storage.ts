/** Account-isolated browser drafts. Nonextractable keys never leave IndexedDB. */
import { api } from '../lib/api';

const DATABASE = 'freecollab-mobile-v1';
// An embedded session never borrows, purges or persists the standalone app's drafts.
const transient = new Map<string, { owner: string; kind: Kind; id: string; value: unknown }>();
if (typeof window !== 'undefined') window.addEventListener('freecollab:auth-changed', () => { if (api.isMemorySession()) transient.clear(); });
export type Kind = 'draft' | 'outbox' | 'offline-channel' | 'offline-file' | 'attachment-outbox' | 'slack-import' | 'semantic-index' | 'meeting-recording';
interface CipherRecord { id: string; owner: string; kind: Kind; iv: Uint8Array<ArrayBuffer>; ciphertext: ArrayBuffer }
export interface Owner { user: string; token: string }

export function owner(): Owner {
  const user = api.getCurrentUserId(), token = api.getAccessToken();
  if (!user || !token || !api.isMemorySession() && (localStorage.getItem('freecollab_user_id') !== user || localStorage.getItem('freecollab_token') !== token))
    throw new Error('The signed-in account changed.');
  return { user, token };
}
export function assertOwner(expected: Owner) {
  const current = owner();
  if (current.user !== expected.user || current.token !== expected.token) throw new Error('The signed-in account changed.');
}
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore('keys');
      const records = request.result.createObjectStore('vault', { keyPath: 'id' }); records.createIndex('owner', 'owner');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('Private offline storage is unavailable in this browser.'));
  });
}
async function keyFor(db: IDBDatabase, expected: Owner): Promise<CryptoKey> {
  const generated = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  assertOwner(expected);
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('keys', 'readwrite'), store = transaction.objectStore('keys');
    const request = store.get(expected.user); let result: CryptoKey;
    request.onsuccess = () => {
      try { assertOwner(expected); result = request.result || generated; if (!request.result) store.put(result, expected.user); }
      catch { transaction.abort(); }
    };
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = transaction.onabort = () => reject(new Error('Private offline storage could not be unlocked.'));
  });
}
const recordId = (user: string, kind: Kind, id: string) => user + ':' + kind + ':' + id;
const context = (user: string, kind: Kind, id: string) => new TextEncoder().encode('freecollab-mobile-v1\0' + user + '\0' + kind + '\0' + id + (kind === 'draft' || kind === 'outbox' ? '' : '\0' + location.origin));

export async function savePrivate<T>(kind: Kind, id: string, value: T, expected = owner()) {
  if (api.isMemorySession()) { assertOwner(expected); transient.set(recordId(expected.user, kind, id), { owner: expected.user, kind, id, value: structuredClone(value) }); return; }
  const db = await database();
  try {
    const key = await keyFor(db, expected), iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: context(expected.user, kind, id) }, key, new TextEncoder().encode(JSON.stringify(value)));
    assertOwner(expected);
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction('vault', 'readwrite');
      transaction.objectStore('vault').put({ id: recordId(expected.user, kind, id), owner: expected.user, kind, iv, ciphertext } satisfies CipherRecord);
      transaction.oncomplete = () => resolve(); transaction.onerror = transaction.onabort = () => reject(new Error('Your draft could not be stored. Keep this window open.'));
    });
    assertOwner(expected);
  } finally { db.close(); }
}

export async function listPrivate<T>(kind: Kind, expected = owner()): Promise<Array<{ id: string; value: T }>> {
  if (api.isMemorySession()) { assertOwner(expected); return [...transient.values()].filter(row => row.owner === expected.user && row.kind === kind).slice(0, 100).map(row => ({ id: row.id, value: structuredClone(row.value) as T })); }
  const db = await database();
  try {
    const key = await keyFor(db, expected);
    const records = await new Promise<CipherRecord[]>((resolve, reject) => {
      const request = db.transaction('vault').objectStore('vault').index('owner').getAll(expected.user);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(new Error('Offline storage could not be read.'));
    });
    const result = [];
    for (const row of records.filter(row => row.kind === kind).slice(0, kind === 'draft' ? 100 : 100)) {
      const id = row.id.slice(recordId(expected.user, kind, '').length);
      try {
        const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: row.iv, additionalData: context(expected.user, kind, id) }, key, row.ciphertext);
        assertOwner(expected); result.push({ id, value: JSON.parse(new TextDecoder().decode(plain)) as T });
      } catch (error) { assertOwner(expected); if (!(error instanceof DOMException)) throw error; }
    }
    assertOwner(expected); return result;
  } finally { db.close(); }
}

export async function removePrivate(kind: Kind, id: string, expected = owner()) {
  if (api.isMemorySession()) { assertOwner(expected); transient.delete(recordId(expected.user, kind, id)); return; }
  const db = await database();
  try {
    assertOwner(expected);
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction('vault', 'readwrite'); transaction.objectStore('vault').delete(recordId(expected.user, kind, id));
      transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(new Error('Offline item could not be removed.'));
    });
  } finally { db.close(); }
}

export async function purgeOtherAccounts(keep: string | null) {
  if (api.isMemorySession()) { for (const [id, row] of transient) if (row.owner !== keep) transient.delete(id); return; }
  const db = await database();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(['keys', 'vault'], 'readwrite');
      for (const name of ['keys', 'vault']) {
        const request = transaction.objectStore(name).openCursor();
        request.onsuccess = () => {
          const cursor = request.result; if (!cursor) return;
          const current = localStorage.getItem('freecollab_user_id');
          if (current !== keep) { transaction.abort(); return; }
          const user = name === 'keys' ? String(cursor.key) : (cursor.value as CipherRecord).owner;
          if (user !== keep) cursor.delete(); cursor.continue();
        };
      }
      transaction.oncomplete = () => resolve(); transaction.onerror = transaction.onabort = () => reject(new Error('Offline account cleanup was interrupted.'));
    });
  } finally { db.close(); }
}

export async function fingerprint(value: string) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
