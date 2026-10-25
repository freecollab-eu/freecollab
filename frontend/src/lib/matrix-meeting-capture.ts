import { getSecureMessaging } from './secure-client';
import { api } from './api';
import { MatrixMeetingRoom, MATRIX_MEETING_EVENT, MATRIX_MEETING_PROTOCOL } from './federated-meeting';
import { EncryptedWorkClient, loadEncryptedRoomContext } from './encrypted-work-api';
import { verifyEncryptedRoomEvents } from './encrypted-room-events';
import { meetingAdmissions, verifyRecordedAdmissionFrontier } from './matrix-meeting-frontier';
import { assertLiveCaption } from './meeting-capture-api';
import { sameRecordingProof } from './meeting-capture-proof';
import type { CaptureAuthority, CaptureState, CaptureStatus, MeetingCapturePreferences } from './meeting-capture-api';
import type { LiveCaption, LocalCaptionConfiguration } from './meeting-captions';
import type { VerifiedWorkEvent } from './federated-work-model';
interface ParticipantProof { participant_id:string; matrix_user_id:string; device_id:string; join_event_id:string; consent_event_id:string }
/** Input is the exact verified, scoped host-home indexed state frontier. */
export function terminalMatrixCaptureState(events:Array<{index_sequence:number;content:Record<string,unknown>}>){
  const states=[...events].filter(event=>['recording','paused','stopped'].includes(String(event.content.state))).sort((a,b)=>a.index_sequence-b.index_sequence);
  return states.some(event=>event.content.state==='stopped')?'stopped':states[states.length-1]?.content.state;
}
export interface MatrixRecordingProof { protocol:'freecollab-matrix-recording/1';room_id:string;connection_id:string;meeting_event_id:string;
  request_event_id:string;publication_event_id:string;capture_id:string;generation:number;clip_id:string;recorder_device_id:string;participants:ParticipantProof[];caption_audience:'channel' }
