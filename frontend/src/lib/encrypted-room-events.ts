import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { loadEncryptedRoomContext } from './encrypted-work-api';
import { CandidateFault, trustedEvent } from './federated-work-api';
import { matrixIdentity, type VerifiedWorkEvent } from './federated-work-model';

/** A batch shares only this read's authority snapshots, never cached trust. */
export async function verifyEncryptedRoomEvents(channel: string, eventIds: string[], options: { ignoreInvalid?: boolean } = {}): Promise<VerifiedWorkEvent[]> {
  if (!Array.isArray(eventIds) || eventIds.length > 100 || new Set(eventIds).size !== eventIds.length
      || eventIds.some(id => typeof id !== 'string' || !id.startsWith('$') || id.length > 1024))
    throw new Error('Choose at most 100 distinct encrypted room events.');
  const token = api.getAccessToken(), actor = api.getCurrentUserId(); let live = true;
  const cancel = () => { live = false; }, current = () => {
    if (!live || !token || !actor || token !== api.getAccessToken() || actor !== api.getCurrentUserId())
      throw new Error('The encrypted room account changed.');
  };
  window.addEventListener('freecollab:auth-changed', cancel); window.addEventListener('freecollab:session-expired', cancel);
  const revocations = async () => {
    const value = await api.request<{ revoked_devices: Array<{ matrix_user_id: string; device_id: string }> }>(
      '/secure/devices/revocations?channel_id=' + encodeURIComponent(channel)); current();
    if (!Array.isArray(value.revoked_devices) || value.revoked_devices.length > 20000
        || value.revoked_devices.some(row => !matrixIdentity(row.matrix_user_id) || typeof row.device_id !== 'string' || !row.device_id || row.device_id.length > 128))
      throw new Error('The current device revocations could not be verified.');
    return new Set(value.revoked_devices.map(row => row.matrix_user_id + '\0' + row.device_id));
  };
  try {
    current(); const context = await loadEncryptedRoomContext(channel); current();
    const secure = await getSecureMessaging(), { c, roomId } = await secure.roomFor(channel); current();
    if (roomId !== context.room_id || c.getUserId() !== context.matrix_user_id || !c.getDeviceId()) throw new Error('The encrypted room binding changed.');
    const revoked = await revocations();
    if (revoked.has(context.matrix_user_id + '\0' + c.getDeviceId())) throw new Error('This encryption device was removed.');
    const result: VerifiedWorkEvent[] = [];
    for (const id of eventIds) {
      try {
        const raw = await c.fetchRoomEvent(roomId, id); current();
        if (!matrixIdentity(raw.sender) || !context.members.some(member => member.matrix_user_id === raw.sender)) throw new CandidateFault('The encrypted author is no longer a room participant.');
        const event = await trustedEvent(c, context, { sequence: 0, matrix_event_id: id, matrix_user_id: raw.sender! }, revoked); current();
        if (!event.sender_device_id) throw new CandidateFault('An exact verified encryption device is required.');
        result.push(event);
      } catch (failure) { current(); if (!options.ignoreInvalid || !(failure instanceof CandidateFault || (failure as { httpStatus?: number }).httpStatus === 404)) throw failure; }
    }
    const latest = await loadEncryptedRoomContext(channel); current(); const finalRevoked = await revocations();
    if (latest.connection_id !== context.connection_id || latest.room_id !== roomId || latest.matrix_user_id !== context.matrix_user_id
        || latest.workspace_id !== context.workspace_id || finalRevoked.has(context.matrix_user_id + '\0' + c.getDeviceId())) throw new Error('Encrypted room authority changed during this read.');
    const accepted = result.filter(event => latest.members.some(member => member.matrix_user_id === event.sender) && !finalRevoked.has(event.sender + '\0' + event.sender_device_id));
    if (!options.ignoreInvalid && accepted.length !== result.length) throw new Error('Encrypted room author authority changed during this read.');
    return accepted;
  } finally { window.removeEventListener('freecollab:auth-changed', cancel); window.removeEventListener('freecollab:session-expired', cancel); }
}
