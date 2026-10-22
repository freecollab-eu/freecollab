import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { webcrypto } from 'node:crypto';
const compiled=ts.transpileModule(readFileSync(new URL('../src/lib/slack-secure-import.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const digest=async value=>Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(value))).toString('hex');
function fixture(){
 const exports={},records=new Map(),events=new Map(),transactions=new Map(),uploads=new Map(),calls=[];let token='original-session',device='original-device',failMessage=0,failFile=false,denied=false,pauseAt=0,changeAt=0,current=true;
 const expected=()=>({user:'alice',token});
 const storage={owner:expected,assertOwner:value=>{if(value.token!==token)throw Error('changed')},fingerprint:digest,listPrivate:async()=>[...records].map(([id,value])=>({id,value:structuredClone(value)})),savePrivate:async(kind,id,value)=>{records.set(id,structuredClone(value))},removePrivate:async(kind,id)=>records.delete(id)};
 const eventView=id=>{const value=events.get(id);return {status:value.status??null,getId:()=>id,getRoomId:()=>value.room||'!room',getSender:()=>value.sender,getContent:()=>value.content,getType:()=>value.type,isRedacted:()=>!!value.redacted,isEncrypted:()=>value.wireType!=='plain',getWireType:()=>value.wireType||'m.room.encrypted',getWireContent:()=>({device_id:value.device||device})}};
 const room={roomId:'!room',getLiveTimeline:()=>({getEvents:()=>[...events.keys()].map(eventView)}),getEventForTxnId:()=>undefined};
 const matrix={getDeviceId:()=>device,getUserId:()=>'@fc_alice:fixture.example',decryptEventIfNeeded:async()=>{}};
 const secure={ensureSession:async()=>matrix,refreshBindings:async()=>[{channel_id:'destination',status:'active',room_id:'!room'}],roomFor:async()=>{if(denied)throw Error('revoked');return {room,roomId:'!room'}},sendEvent:async(channel,type,content,txn)=>{
  if(denied)throw Error('revoked');calls.push({channel,type,content,txn});let id=transactions.get(txn);if(!id){id='$'+events.size;transactions.set(txn,id);events.set(id,{sender:'@fc_alice:fixture.example',type,content})}
  if(type==='m.room.message'&&failMessage&&--failMessage===0)throw new TypeError('acknowledgement lost');if(type==='m.room.message'&&pauseAt&&--pauseAt===0)current=false;if(type==='m.room.message'&&changeAt&&--changeAt===0)token='replacement';return {event_id:id};},getDecryptedEvent:async(channel,id)=>eventView(id)};
 const api={request:async(path,options)=>{if(denied)throw Error('revoked');if(path.endsWith('secure-reserve'))return {id:'reservation',channel_id:'destination'};if(path.endsWith('complete-secure'))return {completed:true};return {};}};
 const provider={upload:async(channel,file,options)=>{uploads.set(options.requestId,await file.text());if(failFile){failFile=false;throw new TypeError('file response lost')}return {id:options.requestId}}};
 vm.runInNewContext(compiled,{exports,require:name=>name==='./api'?{api}:name==='./secure-client'?{getSecureMessaging:async()=>secure}:name==='./secure/files'?{secureFileProvider:provider}:name.includes('mobile/storage')?storage:{slackDigest:digest},TextEncoder,Uint8Array,Map,Set,File,Blob,Date,JSON,Error,TypeError,crypto:webcrypto,setTimeout:callback=>{callback()}});
 const details={version:1,source:'slack',source_timestamp:'1704067200.000001',current_text:'historical private source',author:{id:'U1',name:'Historical Alice'},edited_at:null,history:[],reactions:[]};
 const archive={digest:'a'.repeat(64),channels:[{id:'C1',name:'studio',members:[],messages:[{ts:'1704067200.000001',text:'first source',parent:null,files:[],missing:0,details},{ts:'1704067201.000001',text:'reply source',parent:'1704067200.000001',files:[],missing:0,details:{...details,source_timestamp:'1704067201.000001'}}]}],read:()=>new TextEncoder().encode('portable file')};
 const choices={channels:['C1'],mapping:{},prefix:'slack-'};
 return {...exports,archive,choices,records,events,transactions,uploads,calls,token:value=>{token=value},device:value=>{device=value},loseMessage:number=>{failMessage=number},loseFile:()=>{failFile=true},revoke:()=>{denied=true},isCurrent:()=>current,resume:()=>{current=true},pauseMessage:n=>{pauseAt=n},changeOnMessage:n=>{changeAt=n}};
}
test('ambiguous accepted acknowledgement recovers exact authenticated source event without another local echo',async()=>{
 const f=fixture();f.loseMessage(2);await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/acknowledgement lost/);
 assert.equal([...f.records.values()][0].messageIndex,1);const before=f.transactions.size;
 const result=await f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{});assert.equal(result.messages,2);assert.equal(f.transactions.size,before);
 const messages=f.calls.filter(v=>v.type==='m.room.message');assert.equal(messages.length,2);assert.equal(messages[1].content['m.relates_to'].event_id,'$1');
});
test('encrypted file acknowledgement loss reuses upload receipt and advances counters once',async()=>{
 const f=fixture();f.archive.channels[0].messages[0].files=[{path:'files/F1',name:'source.txt',mime:'text/plain'}];f.loseFile();await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/file response lost/);
 const result=await f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{});assert.equal(result.files,1);assert.equal(f.uploads.size,1);assert.equal(result.messages,2);
});
for(const change of ['token','device'])test('resume refuses changed original '+change+' before any content send',async()=>{
 const f=fixture();f.loseMessage(2);await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}));const count=f.calls.length;f[change]('replacement');await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/original sign-in/);assert.equal(f.calls.length,count);
});
test('current access revocation blocks resume without clearing durable progress',async()=>{
 const f=fixture();f.loseMessage(2);await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}));const count=f.calls.length;f.revoke();await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/revoked/);assert.equal(f.calls.length,count);assert.equal([...f.records.values()][0].messageIndex,1);
});
test('oversized historical provenance is chunked and reconstructed with source/sender commitments',async()=>{
 const f=fixture();f.archive.channels[0].messages[0].details.history=[{timestamp:'1704067200.000001',author:{id:'U1',name:'Alice'},text:'prior '.repeat(2200)}];await f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{});
 const message=[...f.events].find(([,v])=>v.type==='m.room.message');const meta=message[1].content['eu.freecollab.slack.import'];assert.ok(meta.parts.length>1);
 const data=await f.readSlackHistory('destination',message[0],true);assert.equal(data.history[0].text,'prior '.repeat(2200));
 f.events.get(meta.parts[0]).sender='@attacker:other.example';await assert.rejects(f.readSlackHistory('destination',message[0],true),/could not be verified/);
});


