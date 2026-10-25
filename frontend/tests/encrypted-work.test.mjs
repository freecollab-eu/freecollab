import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import ts from 'typescript';
import * as Y from 'yjs';
import { fieldReferences } from './load-field-references.mjs';

function compile(path, dependencies = {}, extra = {}) {
  const output = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, Date, Intl, TextEncoder, TextDecoder, Uint8Array, URL, btoa, atob, crypto: webcrypto,
    require: name => { assert.ok(name in dependencies, name); return dependencies[name]; }, ...extra });
  return exports;
}
const calendar = compile('../src/lib/calendar.ts', { './field-references': fieldReferences });
const references = compile('../src/lib/encrypted-work-references.ts');
const legacy = compile('../src/lib/federated-work-model.ts', { './calendar': calendar, './encrypted-work-references': references });
const anchors = compile('../src/lib/document-anchors.ts',{yjs:Y});
const model = compile('../src/lib/encrypted-work-model.ts', { './federated-work-model': legacy, './encrypted-work-references': references, './document-anchors':anchors });
const connection = 'a'.repeat(32), otherConnection = 'b'.repeat(32);
const alice = '@fc_alice:matrix.a.example.com', bob = '@fc_bob:matrix.b.example.com';
const context = { protocol: legacy.WORK_PROTOCOL, encrypted_work_protocol: model.ENCRYPTED_WORK_PROTOCOL, mode: 'federated',
  connection_id: connection, channel_id: 'room', workspace_id: 'workspace', room_id: '!shared:matrix.a.example.com',
  matrix_user_id: alice, local_server: 'matrix.a.example.com', peer_server: 'matrix.b.example.com',
  members: [{ matrix_user_id: alice, name: 'Alice' }, { matrix_user_id: bob, name: 'Bob' }],
  work_protocols: [legacy.WORK_PROTOCOL, model.ENCRYPTED_WORK_PROTOCOL], room_protocols: [], resource_kinds: model.ENCRYPTED_WORK_KINDS,
  encrypted_work_supported: true, limits: { index_events_per_installation: 2000, page_size: 50, max_document_state_bytes: 524288 } };
const id = async (actor = alice, seed = 'resource') => connection + ':' + await legacy.authorNamespace(actor) + ':' + await model.deterministicWorkUuid(seed);
const task = () => ({ title: 'A private next step', body: 'A reviewed description', status: 'todo', priority: 'normal', assignee_matrix_id: null, project_id: null, due_date: null, checklist: [], file_ids: [], decision_ids: [] });
async function revision({ kind = 'task', fields = task(), event = '$root', parents = [], author = alice, resource } = {}) {
  resource ||= await id(author);
  return model.parseEncryptedRevision({ event_id: event, sender: author, timestamp: 1, type: model.ENCRYPTED_WORK_REVISION,
    content: { protocol: model.ENCRYPTED_WORK_PROTOCOL, connection_id: connection, id: resource, kind, fields, parents, archived: false } }, context);
}

test('typed encrypted resources reject unknown actions, local identities and invalid dates', () => {
  assert.equal(model.validateEncryptedFields('task', task(), connection).status, 'todo');
  for (const fields of [{ ...task(), api_key: 'never' }, { ...task(), assignee_matrix_id: 'native-bob' }, { ...task(), due_date: '2026-02-30' },
    { ...task(), file_ids: ['native-file'] }, { ...task(), project_id: otherConnection + ':' + '1'.repeat(16) + ':12345678-1234-4123-8123-123456789abc' }]) {
    assert.throws(() => model.validateEncryptedFields('task', fields, connection));
  }
  assert.throws(() => model.validateEncryptedFields('workflow', { title: 'Bad', body: '', steps: [{ id: 'one', kind: 'execute_http', title: 'No', body: '' }] }, connection));
  assert.throws(() => model.validateEncryptedFields('linked-file', { title: 'Bad', body: '', url: 'javascript:alert(1)', target_id: '' }, connection));
  assert.throws(() => model.validateEncryptedFields('linked-file', { title: 'Bad', body: '', url: 'https://user:secret@example.com/file', target_id: '' }, connection));
});

