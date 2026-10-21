import { api } from './api';

export interface Meeting { id: string; channel_id: string; host_user_id: string; created_at: string; expires_at: string; ended: boolean; max_participants: number; encryption: 'webrtc-dtls-srtp' }
export interface MeetingParticipant { id: string; name: string; kind: 'member' | 'guest'; state: 'waiting' | 'admitted' | 'removed' | 'left'; media_ready: boolean }
export interface MeetingAccess { meeting_id?: string; meeting?: Meeting; participant: MeetingParticipant; meeting_token: string }
export interface MeetingPoll { meeting_id: string; participant_id: string; state: MeetingParticipant['state']; is_host: boolean; expires_at: string; encryption: 'webrtc-dtls-srtp'; participants: MeetingParticipant[]; signals: Array<{ sequence: number; sender_id: string; payload: { description?: RTCSessionDescriptionInit; candidate?: RTCIceCandidateInit } }>; cursor: number; ice_servers: RTCIceServer[] }
export interface MeetingView { state: 'connecting' | 'waiting' | 'admitted' | 'ended'; host: boolean; participants: MeetingParticipant[]; streams: Map<string, MediaStream>; local: MediaStream | null; error: string; microphone: boolean; camera: boolean; sharing: boolean; connected: number }

const segment = encodeURIComponent;
export const meetingsApi = {
  list: (channel: string) => api.request<Meeting[]>(`/channels/${segment(channel)}/meetings`),
  create: (channel: string) => api.request<MeetingAccess>(`/channels/${segment(channel)}/meetings`, { method: 'POST', body: '{}' }),
  join: (meeting: string) => api.request<MeetingAccess>(`/meetings/${segment(meeting)}/join`, { method: 'POST', body: '{}' }),
  invite: (meeting: string) => api.request<{ invitation_token: string; url: string; expires_at: string }>(`/meetings/${segment(meeting)}/invites`, { method: 'POST', body: '{}' }),
  manage: (meeting: string, participant: string, action: 'admit' | 'remove') => api.request<MeetingParticipant>(`/meetings/${segment(meeting)}/participants/${segment(participant)}/${action}`, { method: 'POST', body: '{}' }),
  end: (meeting: string) => api.request<{ ended: true }>(`/meetings/${segment(meeting)}/end`, { method: 'POST', body: '{}' }),
  guest: (meeting: string, invitation_token: string, name: string) => accessRequest<MeetingAccess>(meeting, '', 'guest', { invitation_token, name }),
  resumeGuest: (meeting: string, token: string) => accessRequest<MeetingAccess>(meeting, token, 'resume', {}),
};

