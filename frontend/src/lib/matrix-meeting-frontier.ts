import type { VerifiedWorkEvent } from './federated-work-model';
import type { VerifiedIndexedWorkEvent } from './encrypted-work-api';
const PROTOCOL='freecollab-matrix-meeting/1',EVENT='eu.freecollab.meeting';
export interface AdmittedMatrixDevice {participant_id:string;matrix_user_id:string;device_id:string;join_event_id:string;join:VerifiedIndexedWorkEvent}
const deviceId=async(sender:string,device:string,nonce:string)=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(sender+'\0'+device+'\0'+nonce)))].map(byte=>byte.toString(16).padStart(2,'0')).join('').slice(0,32);
export const meetingScoped=(event:VerifiedWorkEvent,proposal:VerifiedWorkEvent)=>event.type===EVENT&&!!event.sender_device_id&&event.content.protocol===PROTOCOL
  &&event.content.room_id===proposal.content.room_id&&event.content.connection_id===proposal.content.connection_id
  &&event.content.meeting_id===proposal.content.meeting_id&&event.content.device_id===event.sender_device_id
  &&(event.event_id===proposal.event_id||event.content.proposal_event_id===proposal.event_id);
export async function meetingAdmissions(events:VerifiedIndexedWorkEvent[],proposal:VerifiedWorkEvent,through=Number.MAX_SAFE_INTEGER){
  const hostDomain=proposal.sender.split(':').slice(1).join(':'),base=events.find(event=>event.event_id===proposal.event_id);
  if(!base||base.index_server!==hostDomain||!meetingScoped(base,proposal)||base.content.kind!=='proposal')throw new Error('The meeting has no authenticated durable proposal frontier.');
  const joins=new Map<string,AdmittedMatrixDevice>();
  for(const event of events){const d=event.content;if(!meetingScoped(event,proposal)||d.kind!=='join'||typeof d.nonce!=='string'||!/^[a-f0-9-]{36}$/.test(d.nonce)
      ||typeof d.participant_id!=='string'||await deviceId(event.sender,event.sender_device_id!,d.nonce)!==d.participant_id)continue;
    joins.set(event.event_id,{participant_id:d.participant_id,matrix_user_id:event.sender,device_id:event.sender_device_id!,join_event_id:event.event_id,join:event});}
  const hostJoins=[...joins.values()].filter(row=>row.matrix_user_id===base.sender&&row.device_id===base.sender_device_id&&row.participant_id===base.content.participant_id
    &&row.join.index_server===hostDomain&&row.join.index_sequence>base.index_sequence&&row.join.index_sequence<=through).sort((a,b)=>a.join.index_sequence-b.join.index_sequence);
  const first=hostJoins[0];if(!first)throw new Error('The meeting host has no authenticated durable join.');
  const active=new Map<string,AdmittedMatrixDevice>([[first.participant_id,first]]),admitted:Array<{sequence:number;device:AdmittedMatrixDevice}>=[];
  let ended=false;
  const controls=events.filter(event=>meetingScoped(event,base)&&event.sender===base.sender&&event.sender_device_id===base.sender_device_id
    &&event.index_server===hostDomain&&event.index_sequence>base.index_sequence&&event.index_sequence<=through).sort((a,b)=>a.index_sequence-b.index_sequence);
  for(const event of controls){const d=event.content;
    if(d.kind==='end'){ended=true;continue;}
    if(ended)continue;
    if(d.kind==='admit'){
      const target=typeof d.join_event_id==='string'?joins.get(d.join_event_id):undefined;
      if(!target||target.participant_id!==d.target_id)continue;
      if([...active.values()].some(row=>row.matrix_user_id===target.matrix_user_id&&row.device_id===target.device_id&&row.participant_id!==target.participant_id))throw new Error('One verified device cannot occupy two admitted peer identities.');
      active.set(target.participant_id,target);admitted.push({sequence:event.index_sequence,device:target});
      if(active.size>6)throw new Error('The durable meeting frontier exceeds six admitted devices.');
    }else if(d.kind==='remove'&&typeof d.target_id==='string'){
      const target=active.get(d.target_id);if(target&&d.join_event_id===target.join_event_id&&target.participant_id!==first.participant_id)active.delete(target.participant_id);
    }
  }
  return {active,admitted,ended,hostDomain,base};
}
export async function verifyRecordedAdmissionFrontier(events:VerifiedIndexedWorkEvent[],proposal:VerifiedWorkEvent,request:VerifiedWorkEvent,publication:VerifiedWorkEvent,
    roster:Array<{participant_id:string;matrix_user_id:string;device_id:string;join_event_id:string}>){
  const from=events.find(row=>row.event_id===request.event_id),to=events.find(row=>row.event_id===publication.event_id),domain=proposal.sender.split(':').slice(1).join(':');
  if(!from||!to||from.index_server!==domain||to.index_server!==domain||from.index_sequence>=to.index_sequence
      ||from.sender!==proposal.sender||to.sender!==proposal.sender||from.sender_device_id!==proposal.sender_device_id||to.sender_device_id!==proposal.sender_device_id)
    throw new Error('The recording has no authenticated ordered publication frontier.');
  if(events.some(event=>meetingScoped(event,proposal)&&event.sender===proposal.sender&&event.sender_device_id===proposal.sender_device_id
      &&event.index_server===domain&&event.content.kind==='capture-state'&&event.content.state==='stopped'
      &&event.content.request_event_id===request.event_id&&event.content.capture_id===request.content.capture_id
      &&event.index_sequence>from.index_sequence&&event.index_sequence<to.index_sequence))
    throw new Error('A stopped recording cannot authorize a new publication. Recover its original receipt.');
  const before=await meetingAdmissions(events,proposal,from.index_sequence),after=await meetingAdmissions(events,proposal,to.index_sequence);
  if(before.ended||after.ended)throw new Error('Recording publication happened outside an active admitted meeting.');
  const required=new Map(before.active);
  for(const row of after.admitted)if(row.sequence>from.index_sequence)required.set(row.device.participant_id,row.device);
  if(required.size!==roster.length||before.active.size!==roster.length||roster.some(row=>{
    const device=required.get(row.participant_id);return !device||device.matrix_user_id!==row.matrix_user_id||device.device_id!==row.device_id||device.join_event_id!==row.join_event_id;
  }))throw new Error('Every device admitted during this recording frontier must consent; a device was omitted or admitted after the reviewed request.');
}
