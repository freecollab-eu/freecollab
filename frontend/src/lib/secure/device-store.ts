/** Browser-only Rust-store wrapping key. Never transmitted to the application server. */
const DATABASE = 'freecollab-secure-device-v1';
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('keys');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function read(db: IDBDatabase, key: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const request = db.transaction('keys').objectStore('keys').get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function write(db: IDBDatabase, key: string, value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('keys', 'readwrite');
    transaction.objectStore('keys').put(value, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}
export async function storageKeyFor(device: string): Promise<Uint8Array<ArrayBuffer>> {
  const db = await database();
  try {
    let wrapping = await read(db, 'wrapping') as CryptoKey | undefined;
    if (!wrapping) {
      wrapping = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
      await write(db, 'wrapping', wrapping);
    }
    let stored = await read(db, device) as { iv: Uint8Array<ArrayBuffer>; ciphertext: ArrayBuffer } | undefined;
    if (!stored) {
      const key = crypto.getRandomValues(new Uint8Array(32));
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, wrapping, key);
      stored = { iv, ciphertext };
      await write(db, device, stored);
      return key;
    }
    return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: stored.iv }, wrapping, stored.ciphertext));
  } finally { db.close(); }
}

/** Opaque notification pointers only; message bodies and recovery keys are excluded. */
export async function loadPendingNotices(owner: string): Promise<unknown[]> {
  const db = await database();
  try { const value = await read(db, 'notification-pointers:' + owner); return Array.isArray(value) ? value : []; }
  finally { db.close(); }
}
export async function savePendingNotices(owner: string, values: unknown[]): Promise<void> {
  const db = await database();
  try { await write(db, 'notification-pointers:' + owner, values.slice(-1000)); }
  finally { db.close(); }
}