test('verified references bind exact global IDs, UTF16 spans and unchanged selected text', async () => {
  const resource = await id(), body = '🪵 @Alice and @Plan';
  const references = { body: [{ kind: 'participant', id: alice, label: 'Alice', start: 3, end: 9 }, { kind: 'resource', id: resource, label: 'Plan', resource_kind: 'task', start: 14, end: 19 }] };
  const normalized = model.validateEncryptedFields('feed', { title: 'Update', body, references }, connection);
  assert.equal(normalized.references.body[0].id, alice); assert.equal(normalized.references.body[1].id, resource);
  for (const invalid of [{ ...references, body: [{ ...references.body[0], id: 'alice' }] },
    { ...references, body: [{ ...references.body[0], end: 8 }] }, { ...references, body: [{ ...references.body[1], id: resource.replace(connection, otherConnection) }] }]) {
    assert.throws(() => model.validateEncryptedFields('feed', { title: 'Update', body, references: invalid }, connection));
  }
});

test('all supported collection kinds retain opaque references without a plaintext sidecar', () => {
  const kinds = ['document', 'file', 'linked-document', 'task', 'feed', 'message', 'project', 'decision', 'calendar', 'clip', 'workflow'];
  const fields = model.validateEncryptedFields('collection', { title: 'Materials', body: '', items: kinds.map((kind, index) => ({ kind, id: 'opaque-' + index })) }, connection);
  assert.equal(fields.items.length, kinds.length);
  assert.throws(() => model.validateEncryptedFields('collection', { ...fields, items: [fields.items[0], fields.items[0]] }, connection));
  assert.throws(() => model.validateEncryptedFields('collection', { ...fields, items: [{ kind: 'password', id: 'no' }] }, connection));
});

test('creation namespaces prevent a peer claiming another author resource', async () => {
  const resource = await id(alice);
  await assert.rejects(revision({ author: bob, resource }), /namespace/);
  const row = await revision({ resource }); assert.equal(row.sender, alice);
  const reserved = await model.organizationResourceId(connection);
  await assert.rejects(revision({ resource: reserved }), /namespace/);
});

test('simultaneous first organization authors remain real competing roots and merge explicitly', async () => {
  const resource = await model.organizationResourceId(connection), fields = { title: 'Document organization', body: '{}' };
  const first = await revision({ kind: 'organization', resource, fields, author: alice, event: '$left' });
  const second = await revision({ kind: 'organization', resource, fields, author: bob, event: '$right' });
  const conflicted = model.reduceEncryptedRevisions([first, second])[0];
  assert.equal(conflicted.heads.length, 2); assert.equal(conflicted.invalid.length, 0);
  const merged = await revision({ kind: 'organization', resource, fields, author: bob, event: '$merge', parents: ['$left', '$right'] });
  const result = model.reduceEncryptedRevisions([merged, second, first])[0];
  assert.equal(result.conflicts, false); assert.equal(result.selected.event_id, '$merge'); assert.equal(result.history.length, 3);
});

test('missing and cyclic ancestors never replace a verified encrypted version', async () => {
  const root = await revision(), resource = root.id;
  const orphan = await revision({ resource, event: '$orphan', parents: ['$missing'] });
  const left = await revision({ resource, event: '$a', parents: ['$b'] });
  const right = await revision({ resource, event: '$b', parents: ['$a'] });
  const result = model.reduceEncryptedRevisions([root, right, orphan, left])[0];
  assert.equal(result.selected.event_id, '$root'); assert.equal(result.invalid.length, 3);
});

