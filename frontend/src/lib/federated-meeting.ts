import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { EncryptedWorkClient, loadEncryptedRoomContext, publishEncryptedRoomPointer, type EncryptedWorkContext, type VerifiedIndexedWorkEvent } from './encrypted-work-api';
import { verifyEncryptedRoomEvents } from './encrypted-room-events';
import { meetingAdmissions, meetingScoped } from './matrix-meeting-frontier';
import type { VerifiedWorkEvent } from './federated-work-model';
import type { Meeting, MeetingAccess, MeetingParticipant, MeetingPoll, MeetingTransport } from './meetings-api';

export const MATRIX_MEETING_PROTOCOL = 'freecollab-matrix-meeting/1';
export const MATRIX_MEETING_EVENT = 'eu.freecollab.meeting';
type Data = Record<string, unknown>;
export interface MatrixMeeting extends Meeting { transport: 'matrix'; proposal_event_id: string; host_matrix_id: string; host_device_id: string }
const hash = async (value: string) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))].map(byte => byte.toString(16).padStart(2,'0')).join('').slice(0,32);
const participantId = (sender: string, device: string, nonce: string) => hash(sender + '\0' + device + '\0' + nonce);
const scoped = (event: VerifiedWorkEvent, context: EncryptedWorkContext, meeting?: string) => {
  const data = event.content;
  return event.type === MATRIX_MEETING_EVENT && event.sender_device_id && data.protocol === MATRIX_MEETING_PROTOCOL
    && data.room_id === context.room_id && data.connection_id === context.connection_id && data.device_id === event.sender_device_id
    && (!meeting || data.meeting_id === meeting) && event.timestamp <= Date.now() + 30000;
};
function proposal(event: VerifiedWorkEvent, context: EncryptedWorkContext): MatrixMeeting {
  const data = event.content;
  if (!scoped(event,context) || data.kind !== 'proposal' || typeof data.meeting_id !== 'string' || !/^[a-f0-9-]{36}$/.test(data.meeting_id)
      || typeof data.participant_id !== 'string' || !/^[a-f0-9]{32}$/.test(data.participant_id) || typeof data.nonce !== 'string' || !/^[a-f0-9-]{36}$/.test(data.nonce)
      || typeof data.expires_at !== 'number' || data.expires_at <= event.timestamp || data.expires_at > event.timestamp + 7200000 || data.expires_at <= Date.now())
    throw new Error('The encrypted meeting proposal is unavailable or expired.');
  return { id: event.event_id, proposal_event_id: event.event_id, channel_id: context.channel_id, host_user_id: event.sender,
    host_matrix_id: event.sender, host_device_id: event.sender_device_id!, created_at: new Date(event.timestamp).toISOString(),
    expires_at: new Date(data.expires_at).toISOString(), ended: false, max_participants: 6, encryption:'webrtc-dtls-srtp', transport:'matrix' };
}
export async function listMatrixMeetings(channel: string): Promise<MatrixMeeting[]> {
  const client = new EncryptedWorkClient(channel);
  try {
    const context = await loadEncryptedRoomContext(channel);
    if (!context.room_protocols.includes(MATRIX_MEETING_PROTOCOL)) throw new Error('Both installations must enable encrypted meeting signaling.');
    const events = await client.verifiedEvents([MATRIX_MEETING_EVENT]), ended = new Set<string>();
    for (const event of events) if (scoped(event,context) && event.content.kind === 'end') {
      const original = events.find(row => row.event_id === event.content.proposal_event_id);
      if (original && event.sender === original.sender && event.sender_device_id === original.sender_device_id) ended.add(original.event_id);
    }
    const result: MatrixMeeting[] = [];
    for (const event of events) if (event.content.kind === 'proposal' && !ended.has(event.event_id)) {
      try { const row = proposal(event,context); if (await participantId(event.sender,event.sender_device_id!,event.content.nonce as string) === event.content.participant_id) result.push(row); } catch { /* invalid proposals remain quarantined */ }
    }
    return result.sort((a,b) => b.created_at.localeCompare(a.created_at));
  } finally { client.stop(); }
}
export async function getMatrixMeeting(channel:string,id:string):Promise<MatrixMeeting>{const context=await loadEncryptedRoomContext(channel),[event]=await verifyEncryptedRoomEvents(channel,[id]);return proposal(event,context);}

