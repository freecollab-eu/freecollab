import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { randomUUID } from 'node:crypto';
function fixture() {
  const exports = {}, state={actor:'actor',token:'token',now:1000000,memory:false,storageCalls:0},storage=new Map();
  const api={isMemorySession:()=>state.memory,getAccessToken:()=>state.token,getCurrentUserId:()=>state.actor,getChannel:async id=>({id,workspace_id:'workspace'})};
  const context={exports,URL,Set,Uint8Array,TextDecoder,atob,location:{origin:'https://collab.example.test'},
    Date:{now:()=>state.now},crypto:{randomUUID},sessionStorage:{getItem:key=>{state.storageCalls++;return storage.get(key)||null},setItem:(key,value)=>{state.storageCalls++;return storage.set(key,value)},removeItem:key=>{state.storageCalls++;return storage.delete(key)}},require:()=>({api})};
  const source=fs.readFileSync(new URL('../src/lib/native-links.ts',import.meta.url),'utf8');
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
  return { ...exports,context,state,api };
}
const source={workspace_id:'workspace',channel_id:'channel',kind:'task',item_id:'task',project_id:'project'};
test('actual universal-link parser binds exact origin, known keys, IDs and project',()=>{
  const f=fixture(),base='https://collab.example.test/open?'+new URLSearchParams(source);
  assert.deepEqual(JSON.parse(JSON.stringify(f.parseNativeLink(base))),source);
  for(const url of [base.replace('collab.example.test','other.example.test'),base+'&token=secret',base+'&__proto__=secret',
    base+'&channel_id=other',base+'#secret',base.replace('https:','http:'),base.replace('project_id=project','project_id=')])
    assert.throws(()=>f.parseNativeLink(url));
  assert.throws(()=>f.validateNativeOpenSource({kind:'message',item_id:'message'}));
  assert.throws(()=>f.validateNativeOpenSource({...source,workspace_id:123}));
});
test('browser fallback resource fragments preserve only validated routing',()=>{
  const f=fixture(),fragment='#open='+Buffer.from(JSON.stringify(source)).toString('base64url');
  assert.deepEqual(JSON.parse(JSON.stringify(f.parseOpenFragment(fragment))),source);
  assert.equal(f.parseOpenFragment('#unrelated=1'),null);
  assert.throws(()=>f.parseOpenFragment('#open='+Buffer.from(JSON.stringify({...source,credential:'secret'})).toString('base64url')));
});
test('authorization uses a fresh channel and rejects late actor/token or channel changes',async()=>{
  for(const change of ['actor','token','channel','workspace','mutated-input']){
    const f=fixture();let release;
    f.api.getChannel=()=>new Promise(resolve=>release=resolve);
    const input={...source},pending=f.authorizeNativeSource(input);
    if(change==='actor')f.state.actor='other';
    if(change==='token')f.state.token='other';
    if(change==='mutated-input')input.item_id='a-later-unreviewed-target';
    release({id:change==='channel'?'other':'channel',workspace_id:change==='workspace'?'other':'workspace'});
    if(change==='mutated-input') assert.equal((await pending).item_id,'task'); else await assert.rejects(pending);
  }
});

test('workflow runs retain a distinct canonical native kind',()=>{const f=fixture();assert.equal(f.parseNativeLink('https://collab.example.test/open?'+new URLSearchParams({...source,kind:'workflow-run',item_id:'run-one'})).kind,'workflow-run');assert.throws(()=>f.parseNativeLink('https://collab.example.test/open?'+new URLSearchParams({...source,kind:'workflow-anything'})));});

test('SSO routing hints expire and an older completion cannot clear a newer identical hint',()=>{
 const f=fixture(),first=f.rememberNativeSource(source),next=f.rememberNativeSource(source);assert.notEqual(first.generation,next.generation);f.clearNativeSource(first);assert.equal(f.pendingNativeSource().generation,next.generation);f.clearNativeSource(next);assert.equal(f.pendingNativeSource(),null);
 f.rememberNativeSource(source);f.state.now+=600001;assert.equal(f.pendingNativeSource(),null);
 f.rememberNativeSource(source);f.state.actor='a-different-account';assert.equal(f.pendingNativeSource(),null);
});

test('sample and embed routing hints never borrow or alter the retained standalone account hint',()=>{const f=fixture(),normal=f.rememberNativeSource(source),before=f.state.storageCalls;f.state.memory=true;assert.equal(f.pendingNativeSource(),null);const sample=f.rememberNativeSource({...source,item_id:'ephemeral'});assert.equal(f.pendingNativeSource().source.item_id,'ephemeral');f.clearNativeSource(sample);assert.equal(f.state.storageCalls,before);f.state.memory=false;assert.equal(f.pendingNativeSource().generation,normal.generation);});
