/** Discovery retries contain opaque references only, never content or bearer keys. */
export interface PendingWorkPointer { key: string; owner: string; session_digest: string; channel_id: string; connection_id: string; matrix_event_id: string; created_at: number }
const NAME = 'freecollab-federated-work-pointers-v1';
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => { const request = indexedDB.open(NAME, 1); request.onupgradeneeded = () => request.result.createObjectStore('pointers', { keyPath: 'key' }); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(new Error('Encrypted work discovery retries could not be stored. Keep this window open.')); });
}
export async function pointerSessionDigest(token: string) { const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))); return [...hash].map(byte => byte.toString(16).padStart(2, '0')).join(''); }
export async function pendingWorkPointers(owner: string, digest: string) {
  const db = await database();
  try { return await new Promise<PendingWorkPointer[]>((resolve, reject) => { const request = db.transaction('pointers').objectStore('pointers').getAll(); request.onsuccess = () => resolve((request.result as PendingWorkPointer[]).filter(row => row.owner === owner && row.session_digest === digest).slice(0, 200)); request.onerror = () => reject(new Error('Encrypted work discovery retries could not be read.')); }); }
  finally { db.close(); }
}
export async function storeWorkPointer(row: PendingWorkPointer) {
  const db = await database();
  try { await new Promise<void>((resolve, reject) => { const transaction = db.transaction('pointers', 'readwrite'), store = transaction.objectStore('pointers'); const count = store.count(); count.onsuccess = () => { if (count.result >= 200) transaction.abort(); else store.put(row); }; transaction.oncomplete = () => resolve(); transaction.onabort = transaction.onerror = () => reject(new Error('Encrypted work discovery retry storage is full or unavailable. Keep this window open and retry.')); }); }
  finally { db.close(); }
}
export async function removeWorkPointer(key: string) { const db = await database(); try { await new Promise<void>((resolve, reject) => { const transaction = db.transaction('pointers', 'readwrite'); transaction.objectStore('pointers').delete(key); transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(new Error('The discovery retry could not be cleared.')); }); } finally { db.close(); } }
if (typeof window !== 'undefined') window.addEventListener('freecollab:auth-changed', () => { void database().then(db => { const transaction = db.transaction('pointers', 'readwrite'); transaction.objectStore('pointers').clear(); transaction.oncomplete = transaction.onabort = () => db.close(); }).catch(() => undefined); });