const eventId=(value:unknown):value is string=>typeof value==='string'&&value.startsWith('$')&&value.length<=1024;
const exact=(value:object,keys:string[])=>!Object.keys(value).some(key=>!keys.includes(key));
function parse(value:unknown):MatrixRecordingProof {
  const p=value as MatrixRecordingProof;
  if(!p||typeof p!=='object'||Array.isArray(p)||!exact(p,['protocol','room_id','connection_id','meeting_event_id','request_event_id','publication_event_id','capture_id','generation','clip_id','recorder_device_id','participants','caption_audience'])
      ||p.caption_audience!=='channel'||p.protocol!=='freecollab-matrix-recording/1'||typeof p.room_id!=='string'||!p.room_id.startsWith('!')||p.room_id.length>1024||!/^[a-f0-9]{32}$/.test(p.connection_id)
      ||!eventId(p.meeting_event_id)||!eventId(p.request_event_id)||!eventId(p.publication_event_id)||!/^[a-f0-9-]{36}$/.test(p.capture_id)||p.generation!==1
      ||!/^[A-Za-z0-9_-]{1,80}$/.test(p.clip_id)||typeof p.recorder_device_id!=='string'||!p.recorder_device_id||p.recorder_device_id.length>128
      ||!Array.isArray(p.participants)||!p.participants.length||p.participants.length>6||new Set(p.participants.map(row=>row.participant_id)).size!==p.participants.length
      ||p.participants.some(row=>!row||!exact(row,['participant_id','matrix_user_id','device_id','join_event_id','consent_event_id'])||!/^[a-f0-9]{32}$/.test(row.participant_id)
        ||typeof row.matrix_user_id!=='string'||!row.matrix_user_id.startsWith('@')||row.matrix_user_id.length>512||typeof row.device_id!=='string'||!row.device_id||row.device_id.length>128
        ||!eventId(row.join_event_id)||!eventId(row.consent_event_id)))throw new Error('The encrypted meeting consent proof is invalid.');
  return p;
}
export async function verifyMatrixRecordingProof(channel:string,value:unknown):Promise<{proof:MatrixRecordingProof;host:VerifiedWorkEvent}> {
  const token=api.getAccessToken(),actor=api.getCurrentUserId(),p=parse(value),context=await loadEncryptedRoomContext(channel);
  if(context.room_id!==p.room_id||context.connection_id!==p.connection_id||!context.room_protocols.includes(MATRIX_MEETING_PROTOCOL))throw new Error('The recording belongs to another encrypted room.');
  const ids=[...new Set([p.meeting_event_id,p.request_event_id,p.publication_event_id,...p.participants.flatMap(row=>[row.join_event_id,row.consent_event_id])])];
  const events=await verifyEncryptedRoomEvents(channel,ids),get=(id:string)=>events.find(event=>event.event_id===id)!;
  const host=get(p.meeting_event_id),request=get(p.request_event_id),publication=get(p.publication_event_id);
  const scope=(event:VerifiedWorkEvent)=>event.type===MATRIX_MEETING_EVENT&&event.content.protocol===MATRIX_MEETING_PROTOCOL&&event.content.room_id===p.room_id
    &&event.content.connection_id===p.connection_id&&event.content.device_id===event.sender_device_id&&event.content.meeting_id===host.content.meeting_id;
  const byHost=(event:VerifiedWorkEvent)=>scope(event)&&event.sender===host.sender&&event.sender_device_id===host.sender_device_id;
  if(!scope(host)||host.content.kind!=='proposal'||host.sender_device_id!==p.recorder_device_id||!byHost(request)||request.content.kind!=='capture-request'
      ||request.content.caption_audience!=='channel'||request.content.capture_id!==p.capture_id||request.content.generation!==p.generation||!byHost(publication)||publication.content.kind!=='capture-publish'
      ||publication.content.request_event_id!==p.request_event_id||publication.content.capture_id!==p.capture_id||publication.content.clip_id!==p.clip_id
      ||publication.content.generation!==p.generation||JSON.stringify(publication.content.consent_event_ids)!==JSON.stringify(p.participants.map(row=>row.consent_event_id))
      ||!sameRecordingProof(request.content.roster,p.participants.map(({consent_event_id:_,...row})=>row)))throw new Error('The recording host or reviewed participant frontier does not match.');
  for(const row of p.participants){const join=get(row.join_event_id),consent=get(row.consent_event_id);
    if(!scope(join)||join.content.kind!=='join'||join.sender!==row.matrix_user_id||join.sender_device_id!==row.device_id||join.content.participant_id!==row.participant_id
        ||!scope(consent)||consent.content.kind!=='capture-consent'||consent.sender!==row.matrix_user_id||consent.sender_device_id!==row.device_id||consent.content.participant_id!==row.participant_id
        ||consent.content.capture_id!==p.capture_id||consent.content.generation!==p.generation||consent.content.request_event_id!==p.request_event_id||consent.content.granted!==true)
      throw new Error('Every recorded device must supply its own verified consent.');
  }
  const indexClient=new EncryptedWorkClient(channel);let indexed;
  try{indexed=await indexClient.verifiedIndexedEvents([MATRIX_MEETING_EVENT]);}finally{indexClient.stop();}
  await verifyRecordedAdmissionFrontier(indexed,host,request,publication,p.participants);
  // Revocation is causal to what this device has actually observed. A remote
  // offline recipient can retain a copy; this is not global atomic erasure.
  const secure=await getSecureMessaging(),timeline=await secure.events(channel);
  const candidates=[...indexed.filter(event=>event.content.capture_id===p.capture_id&&event.content.kind==='capture-consent'&&event.content.granted===false).map(event=>event.event_id),...timeline.filter(event=>event.getType()===MATRIX_MEETING_EVENT&&event.getContent().capture_id===p.capture_id&&event.getContent().kind==='capture-consent'
    &&event.getContent().granted===false&&event.getId()).map(event=>event.getId()!)];
  if(candidates.length>100)throw new Error('The recording revocation frontier exceeds its safe bound.');
  if(candidates.length){const revoked=await verifyEncryptedRoomEvents(channel,[...new Set(candidates)],{ignoreInvalid:true});
    if(revoked.some(event=>scope(event)&&event.content.request_event_id===p.request_event_id&&p.participants.some(row=>row.matrix_user_id===event.sender&&row.device_id===event.sender_device_id&&row.participant_id===event.content.participant_id)))
      throw new Error('A recorded device withdrew consent. Publication is blocked.');}
  if(token!==api.getAccessToken()||actor!==api.getCurrentUserId())throw new Error('The encrypted recording account changed.');
  return {proof:p,host};
}

