import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { webcrypto } from 'node:crypto';
import * as fflate from 'fflate';
const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/slack-archive.ts', import.meta.url), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:name=>name==='fflate'?fflate:{FILE_EXPERIENCE_TYPES:[]},TextEncoder,TextDecoder,Uint8Array,DataView,ArrayBuffer,BigInt,Map,Set,Date,JSON,Error,setTimeout,crypto:webcrypto});
const { SlackArchive, parseSlackJson }=exports;
const source=(updates={})=>({
 'users.json':JSON.stringify([{id:'U1',name:'Original Alice'},{id:'U2',name:'Original Bob'}]),
 'channels.json':JSON.stringify([{id:'C1',name:'general',members:['U1','U2']}]),
 'general/2024-01-01.json':JSON.stringify([{type:'message',user:'U1',ts:'1704067200.000002',text:'Current private body',edited:{ts:'1704067300.000001'},edit_history:[{ts:'1704067200.000001',user:'U1',text:'Earlier private body'}],reactions:[{name:'thumbsup',count:2,users:['U1','U2']}],files:[{id:'F1',name:'portable.txt',mimetype:'text/plain'}]},{type:'message',user:'U2',ts:'1704067200.000010',thread_ts:'1704067200.000002',text:'Reply'}]),
 'files/F1':'actual bundled secret bytes',...updates});
const zip=(updates={})=>fflate.zipSync(Object.fromEntries(Object.entries(source(updates)).map(([k,v])=>[k,new TextEncoder().encode(v)])),{level:6});
function locations(bytes){const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.length);let e=bytes.length-22;return {view,e,central:view.getUint32(e+16,true)};}

test('real deflated review preserves complete reaction/edit/source facts and microsecond thread order',async()=>{
 const archive=await new SlackArchive(zip()).review();const [root,reply]=archive.channels[0].messages;
 assert.equal(root.details.history[0].text,'Earlier private body');assert.equal(root.details.reactions[0].users[1].name,'Original Bob');assert.equal(reply.parent,root.ts);assert.equal(archive.digest.length,64);assert.equal(new TextDecoder().decode(archive.read('files/F1')),'actual bundled secret bytes');
});
test('duplicate JSON keys and excessively deep JSON fail before parsing erases evidence',()=>{
 assert.throws(()=>parseSlackJson('{"id":1,"id":2}'),/Duplicate/);assert.throws(()=>parseSlackJson('['.repeat(66)+'0'+']'.repeat(66)),/nesting/);
});
for(const name of ['../escape.json','/absolute.json','general\\escape.json','general/../../escape.json']) test('unsafe ZIP path '+name,()=>{assert.throws(()=>new SlackArchive(zip({[name]:'{}'})),/Unsafe/);});
test('ZIP64 and encryption flags rejected before inflate',()=>{
 let bytes=zip(),{view,e}=locations(bytes);view.setUint16(e+10,65535,true);view.setUint16(e+8,65535,true);assert.throws(()=>new SlackArchive(bytes),/ZIP64|limit/);
 bytes=zip();let value=locations(bytes);value.view.setUint16(value.central+8,1,true);assert.throws(()=>new SlackArchive(bytes));
});
test('CRC corruption and local-central size disagreement fail',async()=>{
 let bytes=zip();let {view,central}=locations(bytes);view.setUint32(central+16,0,true);view.setUint32(14,0,true);await assert.rejects(new SlackArchive(bytes).review(),/CRC/);
 bytes=zip();view=locations(bytes).view;view.setUint32(22,1,true);assert.throws(()=>new SlackArchive(bytes),/sizes disagree/);
});
test('a dishonest expanded bound stops streaming inflate immediately',async()=>{
 const bytes=zip();const {view,central}=locations(bytes);view.setUint32(central+24,1,true);view.setUint32(22,1,true);await assert.rejects(new SlackArchive(bytes).review(),/declared bound/);
});
test('duplicate source timestamps and malformed historical counts are rejected',async()=>{
 const rows=JSON.parse(source()['general/2024-01-01.json']);rows[1].ts=rows[0].ts;await assert.rejects(new SlackArchive(zip({'general/2024-01-01.json':JSON.stringify(rows)})).review(),/Duplicate/);
 rows.pop();rows[0].reactions[0].count=0;await assert.rejects(new SlackArchive(zip({'general/2024-01-01.json':JSON.stringify(rows)})).review(),/reaction/);
});
test('standard message_changed envelope retains the previous body',async()=>{
 const rows=JSON.parse(source()['general/2024-01-01.json']);const old={...rows[0]};delete old.edit_history;delete old.edited;rows.push({type:'message',subtype:'message_changed',message:{...old,text:'Changed body',edited:{ts:'1704067500.000001'}},previous_message:old});const archive=await new SlackArchive(zip({'general/2024-01-01.json':JSON.stringify(rows)})).review();assert.equal(archive.channels[0].messages.length,2);assert.equal(archive.channels[0].messages[0].text,'Changed body');assert.ok(archive.channels[0].messages[0].details.history.some(v=>v.text==='Current private body'));
});

test('oversizedFile rejected before allocating its ArrayBuffer',async()=>{let allocated=false;await assert.rejects(exports.reviewSlackArchive({size:50*1024*1024+1,arrayBuffer:async()=>{allocated=true;return new ArrayBuffer(0)}}),/50 MB/);assert.equal(allocated,false)});
