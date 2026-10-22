import type { MatrixClient, MatrixEvent, Room } from 'matrix-js-sdk';

type Receipt = { event_id: string };
interface Flight { payload: string; epoch: string; promise: Promise<Receipt> }
const flights = new WeakMap<MatrixClient, Map<string, Flight>>();
const pendingEpochs = new WeakMap<MatrixEvent, string>();

/** Called only after current account, room, membership and device checks. */
export function sendOrResumeEncryptedTransaction(
  client: MatrixClient, room: Room, roomId: string, type: string,
  content: Record<string, unknown>, transaction: string, membershipEpoch: string,
  send: (existing?: MatrixEvent) => Promise<Receipt>,
): Promise<Receipt> {
  let current = flights.get(client);
  if (!current) { current = new Map(); flights.set(client, current); }
  const key = roomId + '\0' + transaction, payload = JSON.stringify([type, content]), active = current.get(key);
  if (active) {
    if (active.payload !== payload || active.epoch !== membershipEpoch) return Promise.reject(new Error('This encrypted transaction is already active with a different content or membership snapshot.'));
    return active.promise;
  }
  const promise = Promise.resolve().then(async () => {
    const existing = room.getEventForTxnId(transaction);
    if (existing) {
      if (existing.getSender() !== client.getUserId() || existing.getRoomId() !== roomId || existing.getType() !== type || JSON.stringify([type, existing.getContent()]) !== payload || existing.isRedacted()) throw new Error('The existing encrypted transaction does not match this request.');
      const id = existing.getId();
      if (existing.status === 'sent' || existing.status === null) {
        if (!existing.isEncrypted() || existing.getWireType() !== 'm.room.encrypted' || !id?.startsWith('$')) throw new Error('The existing transaction has no confirmed encrypted receipt.');
        return { event_id: id };
      }
      if (existing.status !== 'not_sent') throw new Error('This encrypted transaction is already sending or was cancelled. Retry after its current operation finishes.');
      // SDK resendEvent reuses encrypted ciphertext. A fresh epoch must never
      // authorize ciphertext encrypted before a member/device was removed.
      if (pendingEpochs.get(existing) !== membershipEpoch) throw new Error('The pending encrypted transaction belongs to an older or unavailable membership snapshot. Reconcile its history before continuing.');
    }
    const operation = send(existing);
    const pending = room.getEventForTxnId(transaction);
    if (!existing && pending) pendingEpochs.set(pending, membershipEpoch);
    return operation;
  });
  const entry = { payload, epoch: membershipEpoch, promise };
  current.set(key, entry);
  const clear = () => { if (current!.get(key) === entry) current!.delete(key); };
  void promise.then(clear, clear);
  return promise;
}
