/** Last HTTP boundary for consent-sensitive encrypted sends. Never persisted. */
export interface SecurePublishOptions { authorize?: () => void | Promise<void>; signal?: AbortSignal }
interface Guard { authorize: () => void | Promise<void>; current: () => void; signal?: AbortSignal; active: boolean; digest?: string; controllers:Set<AbortController> }
const captureTransaction = (transaction: string) => /^(?:file-)?fc-capture-(?:file|clip|caption)-/.test(transaction);
const cancelled = () => new DOMException('The encrypted publication was cancelled.', 'AbortError');
export class EncryptedPublishGuards {
  private readonly rows = new Map<string, Guard>();
  stop() { for (const guard of this.rows.values()) { guard.active=false; for(const controller of guard.controllers) controller.abort(); } }
  register(room: string, transaction: string, options: SecurePublishOptions, current: () => void) {
    const key = room + '\0' + transaction, previous = this.rows.get(key);
    if (!options.authorize && captureTransaction(transaction)) throw Error('A current recording consent review is required.');
    if (!options.authorize && !options.signal) return () => undefined;
    if (previous?.active) throw Error('This encrypted publication is already being reviewed.');
    if (!previous && this.rows.size >= 5000) throw Error('This encryption session reached its guarded publication limit.');
    const guard: Guard = { authorize: options.authorize || (() => undefined), current, signal: options.signal, active: true, digest: previous?.digest, controllers:new Set() };
    this.rows.set(key, guard);
    return () => { guard.active = false; for(const controller of guard.controllers) controller.abort(); }; // A completed/failed SDK retry stays denied.
  }
  async send<T>(room: string, transaction: string, body: unknown, upstream: AbortSignal | undefined, send: (signal?: AbortSignal) => Promise<T>) {
    const key = room + '\0' + transaction, guard = this.rows.get(key);
    if (!guard) { if (captureTransaction(transaction)) throw Error('Recording publication requires a fresh consent review.'); return send(upstream); }
    const current = () => { if (this.rows.get(key) !== guard || !guard.active) throw Error('This encrypted publication review expired.'); guard.current(); if (guard.signal?.aborted || upstream?.aborted) throw cancelled(); };
    current();
    if (typeof body !== 'string' || body.length > 1024 * 1024) throw Error('The encrypted publication transport is invalid.');
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body)))].map(byte => byte.toString(16).padStart(2, '0')).join('');
    current(); if (guard.digest && guard.digest !== digest) throw Error('The encrypted publication changed during retry.'); guard.digest = digest;
    await guard.authorize(); current();
    const controller = new AbortController(), abort = () => controller.abort();
    guard.controllers.add(controller);
    guard.signal?.addEventListener('abort', abort, {once:true}); upstream?.addEventListener('abort', abort, {once:true});
    try { current(); return await send(controller.signal); }
    finally { guard.controllers.delete(controller); guard.signal?.removeEventListener('abort', abort); upstream?.removeEventListener('abort', abort); }
  }
}