/** Verified Matrix carries signaling only. The existing WebRTC engine carries media. */
export class MatrixMeetingRoom implements MeetingTransport {
  readonly pollInterval = 2500;
  readonly access: MeetingAccess;
  private joinedAt = 0; private ready = true; private left = false; private stopped = false;
  private sequence = 0; private delivered = new Set<string>(); private latest: MeetingParticipant[] = [];
  private lastIndexed:VerifiedIndexedWorkEvent[]=[]; private lastJoin = ''; private joinEvent=''; private indexClient:EncryptedWorkClient; private identities = new Map<string,VerifiedWorkEvent>();
  private pollTail:Promise<void>=Promise.resolve();
  private pendingPolls=new Map<boolean,Promise<MeetingPoll>>();
  private ownerToken = api.getAccessToken(); private ownerActor = api.getCurrentUserId();
  private constructor(readonly channel: string, readonly context: EncryptedWorkContext, readonly meeting: MatrixMeeting,
    private readonly proposal: VerifiedWorkEvent, readonly participant: string, private readonly nonce: string,
    readonly matrixUser: string, readonly device: string, private readonly ice: RTCIceServer[]) {
    this.indexClient=new EncryptedWorkClient(channel);
    this.access = { meeting, participant: { id: participant, name: context.members.find(row => row.matrix_user_id === matrixUser)?.name || 'Participant', kind:'member',state:'waiting',media_ready:true }, meeting_token: '' };
  }
  private current() { if (this.stopped || !this.ownerToken || this.ownerToken !== api.getAccessToken() || this.ownerActor !== api.getCurrentUserId()) throw new Error('The encrypted meeting session changed.'); }
  static async open(channel: string, id?: string): Promise<MatrixMeetingRoom> {
    const token = api.getAccessToken(), context = await loadEncryptedRoomContext(channel), secure = await getSecureMessaging(), { c, roomId } = await secure.roomFor(channel);
    if (!context.room_protocols.includes(MATRIX_MEETING_PROTOCOL) || roomId !== context.room_id || c.getUserId() !== context.matrix_user_id || !c.getDeviceId())
      throw new Error('Both installations and this verified device must support encrypted meeting signaling.');
    const device = c.getDeviceId()!, nonce = crypto.randomUUID(), own = await participantId(context.matrix_user_id, device, nonce);
    let event: VerifiedWorkEvent;
    if (!id) {
      const result = await secure.sendEvent(channel,MATRIX_MEETING_EVENT,{ protocol:MATRIX_MEETING_PROTOCOL,kind:'proposal',room_id:roomId,connection_id:context.connection_id,
        meeting_id:crypto.randomUUID(),device_id:device,nonce,participant_id:own,expires_at:Date.now()+7190000 });
      await publishEncryptedRoomPointer(channel,result.event_id); [event] = await verifyEncryptedRoomEvents(channel,[result.event_id]);
    } else [event] = await verifyEncryptedRoomEvents(channel,[id]);
    const meeting = proposal(event,context);
    if (await participantId(event.sender,event.sender_device_id!,event.content.nonce as string) !== event.content.participant_id) throw new Error('The meeting host device identity is invalid.');
    const configuration = await api.request<{ice_servers:RTCIceServer[]}>('/channels/' + encodeURIComponent(channel) + '/meeting-media-configuration');
    if (token !== api.getAccessToken()) throw new Error('The encrypted meeting account changed.');
    // Rejoining a host proposal from another browser device does not inherit its authority.
    const host = !id;
    const room = new MatrixMeetingRoom(channel,context,meeting,event,host ? event.content.participant_id as string : own,host ? event.content.nonce as string : nonce,context.matrix_user_id,device,configuration.ice_servers);
    await room.heartbeat(); return room;
  }
  private async send(kind: string, fields: Data = {}, durable = false, options?:{transactionId?:string;authorize?:()=>void|Promise<void>;signal?:AbortSignal}) {
    if(Object.keys(fields).some(key=>['protocol','room_id','connection_id','meeting_id','proposal_event_id','device_id','participant_id','kind'].includes(key)))throw new Error('Meeting event scope cannot be overridden.');
    this.current(); const secure = await getSecureMessaging(); this.current();
    const next = await loadEncryptedRoomContext(this.channel); this.current();
    if (next.connection_id !== this.context.connection_id || next.room_id !== this.context.room_id || next.matrix_user_id !== this.matrixUser
        || !next.room_protocols.includes(MATRIX_MEETING_PROTOCOL)) throw new Error('The encrypted meeting room changed.');
    const result = await secure.sendEvent(this.channel,MATRIX_MEETING_EVENT,{ protocol:MATRIX_MEETING_PROTOCOL,room_id:this.context.room_id,connection_id:this.context.connection_id,
      meeting_id:this.proposal.content.meeting_id,proposal_event_id:this.proposal.event_id,device_id:this.device,participant_id:this.participant,kind,...fields },options?.transactionId,{authorize:options?.authorize,signal:options?.signal}); this.current();
    if (durable) await publishEncryptedRoomPointer(this.channel,result.event_id); return result.event_id;
  }
  private async heartbeat() { if(!this.joinEvent){this.joinEvent=await this.send('join',{nonce:this.nonce,ready:this.ready},true);this.lastJoin=this.joinEvent;}else this.lastJoin=await this.send('beat',{nonce:this.nonce,ready:this.ready,join_event_id:this.joinEvent}); this.joinedAt=Date.now(); }
  captureLeaseCurrent(){this.current();return Date.now()-this.joinedAt<35000;}
  async publishCapture(kind:string,fields:Data,options?:{transactionId?:string;authorize?:()=>void|Promise<void>;signal?:AbortSignal}) { if(!kind.startsWith('capture-')||JSON.stringify(fields).length>24000)throw new Error('Invalid encrypted capture event.'); if(kind==='capture-caption'&&(!options?.authorize||!options.transactionId?.startsWith('fc-capture-caption-')))throw new Error('Live captions require a current publication authorization.');return this.send(kind,fields,kind!=='capture-caption',options); }
  async captureSnapshot() { await this.poll(false);this.current(); return {context:this.context,proposal:this.proposal,participant:this.participant,device:this.device,
    host:this.isHost(),people:this.latest.map(row=>({...row})),identities:new Map(this.identities),indexed:[...this.lastIndexed]}; }
  async manage(id: string, action:'admit'|'remove') {
    if (!this.isHost()) throw new Error('Only the exact verified host device can manage admission.');
    if (!this.latest.some(row => row.id === id) || action === 'admit' && this.latest.filter(row => row.state === 'admitted').length >= 6) throw new Error('The participant is unavailable or this six-device room is full.');
    const join=this.identities.get(id);if(!join)throw new Error('The admitted device has no verified join.');await this.send(action,{target_id:id,join_event_id:join.event_id},true);
  }
  isHost() { return this.matrixUser === this.proposal.sender && this.device === this.proposal.sender_device_id && this.participant === this.proposal.content.participant_id; }
  async end() { if (!this.isHost()) throw new Error('Only the exact verified host device can end this meeting.'); await this.send('end',{},true); }
  async request<T>(operation:string,body?:unknown):Promise<T> {
    this.current();
    if (operation.startsWith('poll')) return await this.poll() as T;
    if (operation === 'ready') { this.ready=true; await this.heartbeat(); return {} as T; }
    if (operation === 'leave') { if (!this.left) { if (this.isHost()) await this.end(); else await this.send('leave',{join_event_id:this.joinEvent},true); this.left=true; } this.stopped=true;this.indexClient.stop(); return {} as T; }
    if (operation === 'signal') {
      const value=body as Data;
      if (!this.latest.some(row => row.id===value.recipient_id && row.state==='admitted' && row.media_ready)
          || !this.latest.some(row => row.id===this.participant && row.state==='admitted') || JSON.stringify(value).length>36000) throw new Error('Signals require two currently admitted verified devices.');
      await this.send('signal',value); return {} as T;
    }
    throw new Error('Unsupported encrypted meeting operation.');
  }
  private poll(consumeSignals=true):Promise<MeetingPoll> {
    const pending=this.pendingPolls.get(consumeSignals);if(pending)return pending;
    const operation=this.pollTail.then(()=>this.performPoll(consumeSignals));
    this.pendingPolls.set(consumeSignals,operation);this.pollTail=operation.then(()=>undefined,()=>undefined);
    const clear=()=>{if(this.pendingPolls.get(consumeSignals)===operation)this.pendingPolls.delete(consumeSignals);};void operation.then(clear,clear);
    return operation;
  }
  private async performPoll(consumeSignals=true):Promise<MeetingPoll> {
    this.current();if(Date.now()>=Date.parse(this.meeting.expires_at))throw new Error('This encrypted meeting expired.');
    if(Date.now()-this.joinedAt>8000)await this.heartbeat();
    const indexed=await this.indexClient.verifiedIndexedEvents([MATRIX_MEETING_EVENT]);this.current();
    const frontier=await meetingAdmissions(indexed,this.proposal);if(frontier.ended)throw new Error('The host ended this encrypted meeting.');
    const secure=await getSecureMessaging(),timeline=await secure.events(this.channel);this.current();
    const candidates=timeline.filter(row=>row.getType()===MATRIX_MEETING_EVENT&&row.getContent().meeting_id===this.proposal.content.meeting_id&&row.getId()?.startsWith('$'));
    const beats=new Map<string,typeof candidates[number]>(),signals=[];
    for(const row of candidates){const d=row.getContent();if(d.kind==='beat'&&Date.now()-row.getTs()<35000)beats.set((row.getSender()||'')+'\0'+d.device_id,row);
      if(consumeSignals&&d.kind==='signal'&&d.recipient_id===this.participant&&!this.delivered.has(row.getId()!)&&Date.now()-row.getTs()<60000)signals.push(row);}
    const ids=[...new Set([this.lastJoin,...[...beats.values(),...signals].map(row=>row.getId()!)].filter(Boolean))];
    if(ids.length>100)throw new Error('Encrypted meeting signaling exceeded its bounded device window.');
    const verified=await verifyEncryptedRoomEvents(this.channel,ids,{ignoreInvalid:true});this.current();
    const rows=verified.filter(row=>meetingScoped(row,this.proposal));
    const heartbeat=(join:VerifiedWorkEvent)=>rows.filter(row=>row.content.kind==='beat'&&row.sender===join.sender&&row.sender_device_id===join.sender_device_id
      &&row.content.join_event_id===join.event_id&&row.content.participant_id===join.content.participant_id).sort((a,b)=>a.timestamp-b.timestamp||a.event_id.localeCompare(b.event_id)).slice(-1)[0]||join;
    const left=(join:VerifiedWorkEvent)=>indexed.some(row=>meetingScoped(row,this.proposal)&&row.content.kind==='leave'&&row.sender===join.sender&&row.sender_device_id===join.sender_device_id
      &&row.content.join_event_id===join.event_id&&row.content.participant_id===join.content.participant_id);
    if(this.isHost()){
      const stale=[...frontier.active.values()].filter(row=>row.participant_id!==this.participant&&(Date.now()-heartbeat(row.join).timestamp>=35000||left(row.join)));
      if(stale.length){for(const row of stale)await this.send('remove',{target_id:row.participant_id,join_event_id:row.join_event_id},true);return this.performPoll(consumeSignals);}
    }
    const people:MeetingParticipant[]=[],identities=new Map<string,VerifiedWorkEvent>();
    const knownJoins=indexed.filter(row=>meetingScoped(row,this.proposal)&&row.content.kind==='join');
    const previous=new Set(frontier.admitted.map(row=>row.device.participant_id));
    for(const join of knownJoins){const d=join.content;if(typeof d.nonce!=='string'||typeof d.participant_id!=='string'||await participantId(join.sender,join.sender_device_id!,d.nonce)!==d.participant_id)continue;
      const id=d.participant_id,last=heartbeat(join),active=frontier.active.get(id);
      if(!active&&(previous.has(id)||Date.now()-last.timestamp>=35000))continue;
      const state=active?left(join)||Date.now()-last.timestamp>=35000?'left':'admitted':'waiting';
      if(identities.has(id))continue;identities.set(id,join);
      people.push({id,name:this.context.members.find(member=>member.matrix_user_id===join.sender)?.name||join.sender,kind:'member',state,media_ready:last.content.ready===true});
    }
    const host=frontier.active.get(this.proposal.content.participant_id as string);
    if(!host||Date.now()-heartbeat(host.join).timestamp>=35000)throw new Error('The verified host device disconnected.');
    const own=people.find(row=>row.id===this.participant);if(!own||own.state==='left')throw new Error('This device is no longer admitted to the encrypted meeting.');
    const accepted:MeetingPoll['signals']=[];
    for(const row of rows.filter(event=>consumeSignals&&event.content.kind==='signal')){const d=row.content,sender=identities.get(d.participant_id as string);
      if(!sender||sender.sender!==row.sender||sender.sender_device_id!==row.sender_device_id||!people.some(person=>person.id===d.participant_id&&person.state==='admitted'&&person.media_ready)
        ||own.state!=='admitted'||d.recipient_id!==this.participant||JSON.stringify(d).length>40000)continue;
      const description=d.description as RTCSessionDescriptionInit|undefined,candidate=d.candidate as RTCIceCandidateInit|undefined;
      if(description&&(!['offer','answer'].includes(description.type)||typeof description.sdp!=='string'||description.sdp.length>32000)||candidate&&typeof candidate.candidate!=='string'||!description&&!candidate)continue;
      this.delivered.add(row.event_id);accepted.push({sequence:++this.sequence,sender_id:d.participant_id as string,payload:{...(description?{description}:{}),...(candidate?{candidate}:{})}});
    }
    this.latest=people;this.identities=identities;this.lastIndexed=indexed;
    return {meeting_id:this.meeting.id,participant_id:this.participant,state:own.state,is_host:this.isHost(),expires_at:this.meeting.expires_at,encryption:'webrtc-dtls-srtp',participants:people,signals:accepted,cursor:this.sequence,ice_servers:own.state==='admitted'?this.ice:[]};
  }

}
