import { MeetingConnection, meetingsApi, type MeetingAccess, type MeetingView } from './meetings-api';
import { SFUConnection, sfuApi, type SFUAccess } from './sfu-api';

export interface ActiveMeeting { id: string; channelId: string; transport: 'mesh' | 'livekit'; view: MeetingView | null; connection: MeetingConnection | SFUConnection }
let active: ActiveMeeting | null = null;
const listeners = new Set<() => void>();
const emit = () => { for (const listener of listeners) listener(); };
export const subscribeMeeting = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const currentMeeting = () => active;
const update = (connection: ActiveMeeting['connection'], view: MeetingView) => { if (active?.connection === connection) { active = { ...active, view }; emit(); } };
export function startMeshMeeting(channelId: string, access: MeetingAccess, media: MediaStream) {
  if (active) throw new Error('Leave the active meeting before starting another.');
  const connection = new MeetingConnection(access.meeting!.id, access, media, view => update(connection, view));
  active = { id: access.meeting!.id, channelId, transport: 'mesh', view: null, connection }; emit(); connection.start();
}
export function startSFUMeeting(access: SFUAccess, media: MediaStream) {
  if (active) throw new Error('Leave the active meeting before starting another.');
  const connection = new SFUConnection(access, media, view => update(connection, view));
  active = { id: access.meeting.id, channelId: access.meeting.channel_id, transport: 'livekit', view: null, connection }; emit(); void connection.start();
}
export function leaveActiveMeeting() { active?.connection.stop(); active = null; emit(); }
export async function endActiveMeeting() { if (!active) return; if (active.transport === 'livekit') await sfuApi.end(active.id); else await meetingsApi.end(active.id); leaveActiveMeeting(); }
window.addEventListener('pagehide', leaveActiveMeeting);
window.addEventListener('freecollab:auth-changed', leaveActiveMeeting);