export class MatrixCaptureAuthority implements CaptureAuthority {
  private indexClient:EncryptedWorkClient;
  private revision=0; private configuration:LocalCaptionConfiguration|null=null;
  private receipts=new Map<string,Record<string,unknown>>();
  constructor(private room:MatrixMeetingRoom){this.indexClient=new EncryptedWorkClient(room.channel);}
  private async read(){
    const context=await loadEncryptedRoomContext(this.room.channel),indexed=await this.indexClient.verifiedIndexedEvents([MATRIX_MEETING_EVENT]);
    if(context.room_id!==this.room.context.room_id||context.connection_id!==this.room.context.connection_id||context.matrix_user_id!==this.room.matrixUser||!this.room.captureLeaseCurrent())throw new Error('The recording meeting scope changed.');
    const proposal=indexed.find(row=>row.event_id===this.room.meeting.proposal_event_id);if(!proposal)throw new Error('The verified meeting proposal is unavailable.');
    const frontier=await meetingAdmissions(indexed,proposal);if(frontier.ended||Date.now()>=Date.parse(this.room.meeting.expires_at))throw new Error('The encrypted meeting has ended.');
    const snapshot={context,indexed,proposal,participant:this.room.participant,device:this.room.device,host:this.room.isHost(),
      people:[...frontier.active.values()].map(row=>({id:row.participant_id,state:'admitted',name:context.members.find(member=>member.matrix_user_id===row.matrix_user_id)?.name||row.matrix_user_id})),
      identities:new Map([...frontier.active].map(([id,row])=>[id,row.join]))};
    const secure=await getSecureMessaging(),timeline=await secure.events(this.room.channel);
    if(!this.configuration){const value=await api.request<{captions:LocalCaptionConfiguration}>('/channels/'+encodeURIComponent(this.room.channel)+'/meeting-media-configuration');this.configuration=value.captions;}
    const verified=snapshot.indexed.filter(event=>event.content.kind==='capture-request'&&event.content.proposal_event_id===snapshot.proposal.event_id);
    const byHost=(event:VerifiedWorkEvent)=>event.sender===snapshot.proposal.sender&&event.sender_device_id===snapshot.proposal.sender_device_id&&event.content.device_id===event.sender_device_id
      &&event.content.room_id===snapshot.context.room_id&&event.content.connection_id===snapshot.context.connection_id&&event.content.protocol===MATRIX_MEETING_PROTOCOL&&event.content.proposal_event_id===snapshot.proposal.event_id
      &&event.content.meeting_id===snapshot.proposal.content.meeting_id;
    const request=verified.filter(byHost).sort((a,b)=>a.index_sequence-b.index_sequence).slice(-1)[0];
    if(!request)return {snapshot,request:null,status:{capture:null,participant_id:snapshot.participant,configuration:this.configuration} as CaptureStatus,consents:[] as VerifiedWorkEvent[]};
    const data=request.content,roster=data.roster as Array<Omit<ParticipantProof,'consent_event_id'>>;
    if(!Array.isArray(roster)||roster.length<1||roster.length>6||typeof data.video!=='boolean'||typeof data.captions!=='boolean'||typeof data.save_transcript!=='boolean'
        ||data.caption_audience!=='channel'||typeof data.language!=='string'||typeof data.capture_id!=='string'||!/^[a-f0-9-]{36}$/.test(data.capture_id)||data.generation!==1)throw new Error('The encrypted capture policy is invalid.');
    const related=snapshot.indexed.filter(event=>event.content.capture_id===data.capture_id&&event.content.request_event_id===request.event_id);
    const captionRows=timeline.filter(event=>event.getType()===MATRIX_MEETING_EVENT&&event.getContent().capture_id===data.capture_id&&event.getContent().request_event_id===request.event_id
      &&event.getContent().kind==='capture-caption'&&Date.now()-event.getTs()<45000&&event.getId()?.startsWith('$')).slice(-4);
    const current=[...related,...await verifyEncryptedRoomEvents(this.room.channel,[...new Set(captionRows.map(event=>event.getId()!))],{ignoreInvalid:true})];
    const ownConsent=(event:VerifiedWorkEvent,row:Omit<ParticipantProof,'consent_event_id'>)=>event.type===MATRIX_MEETING_EVENT&&event.content.protocol===MATRIX_MEETING_PROTOCOL
      &&event.content.room_id===snapshot.context.room_id&&event.content.connection_id===snapshot.context.connection_id&&event.content.device_id===event.sender_device_id
      &&event.content.request_event_id===request.event_id&&event.content.capture_id===data.capture_id&&event.content.generation===1
      &&event.sender===row.matrix_user_id&&event.sender_device_id===row.device_id&&event.content.participant_id===row.participant_id;
    const revoked=current.some(event=>event.content.kind==='capture-consent'&&event.content.granted===false&&roster.some(row=>ownConsent(event,row)));
    const consents=roster.map(row=>current.filter(event=>event.content.kind==='capture-consent'&&event.content.granted===true&&ownConsent(event,row)).sort((a,b)=>a.timestamp-b.timestamp||a.event_id.localeCompare(b.event_id)).slice(-1)[0]).filter((event):event is VerifiedWorkEvent=>!!event);
    const actual=snapshot.people.filter(row=>row.state==='admitted').map(row=>row.id).sort(),expected=roster.map(row=>row.participant_id).sort();
    const sameRoster=JSON.stringify(actual)===JSON.stringify(expected)&&roster.every(row=>{const join=snapshot.identities.get(row.participant_id);return join?.sender===row.matrix_user_id&&join.sender_device_id===row.device_id;});
    const state=terminalMatrixCaptureState(related.filter(event=>event.content.kind==='capture-state'&&byHost(event)));
    const stopped=state==='stopped';const granted=revoked?[]:consents.map(row=>row.content.participant_id as string);
    const capture:CaptureState={id:data.capture_id,generation:1,revision:++this.revision,state:stopped?'stopped':!sameRoster||revoked?'paused':state==='recording'?'recording':state==='paused'?'paused':'requesting',created_by:snapshot.proposal.sender,
      recorder_participant_id:snapshot.proposal.content.participant_id as string,content_mode:'matrix',caption_audience:'channel',video:data.video,captions:data.captions,save_transcript:data.save_transcript,language:data.language,
      all_consented:!revoked&&(stopped||sameRoster)&&granted.length===roster.length,consented:granted,awaiting:actual.filter(id=>!granted.includes(id)),
      participants:snapshot.people.filter(row=>row.state==='admitted').map(row=>({id:row.id,name:row.name})),encrypted_captions:true,
      live_captions:current.filter(event=>event.content.kind==='capture-caption'&&byHost(event)&&typeof event.content.text==='string'&&event.content.text.length<=2000&&typeof event.content.start==='number'&&typeof event.content.end==='number').map(event=>({start:event.content.start as number,end:event.content.end as number,text:event.content.text as string})),
      ai:{enabled:false,profile_id:null,profile_revision:null,recipient:null,model:null,protocol:null,expires_at:null,max_segments:0}};
    return {snapshot,request,status:{capture,participant_id:snapshot.participant,configuration:this.configuration} as CaptureStatus,consents};
  }
  async status(){return (await this.read()).status;}
  async request(preferences:MeetingCapturePreferences){
    const {snapshot,status}=await this.read();if(!snapshot.host)throw new Error('Only the verified host device can request recording.');
    if(status.capture&&status.capture.state!=='stopped')throw new Error('Stop the current recording request before creating another.');
    if(preferences.captions&&preferences.caption_audience!=='channel')throw new Error('Review disclosure to all encrypted channel members before enabling live captions.');
    if(preferences.ai_profile_id||preferences.ai_disclose)throw new Error('Encrypted meeting AI requires foreground recipient review.');
    const roster=snapshot.people.filter(row=>row.state==='admitted').sort((a,b)=>a.id.localeCompare(b.id)).map(row=>{const join=snapshot.identities.get(row.id)!;return {participant_id:row.id,matrix_user_id:join.sender,device_id:join.sender_device_id!,join_event_id:join.event_id};});
    if(!roster.length||roster.length>6||roster.some(row=>!row.device_id))throw new Error('Every admitted device must be verified before requesting recording.');
    const capture=crypto.randomUUID(),event=await this.room.publishCapture('capture-request',{capture_id:capture,generation:1,roster,video:preferences.video,captions:preferences.captions,save_transcript:preferences.save_transcript,language:preferences.language,caption_audience:'channel'});
    await this.room.publishCapture('capture-consent',{capture_id:capture,generation:1,request_event_id:event,granted:true});return this.status();
  }
  async consent(capture:CaptureState,granted:boolean){const view=await this.read();if(!view.request||view.status.capture?.id!==capture.id||capture.state==='stopped')throw new Error('Review the current recording request.');
    await this.room.publishCapture('capture-consent',{capture_id:capture.id,generation:1,request_event_id:view.request.event_id,granted});return this.status();}
  async control(capture:CaptureState,action:'start'|'pause'|'resume'|'stop'|'segment',requestId?:string){
    const view=await this.read(),current=view.status.capture;if(!view.snapshot.host||!view.request||current?.id!==capture.id)throw new Error('Only the current recording host can control capture.');
    const receiptKey=current.id+'\0'+requestId;
    if(current.state==='stopped'){
      const previous=requestId?this.receipts.get(receiptKey):undefined;
      if(action==='stop'&&(!requestId||previous))return {...view.status,...(previous?{publication_proof:previous}:{})};
      throw new Error('This recording has stopped. Review a new request before recording again.');
    }
    if(['start','resume','segment'].includes(action)&&!current.all_consented)throw new Error('Every current device must consent to capture and publication.');
    let proof:Record<string,unknown>|undefined;
    if((action==='segment'||action==='stop')&&current.all_consented){if(!requestId||!/^[A-Za-z0-9_-]{1,80}$/.test(requestId))throw new Error('The recording segment needs its stable clip identifier.');
      proof=this.receipts.get(receiptKey);
      if(!proof){const roster=view.request.content.roster as Array<Omit<ParticipantProof,'consent_event_id'>>;
        const participants=roster.map(row=>({...row,consent_event_id:view.consents.find(event=>event.content.participant_id===row.participant_id)!.event_id}));
        const event=await this.room.publishCapture('capture-publish',{capture_id:current.id,generation:1,request_event_id:view.request.event_id,clip_id:requestId,consent_event_ids:participants.map(row=>row.consent_event_id)});
        proof={protocol:'freecollab-matrix-recording/1',room_id:view.snapshot.context.room_id,connection_id:view.snapshot.context.connection_id,meeting_event_id:view.snapshot.proposal.event_id,
          request_event_id:view.request.event_id,publication_event_id:event,capture_id:current.id,generation:1,clip_id:requestId,recorder_device_id:view.snapshot.device,participants,caption_audience:'channel'};
        this.receipts.set(receiptKey,proof);}
    }
    if(action!=='segment')await this.room.publishCapture('capture-state',{capture_id:current.id,generation:1,request_event_id:view.request.event_id,state:action==='stop'?'stopped':action==='pause'?'paused':'recording'});
    return {...await this.status(),...(proof?{publication_proof:proof}:{})};
  }
  async caption(capture:CaptureState,caption:LiveCaption,signal?:AbortSignal){
    assertLiveCaption(caption);
    const authorize=async()=>{if(signal?.aborted)throw new DOMException('Caption publication cancelled.','AbortError');const view=await this.read();
      if(!view.snapshot.host||!view.request||view.snapshot.participant!==capture.recorder_participant_id||view.status.capture?.id!==capture.id
        ||view.status.capture.generation!==capture.generation||view.status.capture.state!=='recording'||!view.status.capture.all_consented||view.status.capture.caption_audience!=='channel')
        throw new Error('Current recorder and complete participant consent are required before sharing captions.');
      if(signal?.aborted)throw new DOMException('Caption publication cancelled.','AbortError');};
    await authorize();const view=await this.read();
    const id=await this.room.publishCapture('capture-caption',{capture_id:capture.id,generation:capture.generation,request_event_id:view.request!.event_id,
      caption_audience:'channel',...caption},{transactionId:'fc-capture-caption-'+crypto.randomUUID(),authorize,signal});
    const secure=await getSecureMessaging(),{c,roomId}=await secure.roomFor(this.room.channel);setTimeout(()=>{void c.redactEvent(roomId,id).catch(()=>{});},45000);
  }

}