test('workflow owner, fixed plan operations and receipts cannot be forged by another participant', async () => {
  const workflowId = await id(alice, 'workflow'), runId = await id(alice, 'run');
  const fields = { title: 'Review run', body: '', workflow_id: workflowId, template_event: '$template', execution_owner: alice,
    steps: [{ id: 'one', kind: 'task', title: 'Review', body: '' }], completed: [], status: 'queued' };
  const root = await revision({ kind: 'workflow-run', fields, resource: runId });
  await assert.rejects(revision({ kind: 'workflow-run', fields, resource: await id(bob), author: bob }), /owner/);
  for (const change of [{ completed: [{ step_id: 'unknown', id: await id() }] }, { status: 'completed' },
    { steps: Array.from({ length: 13 }, (_, index) => ({ id: String(index), kind: 'task', title: 'T', body: '' })) }]) {
    assert.throws(() => model.validateEncryptedFields('workflow-run', { ...fields, ...change }, connection));
  }
  const foreign = await revision({ kind: 'workflow-run', fields: { ...fields, execution_owner: bob, status: 'cancelled' }, author: bob, resource: runId, event: '$foreign', parents: ['$root'] });
  const result = model.reduceEncryptedRevisions([root, foreign])[0];
  assert.equal(result.selected.fields.status, 'queued'); assert.equal(result.created_by, alice);
});

function inputHelpers() {
  return compile('../src/components/EncryptedWorkInput.tsx', { react: {}, 'react/jsx-runtime': {}, '../i18n': { t: x => x },
    '../lib/encrypted-work-references': references, '../lib/api': { api: {} }, '../lib/collaboration-context': {}, './CollaborationInput': {} });
}
test('editing before a selected peer reference moves spans; editing its label removes its identity', () => {
  const { reconcileEncryptedReferences } = inputHelpers();
  const selected = [{ kind: 'participant', id: bob, label: 'Bob', start: 3, end: 7 }];
  const shifted = reconcileEncryptedReferences('Hi @Bob', 'Hello @Bob', selected);
  assert.equal(shifted.length, 1); assert.equal(shifted[0].start, 6); assert.equal(shifted[0].id, bob);
  assert.equal(reconcileEncryptedReferences('Hi @Bob', 'Hi @Bobby', selected).length, 0);
});

async function clientFixture() {
  const state = { token: 'session', actor: 'alice', rows: [], sends: [], writes: [], pointerStore: [], held: null };
  const window = new EventTarget();
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => state.actor };
  const transport = compile('../src/lib/encrypted-work-api.ts', {
    './api': { api }, './secure-client': { getSecureMessaging: async () => secure },
    './federated-work-api': { CandidateFault: class extends Error {}, trustedEvent() {}, validateContext: x => x },
    './encrypted-work-references':references, './federated-work-model': legacy, './encrypted-work-model': model,
    './federated-work-pointers': {
      pointerSessionDigest: async () => 'digest', pendingWorkPointers: async () => [],
      storeWorkPointer: async row => state.pointerStore.push(row), removeWorkPointer: async () => {},
    },
  }, { window });
  const events = new Map();
  const secure = { sendEvent: async (_channel, type, content) => {
    const event = { event_id: '$sent-' + (state.sends.length + 1), sender: alice, timestamp: state.sends.length + 1, type, content };
    state.sends.push(event); events.set(event.event_id, event);
    if (state.held) await state.held;
    return { event_id: event.event_id };
  } };
  const client = new transport.EncryptedWorkClient('room', { token: () => state.token, actor: () => state.actor, secure: async () => secure,
    request: async (path, options) => { state.writes.push({ path, options });
      if (options?.method === 'POST') { const event = events.get(JSON.parse(options.body).matrix_event_id); state.rows.push(await model.parseEncryptedRevision(event, context)); }
      return {};
    } });
  client.load = async () => ({ context, records: model.reduceEncryptedRevisions(state.rows), legacyRecords: [], warnings: [], pending_pointers: 0, quarantined_event_ids: [] });
  return { client, state, window, model, transport };
}