async function accessRequest<T>(meeting: string, token: string, operation: string, body?: unknown, keepalive = false): Promise<T> {
  const response = await fetch(api.getBaseUrl().replace(/\/$/, '') + '/meeting-access/' + segment(meeting) + '/' + operation, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'omit', cache: 'no-store', keepalive,
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Meeting-Token': token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof result?.detail === 'string' ? result.detail : 'The meeting connection could not be completed.');
  return result as T;
}

export function leaveMeeting(meeting: string, token: string) {
  return accessRequest(meeting, token, 'leave', {}, true);
}

/** Native browser WebRTC mesh. HTTP only carries SDP/ICE; never media. */
export class MeetingConnection {
  private peers = new Map<string, { pc: RTCPeerConnection; candidates: RTCIceCandidateInit[]; stream: MediaStream; offering: boolean }>();
  private timeout: ReturnType<typeof setTimeout> | undefined;
  private stopped = false;
  private leaveSent = false;
  private cursor = 0;
  private local: MediaStream | null;
  private screen: MediaStream | null = null;
  private ice: RTCIceServer[] = [];
  private view: MeetingView;
  constructor(readonly meetingId: string, readonly access: MeetingAccess, local: MediaStream | null, private update: (view: MeetingView) => void) {
    this.local = local;
    this.view = { state: 'connecting', host: false, participants: [], streams: new Map(), local, error: '', microphone: !!local?.getAudioTracks().some(t => t.enabled), camera: !!local?.getVideoTracks().some(t => t.enabled), sharing: false, connected: 0 };
  }
  private emit() { if (!this.stopped) this.update({ ...this.view, streams: new Map([...this.peers].map(([id, peer]) => [id, peer.stream])), local: this.screen || this.local }); }
  start() { void this.poll(); }
  private async signal(id: string, payload: { description?: RTCSessionDescriptionInit; candidate?: RTCIceCandidateInit }) {
    if (this.stopped) return;
    await accessRequest(this.meetingId, this.access.meeting_token, 'signal', { recipient_id: id, ...payload });
  }
  private peer(id: string) {
    const existing = this.peers.get(id); if (existing) return existing;
    const pc = new RTCPeerConnection({ iceServers: this.ice });
    const peer = { pc, candidates: [] as RTCIceCandidateInit[], stream: new MediaStream(), offering: false };
    this.peers.set(id, peer);
    // Only the offerer creates transceivers. An answerer attaches its tracks to
    // the offered transceivers after setRemoteDescription; pre-creating them
    // would leave extra, unassociated senders and negotiate receive-only media.
    if (this.access.participant.id.localeCompare(id) < 0) {
      pc.addTransceiver(this.local?.getAudioTracks()[0] || 'audio', { direction: 'sendrecv' });
      pc.addTransceiver((this.screen || this.local)?.getVideoTracks()[0] || 'video', { direction: 'sendrecv' });
    }
    pc.onicecandidate = event => { if (event.candidate) void this.signal(id, { candidate: event.candidate.toJSON() }).catch(() => undefined); };
    pc.ontrack = event => { peer.stream.addTrack(event.track); this.emit(); };
    pc.onconnectionstatechange = () => { this.view.connected = [...this.peers.values()].filter(p => p.pc.connectionState === 'connected').length; this.emit(); };
    pc.onnegotiationneeded = async () => {
      if (this.access.participant.id.localeCompare(id) >= 0 || peer.offering || pc.signalingState !== 'stable' || this.stopped) return;
      peer.offering = true;
      try { await pc.setLocalDescription(await pc.createOffer()); if (pc.localDescription) await this.signal(id, { description: pc.localDescription.toJSON() }); }
      catch { if (!this.stopped) { this.view.error = 'A participant could not connect. Check your network or the configured TURN relay.'; this.emit(); } }
      finally { peer.offering = false; }
    };
    return peer;
  }
  private async applySignal(signal: MeetingPoll['signals'][number]) {
    const peer = this.peer(signal.sender_id), pc = peer.pc;
    if (signal.payload.description) {
      await pc.setRemoteDescription(signal.payload.description);
      for (const candidate of peer.candidates.splice(0)) await pc.addIceCandidate(candidate);
      if (signal.payload.description.type === 'offer') {
        for (const transceiver of pc.getTransceivers()) {
          transceiver.direction = 'sendrecv';
          const track = transceiver.receiver.track.kind === 'audio' ? this.local?.getAudioTracks()[0] : (this.screen || this.local)?.getVideoTracks()[0];
          await transceiver.sender.replaceTrack(track || null);
        }
        await pc.setLocalDescription(await pc.createAnswer());
        if (pc.localDescription) await this.signal(signal.sender_id, { description: pc.localDescription.toJSON() });
      }
    } else if (signal.payload.candidate) {
      if (pc.remoteDescription) await pc.addIceCandidate(signal.payload.candidate);
      else peer.candidates.push(signal.payload.candidate);
    }
  }
  private async poll() {
    if (this.stopped) return;
    try {
      const response = await accessRequest<MeetingPoll>(this.meetingId, this.access.meeting_token, 'poll?after=' + this.cursor);
      if (this.stopped) return;
      this.view.state = response.state === 'waiting' ? 'waiting' : 'admitted';
      this.view.host = response.is_host; this.view.participants = response.participants; this.ice = response.ice_servers;
      const active = new Set(response.participants.filter(row => row.state === 'admitted' && row.media_ready && row.id !== response.participant_id).map(row => row.id));
      for (const [id, peer] of this.peers) if (!active.has(id)) { peer.pc.close(); peer.stream.getTracks().forEach(t => t.stop()); this.peers.delete(id); }
      if (response.state === 'admitted' && this.local) {
        for (const id of active) this.peer(id);
        for (const signal of response.signals) if (active.has(signal.sender_id)) {
          try { await this.applySignal(signal); } catch { this.view.error = 'A participant connection needs to reconnect. Leave and join again if it remains unavailable.'; }
        }
        this.cursor = response.cursor;
      }
      this.emit();
      this.timeout = setTimeout(() => void this.poll(), 750);
    } catch (error) {
      if (this.stopped) return;
      this.view.error = error instanceof Error ? error.message : 'The meeting connection has ended.';
      this.view.state = 'ended'; this.emit(); this.stop(false);
    }
  }
  async enableMedia(camera: boolean) {
    if (this.local || this.stopped) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: camera });
    if (this.stopped) { stream.getTracks().forEach(track => track.stop()); return; }
    try { await accessRequest(this.meetingId, this.access.meeting_token, 'ready', {}); }
    catch (error) { stream.getTracks().forEach(track => track.stop()); throw error; }
    if (this.stopped) { stream.getTracks().forEach(track => track.stop()); return; }
    this.local = stream; this.view.microphone = true; this.view.camera = camera; this.emit();
  }
  toggleMicrophone() { for (const track of this.local?.getAudioTracks() || []) track.enabled = !track.enabled; this.view.microphone = !!this.local?.getAudioTracks().some(t => t.enabled); this.emit(); }
  toggleCamera() { for (const track of this.local?.getVideoTracks() || []) track.enabled = !track.enabled; this.view.camera = !!this.local?.getVideoTracks().some(t => t.enabled); this.emit(); }
  async shareScreen() {
    if (!this.local || this.stopped) return;
    if (this.screen) { await this.stopSharing(); return; }
    if (!navigator.mediaDevices.getDisplayMedia) throw new Error('Screen sharing is unavailable in this browser.');
    const screen = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    if (this.stopped) { screen.getTracks().forEach(t => t.stop()); return; }
    this.screen = screen;
    const track = screen.getVideoTracks()[0];
    track.onended = () => void this.stopSharing();
    try { await Promise.all([...this.peers.values()].map(peer => peer.pc.getTransceivers().find(transceiver => transceiver.receiver.track.kind === 'video')?.sender.replaceTrack(track))); }
    catch (error) { await this.stopSharing(); throw error; }
    this.view.sharing = true; this.emit();
  }
  private async stopSharing() {
    const screen = this.screen; this.screen = null;
    if (screen) { for (const track of screen.getTracks()) { track.onended = null; track.stop(); } }
    await Promise.all([...this.peers.values()].map(peer => peer.pc.getTransceivers().find(transceiver => transceiver.receiver.track.kind === 'video')?.sender.replaceTrack(this.local?.getVideoTracks()[0] || null)));
    this.view.sharing = false; this.emit();
  }
  stop(notify = true) {
    // A failed poll already closes local media. An explicit later Leave must
    // still release server admission after the network has recovered.
    if (notify && !this.leaveSent) {
      this.leaveSent = true;
      void leaveMeeting(this.meetingId, this.access.meeting_token).catch(() => { this.leaveSent = false; });
    }
    if (this.stopped) return;
    this.stopped = true; clearTimeout(this.timeout);
    for (const peer of this.peers.values()) { peer.pc.close(); peer.stream.getTracks().forEach(track => track.stop()); }
    this.peers.clear(); this.local?.getTracks().forEach(track => track.stop()); this.screen?.getTracks().forEach(track => track.stop());
  }
}
