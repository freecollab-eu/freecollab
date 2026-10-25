import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { loadEncryptedRoomContext, verifyEncryptedRoomEvent } from './encrypted-work-api';
import type { LiveCaption, LocalCaptionConfiguration } from './meeting-captions';
export interface MeetingCapturePreferences { enabled: boolean; video: boolean; captions: boolean; save_transcript: boolean; language: string;
  caption_audience?: 'admitted'|'channel';
  ai_profile_id?: string; ai_profile_revision?: number; ai_disclose?: boolean }
export interface CaptureState {
  id: string; generation: number; revision: number; state: 'requesting' | 'recording' | 'paused' | 'stopped'; created_by: string;
  video: boolean; captions: boolean; save_transcript: boolean; language: string; all_consented: boolean;
  consented: string[]; awaiting: string[]; participants: Array<{ id: string; name: string }>;
  encrypted_captions?: boolean; live_captions?: LiveCaption[];
  recorder_participant_id: string;
  content_mode?: 'server'|'matrix'|'unknown'; policy_invalidated?: boolean; caption_audience?: 'admitted'|'channel'|null;
  ai?: { enabled: boolean; profile_id: string | null; profile_revision: number | null; recipient: string | null; model: string | null; protocol: string | null; expires_at: string | null; max_segments: number };
}
export interface CaptureStatus { capture: CaptureState | null; participant_id: string; configuration: LocalCaptionConfiguration; publication_receipt?: string; publication_proof?: Record<string, unknown> }
export function assertLiveCaption(value:LiveCaption){if(!value||typeof value!=='object'||Object.keys(value).some(key=>!['start','end','text'].includes(key))||!Number.isFinite(value.start)||!Number.isFinite(value.end)||value.start<0||value.end<value.start||value.end>7200||typeof value.text!=='string'||!value.text.trim()||value.text.length>2000)throw new Error('Invalid live caption window.');}
export interface CaptureAuthority {
  status(): Promise<CaptureStatus>;
  request(preferences: MeetingCapturePreferences): Promise<CaptureStatus>;
  consent(capture: CaptureState, granted: boolean): Promise<CaptureStatus>;
  control(capture: CaptureState, action: 'start' | 'pause' | 'resume' | 'stop' | 'segment', requestId?: string): Promise<CaptureStatus>;
  caption(capture: CaptureState, caption: LiveCaption, signal?:AbortSignal): Promise<void>;
}
export function nativeCaptureAuthority(transport: 'mesh' | 'livekit', meeting: string, channel: string, guestToken?: string): CaptureAuthority {
  const path = guestToken ? '/meeting-access/' + encodeURIComponent(meeting) + '/capture'
    : '/meetings/' + transport + '/' + encodeURIComponent(meeting) + '/capture';
  async function request<T>(suffix = '', body?: unknown): Promise<T> {
    if (!guestToken) return api.request<T>(path + suffix, { ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }) });
    const response = await fetch(api.getBaseUrl() + path + suffix, { method: body === undefined ? 'GET' : 'POST',
      credentials: 'omit', cache: 'no-store', headers: { 'X-Meeting-Token': guestToken, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'The recording request could not be completed.');
    return value as T;
  }
  async function status() {
    const result = await request<CaptureStatus>();
    if (result.capture?.encrypted_captions && !guestToken) {
      const capture = result.capture, secure = await getSecureMessaging(), rows = await secure.events(channel);
      const candidates = rows.filter(row => row.getType() === 'eu.freecollab.meeting.caption'
        && row.getContent().capture_id === capture.id && row.getContent().generation === capture.generation && Date.now() - row.getTs() < 45000&&row.getId()?.startsWith('$')).slice(-4);
      const captions: LiveCaption[] = [];
      for (const row of candidates) {
        const event = await verifyEncryptedRoomEvent(channel, row.getId()!);
        const data = event.content, host = '@fc_' + capture.created_by + ':' + (await secure.roomFor(channel)).c.getUserId()!.split(':').slice(1).join(':');
        if (event.sender !== host || data.protocol!=='freecollab-native-caption/1'||data.caption_audience!=='channel'||data.device_id!==event.sender_device_id||data.recorder_participant_id!==capture.recorder_participant_id||data.meeting_id !== meeting || data.capture_id !== capture.id || data.generation !== capture.generation
            || typeof data.text !== 'string' || data.text.length > 2000 || typeof data.start !== 'number' || typeof data.end !== 'number') continue;
        captions.push({ start: data.start, end: data.end, text: data.text });
      }
      capture.live_captions = captions;
    }
    return result;
  }
  return { status,
    request: preferences => request('', { video: preferences.video, captions: preferences.captions, save_transcript: preferences.save_transcript, language: preferences.language,
      ...(preferences.captions?{caption_audience:preferences.caption_audience}:{}),
      ...(preferences.ai_profile_id ? { ai_profile_id: preferences.ai_profile_id, ai_profile_revision: preferences.ai_profile_revision, ai_disclose: preferences.ai_disclose === true } : {}) }),
    consent: (capture, granted) => request('/consent', { capture_id: capture.id, generation: capture.generation, granted }),
    control: (capture, action, requestId) => request('/control', { capture_id: capture.id, generation: capture.generation, action, ...(requestId ? { request_id: requestId } : {}) }),
    async caption(capture, value, signal) {
      assertLiveCaption(value);
      const authorize=async()=>{
        if(signal?.aborted)throw new DOMException('Caption publication cancelled.','AbortError');
        const next=await request<CaptureStatus>();
        if(next.capture?.id!==capture.id||next.capture.generation!==capture.generation||next.capture.state!=='recording'||!next.capture.all_consented
          ||next.capture.policy_invalidated||next.capture.recorder_participant_id!==next.participant_id||next.capture.caption_audience!==capture.caption_audience
          ||next.capture.content_mode!==capture.content_mode||next.capture.captions!==true)throw new Error('Current recorder, encryption policy and participant consent are required before sharing captions.');
        if(signal?.aborted)throw new DOMException('Caption publication cancelled.','AbortError');
      };
      if (capture.encrypted_captions) {
        if(guestToken||capture.caption_audience!=='channel'||capture.content_mode!=='matrix')throw new Error('Review the encrypted channel caption audience on a verified device.');
        const secure = await getSecureMessaging(),context=await loadEncryptedRoomContext(channel),{c}=await secure.roomFor(channel);
        await authorize();
        const result = await secure.sendEvent(channel, 'eu.freecollab.meeting.caption',
          {protocol:'freecollab-native-caption/1',room_id:context.room_id,connection_id:context.connection_id,device_id:c.getDeviceId(),caption_audience:'channel',
            recorder_participant_id:capture.recorder_participant_id, meeting_id: meeting, capture_id: capture.id, generation: capture.generation, ...value },
          'fc-capture-caption-'+crypto.randomUUID(),{authorize,signal});
        const {roomId } = await secure.roomFor(channel);
        setTimeout(() => { void c.redactEvent(roomId, result.event_id).catch(() => {}); }, 45000);
      } else {
        if(capture.caption_audience!=='admitted'||capture.content_mode!=='server')throw new Error('Review the admitted-participant caption audience again.');
        await authorize();await request('/caption', { capture_id: capture.id, generation: capture.generation, ...value });
      }

    },
  };
}
