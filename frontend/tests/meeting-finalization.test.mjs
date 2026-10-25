import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source=name=>ts.transpileModule(fs.readFileSync(new URL('../src/lib/'+name,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject};};
const record=()=>({id:'part',channel_id:'channel',capture_id:'capture',generation:1,created_at:new Date().toISOString(),mime:'audio/webm',data:btoa('real retained test bytes'),fields:{title:'Meeting recording',duration:2,transcript:''},stage:'captured',content_mode:'matrix',transport:'matrix'});
const receipt=()=>({capture:{id:'capture',generation:1,all_consented:true},publication_proof:{protocol:'freecollab-matrix-recording/1',capture_id:'capture',generation:1,clip_id:'part'}});
function vault(){
  const state={rows:new Map(),uploads:[],current:{user:'actor',token:'token',epoch:1}},exports={},window=new EventTarget();
  const storage={owner:()=>({...state.current}),assertOwner:expected=>{if(JSON.stringify(expected)!==JSON.stringify(state.current))throw Error('owner changed')},listPrivate:async()=>[...state.rows.values()].map(value=>({value})),savePrivate:async(_,id,value)=>{state.rows.set(id,structuredClone(value))},removePrivate:async(_,id)=>state.rows.delete(id)};
  const dependencies={'./api':{api:{request:async()=>{throw Error('Unexpected fallback request')}}},'./clips-api':{clipsApi:{create:async(channel,blob,fields,progress,options)=>{await options.beforePublish();state.uploads.push({channel,blob,options,proof:structuredClone(state.rows.get('part')?.publication_proof)});return{id:'$durable-clip'}}}},'./ai-runtime-api':{aiRuntimeApi:{create:async()=>assert.fail('Unexpected model invocation')}},'../mobile/storage':storage};
  class Notice extends Event{constructor(type,options){super(type);this.detail=options?.detail}}
  vm.runInNewContext(source('meeting-recording-vault.ts'),{exports,require:name=>dependencies[name]||{},Blob,atob,Map,Set,DOMException,AbortController,setTimeout,clearTimeout,CustomEvent:Notice,window});
  return {...exports,state,storage};
}
test('the original delayed stop receipt is reconciled into the retained exact part before a single upload',async()=>{
  const v=vault(),r=record(),late=deferred();v.state.rows.set(r.id,r);let authorized=0;
  const publishing=v.publishMeetingRecording(r,v.storage.owner(),undefined,()=>{authorized++},late.promise);
  await turn();assert.equal(v.state.uploads.length,0);assert.equal(v.state.rows.get(r.id).data,r.data);
  late.resolve(receipt());assert.equal(await publishing,'$durable-clip');assert.equal(v.state.uploads.length,1);assert.equal(v.state.uploads[0].options.id,'part');assert.equal(v.state.uploads[0].proof.clip_id,'part');assert.ok(authorized>=2);assert.equal(v.state.rows.size,0);
});
test('late receipt reconciliation cannot cross capture, generation, part, withdrawal, discard or account scope',async()=>{
  for(const mutation of ['capture','generation','part','consent','discard','account']){
    const v=vault(),r=record(),late=deferred();v.state.rows.set(r.id,r);
    const publishing=v.publishMeetingRecording(r,v.storage.owner(),undefined,undefined,late.promise);const rejected=assert.rejects(publishing);
    await turn();const result=receipt();
    if(mutation==='capture')result.capture.id='another';if(mutation==='generation')result.capture.generation=2;if(mutation==='part')result.publication_proof.clip_id='another';if(mutation==='consent')result.capture.all_consented=false;if(mutation==='discard')v.state.rows.delete(r.id);if(mutation==='account')v.state.current.epoch++;
    late.resolve(result);await rejected;assert.equal(v.state.uploads.length,0,mutation);
  }
});
test('late publication waiting has a real timeout and abort without consuming or inventing a receipt',async()=>{
  const v=vault(),never=deferred();await assert.rejects(v.awaitMeetingPublication(never.promise,undefined,5),/remains private/);
  const controller=new AbortController(),pending=v.awaitMeetingPublication(never.promise,controller.signal);const rejected=assert.rejects(pending,{name:'AbortError'});controller.abort();await rejected;assert.equal(v.state.uploads.length,0);
});
test('actual recording proof equality accepts reordered JSON keys and rejects changed identities, consent or array order',()=>{
 const exports={};vm.runInNewContext(source('meeting-capture-proof.ts'),{exports,require:()=>({})});
 const proof={room:'room',participants:[{device:'first',consent:'yes'},{device:'second',consent:'yes'}]},reordered={participants:[{consent:'yes',device:'first'},{consent:'yes',device:'second'}],room:'room'};
 assert.equal(exports.sameRecordingProof(proof,reordered),true);reordered.participants[0].consent='no';assert.equal(exports.sameRecordingProof(proof,reordered),false);reordered.participants.reverse();assert.equal(exports.sameRecordingProof(proof,reordered),false);
});
for(const protocol of ['freecollab-native-recording/1','freecollab-matrix-recording/1'])test('recording file verifier accepts key reordering but rejects changed authenticated proof: '+protocol,async()=>{
 const exports={},sender='@fc_actor:matrix.test',device='verified-device',room='!room:matrix.test',connection='c'.repeat(32);
 const proof=protocol.includes('native')?{protocol,capture_id:'a'.repeat(32),generation:1,receipt_id:'b'.repeat(32),clip_id:'part',room_id:room,connection_id:connection,recorder_device_id:device,content_mode:'matrix',caption_audience:'channel'}:{protocol,capture_id:'capture',generation:1,clip_id:'part',room_id:room,connection_id:connection,recorder_device_id:device,participants:[{participant_id:'first',consent_event_id:'$consent-first'},{participant_id:'second',consent_event_id:'$consent-second'}],caption_audience:'channel'};
 const reordered=value=>Array.isArray(value)?value.map(reordered):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).reverse().map(([key,item])=>[key,reordered(item)])):value;
 let actual=reordered(proof);
 const api={getAccessToken:()=> 'token',getCurrentUserId:()=> 'actor',request:async()=>({capture_id:proof.capture_id,generation:1,receipt_id:proof.receipt_id,clip_id:'part',created_by:'actor',consent_authority:'native-authenticated-participants',content_mode:'matrix',caption_audience:'channel'})};
 const dependencies={'./api':{api},'./encrypted-work-api':{loadEncryptedRoomContext:async()=>({mode:'local',connection_id:connection,room_id:room,local_server:'matrix.test'})},'./encrypted-room-events':{verifyEncryptedRoomEvents:async()=>[{sender,sender_device_id:device,content:{meeting_capture:actual}}]},'./matrix-meeting-capture':{verifyMatrixRecordingProof:async()=>({proof,host:{sender}})}};
 vm.runInNewContext(source('meeting-capture-proof.ts'),{exports,require:name=>dependencies[name]||{}});
 const event={getContent:()=>({meeting_capture:proof}),getId:()=>'$file',getRoomId:()=>room};await exports.verifyEncryptedRecordingFile('channel',event);
 actual={...actual,generation:2};await assert.rejects(exports.verifyEncryptedRecordingFile('channel',event),/differs/);
});
function active(){
  const exports={},window=new EventTarget(),state={stops:0,actor:'actor',token:'token'};
  class Connection{constructor(id,access,media){this.access=access;this.media=media}start(){}stop(){state.stops++}}
  const api={getCurrentUserId:()=>state.actor,getAccessToken:()=>state.token};
  vm.runInNewContext(source('active-meeting.ts'),{exports,require:name=>name==='./api'?{api}:name==='./meetings-api'?{MeetingConnection:Connection,meetingsApi:{end:async()=>{}}}:name==='./sfu-api'?{SFUConnection:Connection,sfuApi:{end:async()=>{}}}:{},Set,Map,Promise,window,setTimeout,clearTimeout});
  exports.startMeshMeeting('channel',{meeting:{id:'meeting'},participant:{id:'device'}},{getTracks:()=>[]});return {...exports,state,window};
}
test('an immediate leave waits for the actual registered publication finalizer before stopping the call',async()=>{
  const a=active(),publication=deferred();a.registerMeetingFinalizer('meeting',()=>publication.promise);
  const leaving=a.leaveActiveMeeting();await turn();assert.ok(a.currentMeeting());assert.equal(a.state.stops,0);
  publication.resolve({privatePending:false});await leaving;assert.equal(a.currentMeeting(),null);assert.equal(a.state.stops,1);assert.equal(a.currentRecordingDraftNotice(),null);
});
test('a retained pending part remains explicitly discoverable after leave and its notice is scrubbed on auth changes',async()=>{
  const a=active();a.registerMeetingFinalizer('meeting',async()=>({privatePending:true}));await a.leaveActiveMeeting();
  assert.equal(a.currentMeeting(),null);assert.equal(a.currentRecordingDraftNotice().channelId,'channel');assert.equal(a.currentRecordingDraftNotice().actor,'actor');
  a.window.dispatchEvent(new Event('freecollab:auth-changed'));assert.equal(a.currentRecordingDraftNotice(),null);
});
test('a retention failure keeps the call and available-byte controls alive instead of inventing a private draft',async()=>{
  const a=active();a.registerMeetingFinalizer('meeting',async()=>({privatePending:false,retentionFailed:true}));
  await assert.rejects(a.leaveActiveMeeting(),/Download or discard/);assert.ok(a.currentMeeting());assert.equal(a.state.stops,0);assert.equal(a.currentRecordingDraftNotice(),null);
});
test('terminal Matrix recording control cannot resume, pause or issue a new receipt; exact stop retries are idempotent',async()=>{
  const exports={},events=[];vm.runInNewContext(source('matrix-meeting-capture.ts'),{exports,require:name=>name==='./encrypted-work-api'?{EncryptedWorkClient:class{}}:{},Map,Set});
  const authority=new exports.MatrixCaptureAuthority({channel:'channel',publishCapture:async(...args)=>{events.push(args);return'$event'}}),capture={id:'capture',generation:1,state:'stopped',all_consented:true};
  authority.read=async()=>({snapshot:{host:true},request:{event_id:'$request'},status:{capture}});
  for(const operation of ['start','pause','resume','segment'])await assert.rejects(authority.control(capture,operation,'new-part'),/has stopped/);
  assert.equal((await authority.control(capture,'stop')).capture.state,'stopped');
  const proof={capture_id:'capture',clip_id:'part'};authority.receipts.set('capture\0part',proof);
  assert.equal((await authority.control(capture,'stop','part')).publication_proof,proof);await assert.rejects(authority.control(capture,'stop','other'),/has stopped/);assert.equal(events.length,0);
  assert.equal(exports.terminalMatrixCaptureState([{index_sequence:8,content:{state:'recording'}},{index_sequence:5,content:{state:'stopped'}},{index_sequence:6,content:{state:'paused'}}]),'stopped');
});
test('actual Matrix capture read ignores its in-flight local caption echo at the last-HTTP authorization boundary',async()=>{
 const exports={},kind='eu.freecollab.meeting',protocol='freecollab-matrix-meeting/1',sender='@fc_actor:test',device='verified',capture='11111111-1111-4111-8111-111111111111';
 const context={room_id:'!room:test',connection_id:'c'.repeat(32),matrix_user_id:sender,members:[{matrix_user_id:sender,name:'Actor'}]};
 const content={protocol,room_id:context.room_id,connection_id:context.connection_id,meeting_id:'meeting',proposal_event_id:'$proposal',participant_id:'participant',device_id:device};
 const event=(id,sequence,data)=>({event_id:id,index_sequence:sequence,index_server:'test',type:kind,sender,sender_device_id:device,content:{...content,...data}}),join=event('$join',2,{kind:'join'});
 const indexed=[event('$proposal',1,{kind:'proposal'}),event('$request',3,{kind:'capture-request',capture_id:capture,generation:1,caption_audience:'channel',video:false,captions:true,save_transcript:false,language:'en',roster:[{participant_id:'participant',matrix_user_id:sender,device_id:device,join_event_id:'$join'}]}),event('$consent',4,{kind:'capture-consent',capture_id:capture,generation:1,request_event_id:'$request',granted:true}),event('$state',5,{kind:'capture-state',capture_id:capture,request_event_id:'$request',state:'recording'})];
 const caption={...content,kind:'capture-caption',capture_id:capture,request_event_id:'$request',text:'Approved caption',start:0,end:1};
 const timeline=['~!room:test:pending','$caption'].map(id=>({getType:()=>kind,getContent:()=>caption,getTs:()=>Date.now(),getId:()=>id})),requested=[];
 const dependencies={'./api':{api:{request:async()=>({captions:{languages:['en']}})}},'./encrypted-work-api':{EncryptedWorkClient:class{async verifiedIndexedEvents(){return indexed}},loadEncryptedRoomContext:async()=>context},'./federated-meeting':{MATRIX_MEETING_EVENT:kind,MATRIX_MEETING_PROTOCOL:protocol},'./matrix-meeting-frontier':{meetingAdmissions:async()=>({ended:false,active:new Map([['participant',{participant_id:'participant',matrix_user_id:sender,join}]])})},'./secure-client':{getSecureMessaging:async()=>({events:async()=>timeline})},'./encrypted-room-events':{verifyEncryptedRoomEvents:async(channel,ids)=>{requested.push(...ids);if(ids.some(id=>!id.startsWith('$')))throw Error('Uncommitted local echo cannot be verified');return[event('$caption',0,caption)]}}};
 vm.runInNewContext(source('matrix-meeting-capture.ts'),{exports,require:name=>dependencies[name]||{},Map,Set,Date});
 const authority=new exports.MatrixCaptureAuthority({channel:'channel',context,matrixUser:sender,participant:'participant',device,isHost:()=>true,captureLeaseCurrent:()=>true,meeting:{proposal_event_id:'$proposal',expires_at:new Date(Date.now()+60000).toISOString()}});
 const status=await authority.status();assert.equal(status.capture.state,'recording');assert.equal(status.capture.live_captions[0].text,'Approved caption');assert.deepEqual(requested,['$caption']);
});
test('native encrypted-caption projection verifies committed events and ignores an in-flight local echo',async()=>{
 const exports={},requested=[],capture={id:'capture',generation:1,created_by:'actor',recorder_participant_id:'participant',encrypted_captions:true},data={protocol:'freecollab-native-caption/1',caption_audience:'channel',device_id:'device',recorder_participant_id:'participant',meeting_id:'meeting',capture_id:'capture',generation:1,start:0,end:1,text:'Approved caption'};
 const timeline=['~!room:test:pending','$caption'].map(id=>({getType:()=> 'eu.freecollab.meeting.caption',getContent:()=>data,getId:()=>id,getTs:()=>Date.now()}));
 const dependencies={'./api':{api:{request:async()=>({capture})}},'./secure-client':{getSecureMessaging:async()=>({events:async()=>timeline,roomFor:async()=>({c:{getUserId:()=> '@fc_actor:test'}})})},'./encrypted-work-api':{verifyEncryptedRoomEvent:async(channel,id)=>{requested.push(id);if(!id.startsWith('$'))throw Error('Uncommitted caption');return{sender:'@fc_actor:test',sender_device_id:'device',content:data}}}};
 vm.runInNewContext(source('meeting-capture-api.ts'),{exports,require:name=>dependencies[name]||{},Date});
 const result=await exports.nativeCaptureAuthority('mesh','meeting','channel').status();assert.equal(result.capture.live_captions[0].text,'Approved caption');assert.deepEqual(requested,['$caption']);
});