/** Recover only a durably indexed, independently verified publication receipt. */
export async function recoverMatrixRecordingProof(channel:string,captureId:string,clipId:string):Promise<Record<string,unknown>>{
  const client=new EncryptedWorkClient(channel);
  try{
    const context=await loadEncryptedRoomContext(channel),events=await client.verifiedIndexedEvents([MATRIX_MEETING_EVENT]);
    const candidates=events.filter(event=>event.content.kind==='capture-publish'&&event.content.capture_id===captureId&&event.content.clip_id===clipId&&event.sender===context.matrix_user_id)
      .sort((a,b)=>a.index_sequence-b.index_sequence);
    for(const publication of candidates){const request=events.find(event=>event.event_id===publication.content.request_event_id),meeting=events.find(event=>event.event_id===publication.content.proposal_event_id);
      if(!request||!meeting||!Array.isArray(request.content.roster)||!Array.isArray(publication.content.consent_event_ids))continue;
      const participants=(request.content.roster as Array<Record<string,unknown>>).map((row,index)=>({...row,consent_event_id:(publication.content.consent_event_ids as unknown[])[index]}));
      const proof={protocol:'freecollab-matrix-recording/1',room_id:context.room_id,connection_id:context.connection_id,meeting_event_id:meeting.event_id,
        request_event_id:request.event_id,publication_event_id:publication.event_id,capture_id:captureId,generation:publication.content.generation,
        clip_id:clipId,recorder_device_id:publication.sender_device_id,participants,caption_audience:'channel'};
      try{await verifyMatrixRecordingProof(channel,proof);return proof;}catch{/* another valid indexed attempt may carry the original receipt */}
    }
    throw new Error('No current approved encrypted publication receipt was found. The recording remains private.');
  }finally{client.stop();}
}