test('actual save callback sends only ciphertext event pointers and rejects stale resource heads', async () => {
  const f = await clientFixture();
  const first = await f.client.save('feed', { title: 'Private title', body: 'Private body' }, undefined, { id: 'stable-draft' });
  assert.equal(first.selected.fields.title, 'Private title');
  assert.equal(f.state.writes.length, 1);
  assert.deepEqual(Object.keys(JSON.parse(f.state.writes[0].options.body)), ['matrix_event_id']);
  assert.ok(!JSON.stringify(f.state.pointerStore).includes('Private title'));
  const concurrent = await revision({ kind: 'feed', resource: first.id, fields: { title: 'Peer edit', body: '' }, event: '$peer', parents: [first.selected.event_id], author: bob });
  f.state.rows.push(concurrent);
  await assert.rejects(f.client.save('feed', { title: 'Old edit', body: '' }, first), /changed/);
  assert.equal(f.state.sends.length, 1); f.client.stop();
});

test('an auth ABA during encrypted send prevents stale pointer publication', async () => {
  const f = await clientFixture(); let release;
  f.state.held = new Promise(resolve => release = resolve);
  const sending = f.client.save('feed', { title: 'Private', body: '' });
  while (!f.state.sends.length) await new Promise(resolve => setTimeout(resolve, 0));
  f.state.token = 'other'; f.window.dispatchEvent(new Event('freecollab:auth-changed')); f.state.token = 'session';
  release(); await assert.rejects(sending, /changed/);
  assert.equal(f.state.writes.length, 0); assert.equal(f.state.pointerStore.length, 0);
});

test('bounded encrypted workflow executes reviewed steps once and resumes acknowledged output IDs', async () => {
  const f = await clientFixture();
  const template = await f.client.save('workflow', { title: 'Shared recipe', body: '', steps: [
    { id: 'one', kind: 'task', title: 'Review material', body: 'Use the shared source' },
    { id: 'two', kind: 'decision', title: 'Record decision', body: 'Review the outcome' },
  ] });
  const queued = await f.client.startWorkflow(template, 'review-run');
  const complete = await f.client.resumeWorkflow(queued);
  assert.equal(complete.selected.fields.status, 'completed'); assert.equal(complete.selected.fields.completed.length, 2);
  const outputs = model.reduceEncryptedRevisions(f.state.rows).filter(row => row.selected.fields.origin_run);
  assert.equal(outputs.length, 2); assert.equal(outputs.find(row => row.kind === 'task').selected.fields.status, 'todo');
  const before = f.state.sends.length; await f.client.resumeWorkflow(complete); assert.equal(f.state.sends.length, before);
  f.client.stop();
});

test('cancelled encrypted run does not execute any future operation', async () => {
  const f = await clientFixture();
  const template = await f.client.save('workflow', { title: 'Recipe', body: '', steps: [{ id: 'one', kind: 'feed', title: 'Do not post', body: '' }] });
  const queued = await f.client.startWorkflow(template);
  const cancelled = await f.client.setRunStatus(queued, 'cancelled');
  const before = f.state.sends.length; await f.client.resumeWorkflow(cancelled);
  assert.equal(f.state.sends.length, before); assert.equal(model.reduceEncryptedRevisions(f.state.rows).filter(row => row.kind === 'feed').length, 0);
  f.client.stop();
});

