import { Room, RoomEvent, Track } from 'livekit-client';
import { api } from './api';
import type { MeetingView, MeetingParticipant } from './meetings-api';

export interface SFURoom { id: string; channel_id: string; host_user_id: string; expires_at: string; max_participants: number; transport: 'livekit'; encryption: 'sfu-dtls-srtp' }
export interface SFUStatus { provider: 'disabled' | 'livekit'; configured: boolean; ready: boolean; reason: string; max_participants: number }
export interface SFUAccess { meeting: SFURoom; participant_id: string; url: string; token: string; token_expires_in: number }
const path = (id: string) => '/large-meetings/' + encodeURIComponent(id);
export const sfuApi = {
  list: (channel: string) => api.request<{ status: SFUStatus; rooms: SFURoom[] }>('/channels/' + encodeURIComponent(channel) + '/large-meetings'),
  create: (channel: string) => api.request<SFURoom>('/channels/' + encodeURIComponent(channel) + '/large-meetings', { method: 'POST', body: '{}' }),
  join: (id: string) => api.request<SFUAccess>(path(id) + '/join', { method: 'POST', body: '{}' }),
  heartbeat: (id: string, participant: string) => api.request(path(id) + '/participants/' + encodeURIComponent(participant) + '/heartbeat', { method: 'POST', body: '{}' }),
  leave: (id: string, participant: string) => api.request(path(id) + '/participants/' + encodeURIComponent(participant) + '/leave', { method: 'POST', body: '{}', keepalive: true }),
  end: (id: string) => api.request(path(id) + '/end', { method: 'POST', body: '{}' }),
  remove: (id: string, participant: string) => api.request(path(id) + '/participants/' + encodeURIComponent(participant) + '/remove', { method: 'POST', body: '{}' }),
};

/** Actual LiveKit subscription/publication, never a larger browser mesh. */
export class SFUConnection {
  private room = new Room({ adaptiveStream: true, dynacast: true });
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private screen: MediaStream | null = null;
  private view: MeetingView;
  constructor(readonly access: SFUAccess, private local: MediaStream, private update: (view: MeetingView) => void) {
    this.view = { state: 'connecting', host: access.meeting.host_user_id === api.getCurrentUserId(), participants: [], streams: new Map(), local, error: '', microphone: true, camera: local.getVideoTracks().length > 0, sharing: false, connected: 0 };
  }
  private emit() {
    if (this.stopped) return;
    const streams = new Map<string, MediaStream>(), participants: MeetingParticipant[] = [];
    for (const participant of this.room.remoteParticipants.values()) {
      participants.push({ id: participant.identity, name: participant.name || participant.identity, kind: 'member', state: 'admitted', media_ready: true });
      const publications = [...participant.trackPublications.values()].filter(item => item.track && item.isSubscribed);
      const screen = publications.find(item => item.source === Track.Source.ScreenShare)?.track?.mediaStreamTrack;
      const camera = publications.find(item => item.source === Track.Source.Camera)?.track?.mediaStreamTrack;
      const audio = publications.filter(item => item.kind === Track.Kind.Audio).map(item => item.track!.mediaStreamTrack);
      streams.set(participant.identity, new MediaStream([...audio, ...(screen || camera ? [screen || camera!] : [])]));
    }
    this.view.participants = participants; this.view.streams = streams; this.view.connected = participants.length;
    this.update({ ...this.view, local: this.screen || this.local });
  }
  async start() {
    for (const event of [RoomEvent.TrackSubscribed, RoomEvent.TrackUnsubscribed, RoomEvent.ParticipantConnected, RoomEvent.ParticipantDisconnected]) this.room.on(event, () => this.emit());
    this.room.on(RoomEvent.Disconnected, () => { if (!this.stopped) { this.view.state = 'ended'; this.view.error = 'The SFU connection ended. Leave and rejoin to request fresh access.'; this.emit(); this.stop(); } });
    try {
      await this.room.connect(this.access.url, this.access.token, { autoSubscribe: true });
      if (this.stopped) { void this.room.disconnect(); return; }
      for (const track of this.local.getTracks()) await this.room.localParticipant.publishTrack(track, { source: track.kind === 'audio' ? Track.Source.Microphone : Track.Source.Camera });
      this.view.state = 'admitted'; this.emit(); void this.heartbeat();
    } catch (error) { this.view.state = 'ended'; this.view.error = error instanceof Error ? error.message : 'The configured SFU could not connect.'; this.emit(); this.stop(); }
  }
  private async heartbeat() {
    if (this.stopped) return;
    try { await sfuApi.heartbeat(this.access.meeting.id, this.access.participant_id); if (!this.stopped) this.timer = setTimeout(() => void this.heartbeat(), 5000); }
    catch (error) { if (!this.stopped) { this.view.state = 'ended'; this.view.error = error instanceof Error ? error.message : 'Your meeting access ended.'; this.emit(); this.stop(); } }
  }
  toggleMicrophone() { for (const track of this.local.getAudioTracks()) track.enabled = !track.enabled; this.view.microphone = this.local.getAudioTracks().some(track => track.enabled); this.emit(); }
  toggleCamera() { for (const track of this.local.getVideoTracks()) track.enabled = !track.enabled; this.view.camera = this.local.getVideoTracks().some(track => track.enabled); this.emit(); }
  async shareScreen() {
    await this.room.localParticipant.setScreenShareEnabled(!this.view.sharing, { audio: false });
    const track = this.room.localParticipant.getTrackPublication(Track.Source.ScreenShare)?.track;
    this.screen = track ? new MediaStream([track.mediaStreamTrack]) : null; this.view.sharing = !!track;
    if (track) track.mediaStreamTrack.addEventListener('ended', () => { this.screen = null; this.view.sharing = false; this.emit(); }, { once: true });
    this.emit();
  }
  stop() {
    if (this.stopped) return; this.stopped = true; clearTimeout(this.timer);
    void this.room.disconnect(); this.local.getTracks().forEach(track => track.stop()); this.screen?.getTracks().forEach(track => track.stop());
    void sfuApi.leave(this.access.meeting.id, this.access.participant_id).catch(() => undefined);
  }
}
