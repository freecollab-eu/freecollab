import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const exports = {};
const compile = text => ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(compile(readFileSync(new URL('../src/lib/channel-metadata.ts', import.meta.url), 'utf8')), { exports });
const record = (extra={}) => ({channel_id:'channel',version:1,secure:true,authoritative:true,name:null,description:null,matrix_event_id:'$committed',updated_by:'admin',can_manage:true,...extra});
const event = (extra={}) => ({id:'$committed',roomId:'!room:local.test',sender:'@fc_admin:local.test',type:'eu.freecollab.channel.metadata',encrypted:true,redacted:false,failed:false,content:{name:'Nord',description:'Readable only on devices',channel_id:'channel',version:1,registration_required:true},...extra});
const verify = (row=record(), item=event()) => exports.verifiedChannelMetadata(row,'!room:local.test','@fc_me:local.test',item);

test('verified metadata is exact-event/room/sender/revision bound and decrypted only on the client', () => {
  assert.equal(verify().name,'Nord');
  for (const changes of [{id:'$uncommitted'},{roomId:'!other:local.test'},{sender:'@fc_other:local.test'},{type:'m.room.message'},{encrypted:false},{redacted:true},{failed:true},{content:{...event().content,version:2}},{content:{...event().content,channel_id:'other'}},{content:{...event().content,registration_required:false}}]) assert.throws(()=>verify(record(),event(changes)));
  for (const changes of [{authoritative:false},{secure:false},{matrix_event_id:null},{version:0},{version:1.5},{updated_by:'other'}]) assert.throws(()=>verify(record(changes)));
});

test('strict legacy compatibility excludes all new uploads, including a first failed CAS', () => {
  assert.equal(exports.legacyChannelName({name:'Old channel'}),'Old channel');
  assert.equal(exports.legacyChannelName(event().content),null);
  assert.equal(exports.legacyChannelName({name:'Uploaded but uncommitted',registration_required:true}),null);
  assert.equal(exports.legacyChannelName({name:'bad\u0000name'}),null);
});

test('native and encrypted reads reject unsafe fields and never use native fallback text', () => {
  assert.throws(()=>exports.nativeChannelMetadata(record({name:'Server fallback'})));
  for (const name of ['', ' ', 'x'.repeat(101), 'bad\u0000name', '\ud800']) assert.throws(()=>verify(record(),event({content:{...event().content,name}})));
  for (const description of ['x'.repeat(1001),'bad\u0001','\udfff']) assert.throws(()=>verify(record(),event({content:{...event().content,description}})));
  assert.equal(verify(record(),event({content:{...event().content,name:'Nord 🌱',description:'First\r\nSecond'}})).description,'First\nSecond');
});

test('older and foreign metadata cannot roll back an acknowledged header revision', () => {
  const channel={id:'channel',secure:true,name:'New',description:'New details',metadata_version:2};
  assert.equal(exports.applyChannelMetadata(channel,verify()),channel);
  assert.equal(exports.applyChannelMetadata(channel,{...verify(),channel_id:'other',version:3}),channel);
  assert.equal(exports.applyChannelMetadata(channel,{...verify(),secure:false,version:3}),channel);
  assert.equal(exports.applyChannelMetadata(channel,{...verify(),name:'Third',version:3}).name,'Third');
});

function realSecure(overrides={}) {
  const source=readFileSync(new URL('../src/lib/secure/index.ts',import.meta.url),'utf8'), parsed=ts.createSourceFile('secure.ts',source,ts.ScriptTarget.Latest,true);
  const names=['metadataEvent','readChannelMetadata','writeChannelMetadata'];
  const functions=parsed.statements.filter(node=>ts.isFunctionDeclaration(node)&&names.includes(node.name?.text)).map(node=>node.getText(parsed)).join('\n');
  const published=[],readIds=[],module={},client={getUserId:()=> '@fc_me:local.test'},room={oldState:{paginationToken:null}};
  const sdkEvent=item=>({getId:()=>item.id,getRoomId:()=>item.roomId,getSender:()=>item.sender,getType:()=>item.type,isEncrypted:()=>item.encrypted,isRedacted:()=>item.redacted,isDecryptionFailure:()=>item.failed,getContent:()=>item.content});
  const context={exports:module,...exports,configuration:{getAccessToken:()=> 'token'},generation:1,client,METADATA:'eu.freecollab.channel.metadata',roomFor:async()=>({c:client,room,roomId:'!room:local.test'}),request:async()=>record(),events:async()=>[sdkEvent(event({id:'$new-uncommitted',content:{...event().content,version:2,name:'Uncommitted'}}))],validMetadataSender:async()=>true,publishChannelMetadata:value=>published.push(value),secureMessaging:{getDecryptedEvent:async(channel,id)=>{readIds.push(id);return sdkEvent(event());}},...overrides};
  vm.runInNewContext(compile(functions+'\nexports.read=readChannelMetadata;exports.write=writeChannelMetadata;'),context);return {...module,published,readIds};
}