async function guardedReader({changeMembers=false,changeRevocations=false,counter={trusted:0},sourceSender=alice}={}) {
  const resource=await id(),event={event_id:'$read',sender:alice,sender_device_id:'ALICE',timestamp:1,type:model.ENCRYPTED_WORK_REVISION,content:{protocol:model.ENCRYPTED_WORK_PROTOCOL,connection_id:connection,id:resource,kind:'feed',fields:{title:'Verified private source',body:'Never return after revoked authority'},parents:[],archived:false}};
  let scopes=0,deviceReads=0;
  const api={getAccessToken:()=> 'session',getCurrentUserId:()=> 'alice'};
  const transport=compile('../src/lib/encrypted-work-api.ts',{'./api':{api},'./secure-client':{},'./federated-work-api':{CandidateFault:class extends Error{},validateContext:x=>x,trustedEvent:async()=>{counter.trusted++;return event;}},'./encrypted-work-references':references,'./federated-work-model':legacy,'./encrypted-work-model':model,'./federated-work-pointers':{pointerSessionDigest:async()=> 'digest',pendingWorkPointers:async()=>[],removeWorkPointer:async()=>{}}},{window:new EventTarget()});
  const client=new transport.EncryptedWorkClient('room',{token:()=> 'session',actor:()=> 'alice',secure:async()=>({roomFor:async()=>({roomId:context.room_id,c:{getUserId:()=>alice,getDeviceId:()=> 'ALICE'}})}),request:async path=>{
    if(path.endsWith('/context')){scopes++;return {...context,members:changeMembers&&scopes>1?context.members.filter(m=>m.matrix_user_id!==bob):context.members};}
    if(path.startsWith('/secure/devices/revocations')){deviceReads++;return {revoked_devices:changeRevocations&&deviceReads>1?[{matrix_user_id:alice,device_id:'ALICE'}]:[]};}
    const empty={connection_id:connection,status:'active',items:[],next_after:0,has_more:false},after=Number(new URL(path,'https://fixture.test').searchParams.get('local_after')||0);return {connection_id:connection,local:{...empty,items:after?[]:[{sequence:1,matrix_event_id:'$read',matrix_user_id:sourceSender}],next_after:1},remote:empty};
  }});
  return client;
}
test('actual encrypted reader rechecks current room and device authority after asynchronous event verification',async()=>{
 for(const change of [{changeMembers:true},{changeRevocations:true}]){const client=await guardedReader(change);await assert.rejects(client.load(),/authority changed/);assert.equal(client.hasCurrentScope(),false);client.stop();}
 const allowed=await guardedReader();const view=await allowed.load();assert.equal(view.records[0].selected.fields.title,'Verified private source');allowed.stop();
});

test('only exact verified historical spans survive target removal; forged old metadata and copied spans need current access',async()=>{
 const source=await id(),prior={body:[{kind:'resource',id:source,label:'Plan',start:7,end:12,resource_kind:'task'}]},before={body:'Review @Plan'},after={body:'🪵 Review @Plan'},moved={body:[{resource_kind:'task',kind:'resource',id:source,label:'Plan',start:10,end:15}]};
 assert.equal(references.addedEncryptedReferences(before,prior,after,moved),undefined);
 const duplicated={body:[prior.body[0],{...prior.body[0],start:13,end:18}]};
 assert.equal(references.addedEncryptedReferences(before,prior,{body:'Review @Plan @Plan'},duplicated).body.length,1);
 assert.equal(references.addedEncryptedReferences(before,undefined,before,prior).body.length,1);
 assert.throws(()=>references.addedEncryptedReferences({body:'Different content'},prior,before,prior),/no longer matches/);
 const forged=JSON.parse('{"__proto__":[]}');assert.throws(()=>references.validateEncryptedReferences(before,forged),/field/);
 const f=await clientFixture(),feed=await f.client.save('feed',{title:'Old reference',body:'Review @Bob',references:{body:[{kind:'participant',id:bob,label:'Bob',start:7,end:11}]}});
 const actualLoad=f.client.load;f.client.load=async()=>({...await actualLoad(),context:{...context,members:context.members.filter(row=>row.matrix_user_id!==bob)}});
 const edited=await f.client.save('feed',{...feed.selected.fields,title:'Still editable'},feed);assert.equal(edited.selected.fields.references.body[0].id,bob);
 await assert.rejects(f.client.save('feed',{...edited.selected.fields,body:'Review @Bob @Bob',references:{body:[...edited.selected.fields.references.body,{kind:'participant',id:bob,label:'Bob',start:12,end:16}]}},edited),/no longer available/);
});

