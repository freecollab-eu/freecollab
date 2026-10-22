import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const compiled=ts.transpileModule(readFileSync(new URL('../src/lib/secure/transaction-retry.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
function fixture(){
 const exports={};vm.runInNewContext(compiled,{exports,WeakMap,Map,Promise,JSON,Error});
 const events=new Map(),client={getUserId:()=> '@alice:example'},room={getEventForTxnId:txn=>events.get(txn)};let attempts=0,fail=false,hold;
 const send=existing=>{attempts++;const event=existing||{status:'sending',getSender:()=>client.getUserId(),getRoomId:()=>'!room',getType:()=>'m.room.message',getContent:()=>({body:'source'}),isRedacted:()=>false,isEncrypted:()=>true,getWireType:()=>'m.room.encrypted',getId:()=>event.status==='sent'?'$accepted':'~pending'};events.set('stable',event);return (async()=>{if(hold)await hold;if(fail){event.status='not_sent';throw Error('ack lost')}event.status='sent';return {event_id:'$accepted'}})()};
 const run=(epoch='epoch-1',content={body:'source'})=>exports.sendOrResumeEncryptedTransaction(client,room,'!room','m.room.message',content,'stable',epoch,send);
 return {run,events,client,room,send,attempts:()=>attempts,fail:v=>fail=v,hold:promise=>hold=promise};
}
test('confirmed identical SDK event returns existing receipt without duplicate local echo or send',async()=>{const f=fixture();await f.run();assert.equal((await f.run()).event_id,'$accepted');assert.equal(f.attempts(),1)});
test('failed same-epoch SDK event is reused with the original transaction',async()=>{const f=fixture();f.fail(true);await assert.rejects(f.run(),/ack lost/);const event=f.events.get('stable');f.fail(false);await f.run();assert.equal(f.events.get('stable'),event);assert.equal(f.attempts(),2)});
test('changed membership epoch cannot authorize old encrypted NOT_SENT ciphertext',async()=>{const f=fixture();f.fail(true);await assert.rejects(f.run());f.fail(false);await assert.rejects(f.run('epoch-2'),/older or unavailable membership/);assert.equal(f.attempts(),1)});
test('restored NOT_SENT event without original epoch cannot be resent',async()=>{const f=fixture(),old=fixture();old.fail(true);await assert.rejects(old.run());f.events.set('stable',old.events.get('stable'));await assert.rejects(f.run(),/older or unavailable membership/);assert.equal(f.attempts(),0)});
test('same active transaction coalesces while differing content or membership is rejected',async()=>{const f=fixture();let release;f.hold(new Promise(resolve=>release=resolve));const first=f.run(),second=f.run();await assert.rejects(f.run('epoch-2'),/different content or membership/);await assert.rejects(f.run('epoch-1',{body:'changed'}),/different content or membership/);release();await Promise.all([first,second]);assert.equal(f.attempts(),1)});
test('existing sender, content, type and redaction must match before receipt reuse',async()=>{for(const mutate of [event=>event.getSender=()=>'@other:example',event=>event.getContent=()=>({body:'other'}),event=>event.getType=()=>'other',event=>event.isRedacted=()=>true]){const f=fixture();await f.run();mutate(f.events.get('stable'));await assert.rejects(f.run(),/does not match/);assert.equal(f.attempts(),1)}});

test('plaintext SENT echo cannot satisfy encrypted transaction receipt',async()=>{const f=fixture();await f.run();f.events.get('stable').isEncrypted=()=>false;await assert.rejects(f.run(),/no confirmed encrypted receipt/);assert.equal(f.attempts(),1)});
