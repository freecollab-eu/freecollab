import { MeetingConnection, meetingsApi, type MeetingAccess, type MeetingView } from './meetings-api';
import { SFUConnection, sfuApi, type SFUAccess } from './sfu-api';
import type { MeetingCapturePreferences } from './meeting-capture-api';
import { MatrixMeetingRoom } from './federated-meeting';
import type { CaptureAuthority } from './meeting-capture-api';
import { api } from './api';

export interface ActiveMeeting { id: string; channelId: string; transport: 'mesh' | 'livekit' | 'matrix'; view: MeetingView | null; connection: MeetingConnection | SFUConnection; capturePreferences?: MeetingCapturePreferences; matrixRoom?: MatrixMeetingRoom; captureAuthority?: CaptureAuthority }
let active: ActiveMeeting | null = null;
const listeners = new Set<() => void>();
export interface CaptureFinalization {privatePending:boolean;retentionFailed?:boolean}
export interface RecordingDraftNotice {id:number;channelId:string;actor:string;token:string}
let recordingNotice:RecordingDraftNotice|null=null,noticeId=0;
export const currentRecordingDraftNotice=()=>recordingNotice;
export function dismissRecordingDraftNotice(id:number){if(recordingNotice?.id===id){recordingNotice=null;emit();}}
const finalizers = new Map<string, Set<() => Promise<CaptureFinalization|void>>>();
export function registerMeetingFinalizer(id: string, callback: () => Promise<CaptureFinalization|void>) {
  if (!finalizers.has(id)) finalizers.set(id, new Set()); finalizers.get(id)!.add(callback);
  return () => { finalizers.get(id)?.delete(callback); if (!finalizers.get(id)?.size) finalizers.delete(id); };
}
export async function finalizeMeetingCapture(id: string) {
  let timer:ReturnType<typeof setTimeout>;
  try{return await Promise.race([Promise.allSettled([...(finalizers.get(id) || [])].map(callback => callback())).then(rows=>({privatePending:rows.some(row=>row.status==='fulfilled'&&row.value?.privatePending),retentionFailed:rows.some(row=>row.status==='rejected'||row.value?.retentionFailed)})),
    new Promise<CaptureFinalization>(resolve=>{timer=setTimeout(()=>resolve({privatePending:false,retentionFailed:true}),16000);})]);}finally{clearTimeout(timer!);}
}
const emit = () => { for (const listener of listeners) listener(); };
export const subscribeMeeting = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const currentMeeting = () => active;
const update = (connection: ActiveMeeting['connection'], view: MeetingView) => { if (active?.connection === connection) { active = { ...active, view }; emit(); } };
export function startMeshMeeting(channelId: string, access: MeetingAccess, media: MediaStream, capturePreferences?: MeetingCapturePreferences) {
  if (active) throw new Error('Leave the active meeting before starting another.');
  const connection = new MeetingConnection(access.meeting!.id, access, media, view => update(connection, view));
  active = { id: access.meeting!.id, channelId, transport: 'mesh', view: null, connection, capturePreferences }; emit(); connection.start();
}
export function startSFUMeeting(access: SFUAccess, media: MediaStream, capturePreferences?: MeetingCapturePreferences) {
  if (active) throw new Error('Leave the active meeting before starting another.');
  const connection = new SFUConnection(access, media, view => update(connection, view));
  active = { id: access.meeting.id, channelId: access.meeting.channel_id, transport: 'livekit', view: null, connection, capturePreferences }; emit(); void connection.start();
}
export function startMatrixMeeting(room:MatrixMeetingRoom,media:MediaStream,capturePreferences?:MeetingCapturePreferences,captureAuthority?:CaptureAuthority) {
  if(active)throw new Error('Leave the active meeting before starting another.');
  const connection=new MeetingConnection(room.meeting.id,room.access,media,view=>update(connection,view),room);
  active={id:room.meeting.id,channelId:room.channel,transport:'matrix',view:null,connection,matrixRoom:room,capturePreferences,captureAuthority}; emit(); connection.start();
}
export async function leaveActiveMeeting() {
  const call = active; if (!call) return;
  call.view?.local?.getTracks().forEach(track => { track.enabled = false; });
  const finalized=await finalizeMeetingCapture(call.id);
  if (active?.connection !== call.connection) return;
  if(finalized.retentionFailed)throw new Error('Recording storage failed. Download or discard the unsaved recording before leaving.');
  if(finalized.privatePending){const actor=api.getCurrentUserId(),token=api.getAccessToken();if(actor&&token)recordingNotice={id:++noticeId,channelId:call.channelId,actor,token};}
  call.connection.stop(); active = null; emit();
}
export async function endActiveMeeting() {
  const call = active; if (!call) return;
  const finalized=await finalizeMeetingCapture(call.id);
  if (active?.connection !== call.connection) return;
  if(finalized.retentionFailed)throw new Error('Recording storage failed. Download or discard the unsaved recording before leaving.');
  if(finalized.privatePending){const actor=api.getCurrentUserId(),token=api.getAccessToken();if(actor&&token)recordingNotice={id:++noticeId,channelId:call.channelId,actor,token};}
  if (call.transport === 'matrix') await call.matrixRoom!.end(); else if (call.transport === 'livekit') await sfuApi.end(call.id); else await meetingsApi.end(call.id);
  if(active?.connection!==call.connection)return;call.connection.stop();active=null;emit();
}
const discardActiveMeeting = () => { active?.connection.stop(); active = null;recordingNotice=null; finalizers.clear(); emit(); };
window.addEventListener('pagehide', discardActiveMeeting);
window.addEventListener('freecollab:auth-changed', discardActiveMeeting);