test('the actual secure reader resolves committed API pointer even when a newer upload exists',async()=>{
  const secure=realSecure();assert.equal((await secure.read('channel')).name,'Nord');assert.deepEqual(secure.readIds,['$committed']);assert.equal(secure.published.length,1);
  const initial=realSecure({request:async()=>record({authoritative:false,version:0,matrix_event_id:null})});await assert.rejects(initial.read('channel'),/unavailable/);assert.equal(initial.published.length,0);
});

test('the actual secure writer never publishes a failed registration or plaintext REST fields',async()=>{
  const calls=[],secure=realSecure({sendEvent:async(channel,type,content)=>{assert.equal(content.registration_required,true);assert.equal(content.version,2);return {event_id:'$uploaded'};},request:async(path,body,method)=>{calls.push({body,method});if(method==='PATCH')throw new Error('CAS conflict');return record();}});
  await assert.rejects(secure.write('channel','New name','New description',1),/CAS conflict/);assert.equal(secure.published.length,0);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1])),{body:{expected_version:1,matrix_event_id:'$uploaded'},method:'PATCH'});
});

test('an account change during the metadata lookup prevents any encrypted upload',async()=>{
  let token='first-account',uploads=0;
  const secure=realSecure({configuration:{getAccessToken:()=>token},request:async()=>{token='second-account';return record();},sendEvent:async()=>{uploads++;return {event_id:'$should-not-exist'};}});
  await assert.rejects(secure.write('channel','Old account draft','Private draft',1),/session changed/);
  assert.equal(uploads,0);assert.equal(secure.published.length,0);
});

test('the actual list loader preserves an acknowledged save when an older list arrives late',async()=>{
  const source=readFileSync(new URL('../src/components/Chat.tsx',import.meta.url),'utf8'),parsed=ts.createSourceFile('chat.tsx',source,ts.ScriptTarget.Latest,true);let declaration;
  function walk(node){if(ts.isVariableDeclaration(node)&&node.name.getText(parsed)==='loadChannels')declaration=node;ts.forEachChild(node,walk);}walk(parsed);assert.ok(declaration);
  let release;const pending=new Promise(resolve=>release=resolve),module={},reads=[];
  let rows=[{id:'channel',name:'Original',type:'private',secure:false}],active=rows[0];
  const context={exports:module,retainChannelMetadata:exports.retainChannelMetadata,api:{getAccessToken:()=> 'token',getChannels:()=>pending},workspaceRef:{current:'workspace'},channelsRequestRef:{current:0},channelsWorkspaceRef:{current:null},channelSnapshotsRef:{current:rows},activeChannelRef:{current:'channel'},embeddedChannelId:undefined,initialChannelId:undefined,setChannelError:()=>{},setLoading:()=>{},refreshChannelMetadata:id=>reads.push(id),setChannels:update=>rows=typeof update==='function'?update(rows):update,setActiveChannel:update=>active=typeof update==='function'?update(active):update};
  vm.runInNewContext(compile('const '+declaration.getText(parsed)+';exports.load=loadChannels;'),context);
  const loading=module.load();active={...active,name:'Acknowledged rename',description:'Saved description',metadata_version:2};rows=[active];release([{id:'channel',name:'Original',type:'private',secure:false,role:'member'}]);await loading;
  assert.equal(active.name,'Acknowledged rename');assert.equal(rows[0].name,'Acknowledged rename');assert.equal(active.metadata_version,2);assert.equal(active.role,'member','Fresh membership fields still update');assert.ok(reads.includes('channel'),'The authoritative metadata is refreshed after an unversioned list');
  assert.equal(exports.applyChannelMetadata(active,{...verify(),secure:false,version:1,name:'Stale revision'}),active);
  assert.equal(exports.retainChannelMetadata({id:'other',name:'Other',secure:false},active).name,'Other');
});