test('encrypted inputs expose local slash tools only under the exact current secure channel host',()=>{
 for(const [host,allowed]of [[{channelId:'room',secure:true,isCurrent:()=>true},true],[{channelId:'other',secure:true,isCurrent:()=>true},false],[{channelId:'room',secure:false,isCurrent:()=>true},false],[{channelId:'room',secure:true,isCurrent:()=>false},false]]){
  const controls=[],module=compile('../src/components/EncryptedWorkInput.tsx',{react:{useId:()=> 'input',useRef:value=>({current:value}),useState:value=>[value,()=>{}],useEffect(){}},'react/jsx-runtime':{jsx:(type,props)=>{if(type==='shared-input')controls.push(props);return {type,props};},jsxs:(type,props)=>({type,props})},'../i18n':{t:x=>x},'../lib/api':{api:{getAccessToken:()=> 'token',getCurrentUserId:()=> 'actor'}},'../lib/collaboration-context':{useCollaborationContext:()=>host},'./CollaborationInput':{CollaborationInput:'shared-input'},'../lib/encrypted-work-references':references});
  module.EncryptedWorkInput({channelId:'room',contextId:'retained-resource',value:'',label:'Title',candidates:[],onChange(){}});assert.equal(controls[0].allowCommands,allowed);
 }
});

test('encrypted passage comments retain immutable author text and exact document anchors',async()=>{
 const target=await id(alice,'document'),doc=new Y.Doc();doc.getText('body').insert(0,'Review this passage');const anchor=anchors.createDocumentAnchor(doc,target,1,0,6),fields={title:'Comment',body:'Please check',target_id:target,target_kind:'document',resolved:false,anchor};
 const normalized=model.validateEncryptedFields('comment',fields,connection);assert.equal(normalized.anchor.quote,'Review');
 for(const invalid of [{...anchor,document_id:await id(bob)},{...anchor,source_sequence:501},{...anchor,quote:'x'.repeat(2001)}])assert.throws(()=>model.validateEncryptedFields('comment',{...fields,anchor:invalid},connection));
 const root=await revision({kind:'comment',fields,resource:await id(alice,'comment')}),resource=root.id;
 const changed=await revision({kind:'comment',fields:{...fields,body:'Forged replacement'},resource,event:'$changed',parents:['$root']});
 const foreign=await revision({kind:'comment',fields:{...fields,resolved:true},resource,event:'$foreign',author:bob,parents:['$root']});
 const valid=await revision({kind:'comment',fields:{...fields,resolved:true},resource,event:'$valid',parents:['$root']});
 const result=model.reduceEncryptedRevisions([root,changed,foreign,valid])[0];assert.equal(result.selected.event_id,'$valid');assert.equal(result.selected.fields.body,'Please check');assert.equal(result.invalid.length,1);assert.equal(result.quarantined.length,1);doc.destroy();
});


test('durable indexed event filtering reuses only immutable type hints and preserves exact fresh device trust',async()=>{
 const counter={trusted:0},client=await guardedReader({counter});
 const first=await client.verifiedIndexedEvents([model.ENCRYPTED_WORK_REVISION]);assert.equal(first.length,1);assert.equal(first[0].index_server,context.local_server);assert.equal(first[0].index_sequence,1);assert.equal(counter.trusted,1);
 assert.equal((await client.verifiedIndexedEvents(['unrelated.type'])).length,0);assert.equal(counter.trusted,1);
 assert.equal((await client.verifiedIndexedEvents([model.ENCRYPTED_WORK_REVISION])).length,1);assert.equal(counter.trusted,2);
 assert.equal((await client.load()).records.length,1);assert.equal(counter.trusted,3);client.stop();
 const wrong=await guardedReader({sourceSender:bob});await assert.rejects(wrong.load(),/Invalid encrypted work pointer/);wrong.stop();
});

test('oversized typed workflow drafts never publish an event or create an opaque pointer',async()=>{
 const f=await clientFixture(),fields={title:'Large but structurally valid',body:'',steps:Array.from({length:3},(_,i)=>({id:'step-'+i,kind:'task',title:'Step',body:'x'.repeat(12000)}))};
 fields.body='x'.repeat(40*1024-8-new TextEncoder().encode(JSON.stringify(model.validateEncryptedFields('workflow',fields,connection))).length);
 assert.equal(model.validateEncryptedFields('workflow',fields,connection).steps.length,3);
 await assert.rejects(f.client.save('workflow',fields),/too large/);assert.equal(f.state.sends.length,0);assert.equal(f.state.pointerStore.length,0);assert.equal(f.state.writes.length,0);f.client.stop();
});
