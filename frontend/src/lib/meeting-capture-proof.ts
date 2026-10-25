import type { MatrixEvent } from 'matrix-js-sdk';
import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { loadEncryptedRoomContext } from './encrypted-work-api';
import { verifyEncryptedRoomEvents } from './encrypted-room-events';
/** Matrix JSON object key order is not part of a signed event's meaning. */
export function sameRecordingProof(left:unknown,right:unknown):boolean{
  const canonical=(value:unknown):unknown=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'
    ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical((value as Record<string,unknown>)[key])])):value;
  return JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
}

export interface NativeRecordingProof {
  protocol: 'freecollab-native-recording/1'; capture_id: string; generation: number; receipt_id: string; clip_id: string;
  room_id: string; connection_id: string; recorder_device_id: string;
  content_mode:'matrix';caption_audience:'channel';
}
interface Publication { capture_id: string; generation: number; receipt_id: string; clip_id: string; created_by: string; consent_authority: string;content_mode:string;caption_audience:string }
const base = (channel: string, capture: string) => '/channels/' + encodeURIComponent(channel) + '/meeting-captures/' + encodeURIComponent(capture);
export async function authorizeNativeRecording(channel: string, capture: string, receipt: string, clip: string) {
  return api.request<Publication>(base(channel, capture) + '/publication', { method: 'POST', body: JSON.stringify({ receipt_id: receipt, clip_id: clip }) });
}
export async function nativeRecordingProof(channel: string, capture: string, generation: number, receipt: string, clip: string): Promise<NativeRecordingProof> {
  const token = api.getAccessToken(), context = await loadEncryptedRoomContext(channel), secure = await getSecureMessaging();
  const { c, roomId } = await secure.roomFor(channel);
  if (context.mode !== 'local' || context.room_id !== roomId || context.matrix_user_id !== c.getUserId() || !c.getDeviceId()
      || token !== api.getAccessToken()) throw new Error('Native recording consent belongs to a local encrypted room.');
  return { protocol: 'freecollab-native-recording/1', capture_id: capture, generation, receipt_id: receipt,
    clip_id: clip, room_id: roomId, connection_id: context.connection_id, recorder_device_id: c.getDeviceId()!,content_mode:'matrix',caption_audience:'channel' };
}
function parseProof(value: unknown): NativeRecordingProof {
  const proof = value as NativeRecordingProof;
  if (!proof || typeof proof !== 'object' || Array.isArray(proof)
      || Object.keys(proof).some(key => !['protocol','capture_id','generation','receipt_id','clip_id','room_id','connection_id','recorder_device_id','content_mode','caption_audience'].includes(key))
      || proof.protocol !== 'freecollab-native-recording/1' || !/^[a-f0-9]{32}$/.test(proof.capture_id)
      || !/^[a-f0-9]{32}$/.test(proof.receipt_id) || !/^[a-f0-9]{32}$/.test(proof.connection_id)
      || !/^[A-Za-z0-9_-]{1,80}$/.test(proof.clip_id) || !Number.isSafeInteger(proof.generation) || proof.generation < 1
      || typeof proof.room_id !== 'string' || !proof.room_id.startsWith('!') || proof.room_id.length > 1024
      || typeof proof.recorder_device_id !== 'string' || !proof.recorder_device_id || proof.recorder_device_id.length > 128||proof.content_mode!=='matrix'||proof.caption_audience!=='channel')
    throw new Error('The encrypted recording consent proof is invalid.');
  return proof;
}
export async function verifyEncryptedRecordingFile(channelId: string, event: MatrixEvent): Promise<void> {
  if(event.getContent().meeting_capture?.protocol==='freecollab-matrix-recording/1'){
    const {verifyMatrixRecordingProof}=await import('./matrix-meeting-capture'),{proof,host}=await verifyMatrixRecordingProof(channelId,event.getContent().meeting_capture);
    const [actual]=await verifyEncryptedRoomEvents(channelId,[event.getId()!]);
    if(actual.sender!==host.sender||actual.sender_device_id!==proof.recorder_device_id
      ||!sameRecordingProof(actual.content.meeting_capture,proof))throw new Error('The encrypted recording file author differs from its consent frontier.');
    return;
  }
  const token = api.getAccessToken(), actor = api.getCurrentUserId(), proof = parseProof(event.getContent().meeting_capture);
  const context = await loadEncryptedRoomContext(channelId);
  if (context.mode !== 'local' || context.connection_id !== proof.connection_id || context.room_id !== proof.room_id
      || event.getRoomId() !== proof.room_id || !event.getId()) throw new Error('The recording consent belongs to another room.');
  const [verified] = await verifyEncryptedRoomEvents(channelId, [event.getId()!]);
  if (!sameRecordingProof(parseProof(verified.content.meeting_capture),proof)) throw new Error('The authenticated recording proof differs from this file.');
  const receipt = await api.request<Publication>(base(channelId, proof.capture_id) + '/publications/' + encodeURIComponent(proof.receipt_id));
  if (receipt.capture_id !== proof.capture_id || receipt.generation !== proof.generation || receipt.receipt_id !== proof.receipt_id
      || receipt.clip_id !== proof.clip_id || receipt.consent_authority !== 'native-authenticated-participants'||receipt.content_mode!=='matrix'||receipt.caption_audience!=='channel'
      || verified.sender !== '@fc_' + receipt.created_by + ':' + context.local_server || verified.sender_device_id !== proof.recorder_device_id
      || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) throw new Error('The recording consent or verified recording device changed.');
}
