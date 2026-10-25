import { api } from './api';
import { clipsApi, type ClipFields } from './clips-api';
import { aiRuntimeApi, type AIRunInput } from './ai-runtime-api';
import { assertOwner, listPrivate, owner, removePrivate, savePrivate, type Owner } from '../mobile/storage';
export interface PendingMeetingRecording {
  id: string; channel_id: string; capture_id: string; generation: number; created_at: string;
  mime: string; data: string; fields: ClipFields; publication_receipt?: string;
  stage: 'captured' | 'published'; clip_id?: string;
  publication_proof?: Record<string, unknown>;
  content_mode?: 'server'|'matrix';transport?:'native'|'matrix';
  assistant?: { workspace_id: string; profile_id: string; profile_revision: number };
}
export interface MeetingPublicationResult {
  capture: { id:string; generation:number; all_consented:boolean } | null;
  publication_receipt?:string; publication_proof?:Record<string,unknown>;
}
const publications=new Map<string,Set<AbortController>>();
const publicationKey=(expected:Owner,id:string)=>expected.user+'\0'+id;
/** A late stop response may authorize only the exact retained part it reserved. */
export function bindMeetingPublication(record:PendingMeetingRecording,result:MeetingPublicationResult):PendingMeetingRecording {
  if(result.capture?.id!==record.capture_id||result.capture.generation!==record.generation||!result.capture.all_consented
      ||!result.publication_receipt&&!result.publication_proof
      ||result.publication_proof&&(result.publication_proof.capture_id!==record.capture_id
        ||result.publication_proof.generation!==record.generation||result.publication_proof.clip_id!==record.id))
    throw new Error('The late publication receipt does not match this recording and its consent.');
  return {...record,publication_receipt:result.publication_receipt,publication_proof:result.publication_proof};
}
export async function awaitMeetingPublication(operation:Promise<MeetingPublicationResult>,signal?:AbortSignal,milliseconds=30000):Promise<MeetingPublicationResult>{
  if(signal?.aborted)throw new DOMException('Recording publication cancelled.','AbortError');
  let timer:ReturnType<typeof setTimeout>;let abort:()=>void;
  try{return await Promise.race([operation,new Promise<never>((_,reject)=>{
    timer=setTimeout(()=>reject(new Error('Recording publication is still pending. The part remains private on this device.')),milliseconds);
    abort=()=>reject(new DOMException('Recording publication cancelled.','AbortError'));signal?.addEventListener('abort',abort,{once:true});
    if(signal?.aborted)abort();
  })]);}finally{clearTimeout(timer!);signal?.removeEventListener('abort',abort!);}
}
async function blobData(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader(); reader.onerror = () => reject(new Error('The private recording could not be read.'));
    reader.onload = () => resolve(String(reader.result).split(',')[1] || ''); reader.readAsDataURL(blob);
  });
}
export async function retainMeetingRecording(record: Omit<PendingMeetingRecording, 'data' | 'mime' | 'stage'>, blob: Blob, expected = owner()) {
  assertOwner(expected);
  if (blob.size > 50 * 1024 * 1024) throw new Error('This recording segment exceeds 50 MiB.');
  const pending = await listPrivate<PendingMeetingRecording>('meeting-recording', expected); assertOwner(expected);
  if (pending.filter(row => row.value.stage === 'captured').length >= 2)
    throw new Error('Two recording segments are waiting for upload. Finish or discard them before recording more.');
  const value: PendingMeetingRecording = { ...record, stage: 'captured', mime: blob.type, data: await blobData(blob) };
  assertOwner(expected); await savePrivate('meeting-recording', record.id, value, expected);
  window.dispatchEvent(new CustomEvent('freecollab:meeting-recording-pending', { detail: { channel_id: record.channel_id } })); return value;
}
export async function pendingMeetingRecordings(channel: string) {
  const expected = owner(), values = await listPrivate<PendingMeetingRecording>('meeting-recording', expected); assertOwner(expected);
  return values.map(row => row.value).filter(row => row.channel_id === channel);
}
export function recordingBlob(record: PendingMeetingRecording) {
  const raw = atob(record.data); return new Blob([Uint8Array.from(raw, value => value.charCodeAt(0))], { type: record.mime });
}
export async function discardMeetingRecording(id:string){const expected=owner();assertOwner(expected);
  for(const operation of publications.get(publicationKey(expected,id))||[])operation.abort();
  return removePrivate('meeting-recording',id,expected);
}
export async function publishMeetingRecording(record: PendingMeetingRecording, expected: Owner = owner(), signal?: AbortSignal, beforePublish?: () => void | Promise<void>,
  pendingPublication?:Promise<MeetingPublicationResult>) {
  assertOwner(expected);const controller=new AbortController(),key=publicationKey(expected,record.id),abort=()=>controller.abort();
  if(!publications.has(key))publications.set(key,new Set());publications.get(key)!.add(controller);
  signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)controller.abort();
  try{return await publishRetainedRecording(record,expected,controller.signal,beforePublish,pendingPublication);}
  finally{signal?.removeEventListener('abort',abort);const entries=publications.get(key);entries?.delete(controller);if(!entries?.size)publications.delete(key);}
}
async function publishRetainedRecording(record:PendingMeetingRecording,expected:Owner,signal:AbortSignal,beforePublish?:(()=>void|Promise<void>),pendingPublication?:Promise<MeetingPublicationResult>){
  assertOwner(expected); let value = record;
  const currentDraft=async()=>{assertOwner(expected);if(signal?.aborted)throw new DOMException('Recording publication cancelled.','AbortError');
    const stored=(await listPrivate<PendingMeetingRecording>('meeting-recording',expected)).find(row=>row.value.id===value.id)?.value;assertOwner(expected);
    if(!stored||stored.capture_id!==value.capture_id||stored.generation!==value.generation)throw new Error('This private recording was discarded or replaced.');
    await beforePublish?.();assertOwner(expected);if(signal?.aborted)throw new DOMException('Recording publication cancelled.','AbortError');};
  if (value.stage === 'captured') {
    if(value.content_mode!=='server'&&value.content_mode!=='matrix')throw new Error('This private recording has no reviewed encryption policy. Download it for manual review.');
    if(!value.publication_receipt&&!value.publication_proof&&pendingPublication){
      const result=await awaitMeetingPublication(pendingPublication,signal);await currentDraft();
      value=bindMeetingPublication(value,result);await savePrivate('meeting-recording',value.id,value,expected);
      if(signal.aborted){await removePrivate('meeting-recording',value.id,expected);throw new DOMException('Recording publication cancelled.','AbortError');}await currentDraft();
    }
    if (!value.publication_receipt && !value.publication_proof) {
      if(value.transport==='matrix'){const {recoverMatrixRecordingProof}=await import('./matrix-meeting-capture');value={...value,publication_proof:await recoverMatrixRecordingProof(value.channel_id,value.capture_id,value.id)};await savePrivate('meeting-recording',value.id,value,expected);}
      else {const result = await api.request<{ publication_receipt: string }>('/channels/' + encodeURIComponent(value.channel_id)
        + '/meeting-captures/' + encodeURIComponent(value.capture_id) + '/segments/' + encodeURIComponent(value.id));
      assertOwner(expected); value = { ...value, publication_receipt: result.publication_receipt };
      await savePrivate('meeting-recording', value.id, value, expected);}
    }
    await currentDraft();
    const clip = await clipsApi.create(value.channel_id, recordingBlob(value), value.fields, undefined,
      { id: value.id, publicationReceipt: value.publication_receipt, captureId: value.capture_id, generation: value.generation, signal, beforePublish:currentDraft, meetingCaptureProof:value.publication_proof,expectedContentMode:value.content_mode });
    await currentDraft();
    value = { ...value, stage: 'published', clip_id: clip.id, data: '' };
    await savePrivate('meeting-recording', value.id, value, expected);
  }
  if (value.assistant && value.fields.transcript?.trim()) {
    const input: AIRunInput & { disclosure_scope: { kind: 'meeting_recording'; id: string; revision: number } } = {
      request_id: 'meeting_' + value.id, channel_id: value.channel_id, profile_id: value.assistant.profile_id,
      expected_profile_revision: value.assistant.profile_revision, operation: 'summarize',
      prompt: 'Summarize this consented meeting segment. Separate agreed decisions, open questions and proposed next steps. Do not invent speakers, owners or commitments. Keep the result private for review.',
      sources: [{ kind: 'clip', id: value.clip_id! }], max_steps: 1, max_tokens: 8192, disclose_to_provider: true,
      disclosure_scope: { kind: 'meeting_recording', id: value.capture_id, revision: value.generation },
    };
    await aiRuntimeApi.create(value.assistant.workspace_id, input); assertOwner(expected);
  }
  await removePrivate('meeting-recording', value.id, expected);
  window.dispatchEvent(new CustomEvent('freecollab:clips-changed', { detail: { channel_id: value.channel_id } }));
  return value.clip_id!;
}