test('ordinary pause preserves a known server receipt before advancing counters and avoids duplicate local replies',async()=>{
 const f=fixture();f.pauseMessage(2);await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{},f.isCurrent),/paused/);
 const saved=[...f.records.values()][0];assert.equal(saved.messageIndex,1);assert.equal(saved.pendingMain.eventId,'$2');
 f.resume();const result=await f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{},f.isCurrent);assert.equal(result.messages,2);assert.equal(f.calls.filter(row=>row.type==='m.room.message').length,2);assert.equal([...f.records.values()][0].pendingMain,undefined);
});
test('account change after accepted send prevents persisting even its known receipt',async()=>{
 const f=fixture();f.changeOnMessage(1);await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/changed/);
 const saved=[...f.records.values()][0];assert.equal(saved.messageIndex,0);assert.equal(saved.pendingMain.eventId,undefined);assert.equal(saved.processed,0);
});
for(const [name,mutate] of [
 ['queued local echo',value=>{value.status='sending'}],['another source device',value=>{value.device='other-device'}],['plaintext event',value=>{value.wireType='plain'}],['other sender',value=>{value.sender='@attacker:fixture'}],['wrong thread',value=>{value.content['m.relates_to'].event_id='$other'}],['different source archive',value=>{value.content['eu.freecollab.slack.import'].archive_digest='b'.repeat(64)}]
])test('ambiguous acknowledgement cannot recover '+name,async()=>{
 const f=fixture();f.loseMessage(2);await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/acknowledgement lost/);
 const reply=[...f.events.values()].find(value=>value.type==='m.room.message'&&value.content['m.relates_to']);mutate(reply);
 await assert.rejects(f.secureSlackMigration(f.archive,'workspace',f.choices,()=>{}),/acknowledgement is still unavailable/);assert.equal([...f.records.values()][0].processed,1);assert.equal(f.calls.filter(row=>row.type==='m.room.message').length,2);
});
