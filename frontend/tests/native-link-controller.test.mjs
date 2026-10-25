import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {randomUUID} from 'node:crypto';
import ts from 'typescript';
const settle=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
const source={workspace_id:'workspace',channel_id:'channel',kind:'document',item_id:'document'};
const link=(item='document')=>'https://collab.example.test/open?'+new URLSearchParams({...source,item_id:item});
function fixture(onOpen,browserSource){
  const state={token:'token',actor:'actor',pending:browserSource?{}:{url:link(),generation:randomUUID()},cleared:[],errors:[]},timers=new Map(),storage=new Map();let timer=0,cleanup;
  const window=new EventTarget();window.Capacitor={isNativePlatform:()=>true,registerPlugin:()=>({getPendingLink:async()=>({...state.pending}),clearPendingLink:async expected=>{state.cleared.push(expected);if(state.pending.url===expected.url&&state.pending.generation===expected.generation)state.pending={};}})};
  const location={origin:'https://collab.example.test',pathname:'/',hash:'',href:'https://collab.example.test/'};
  const api={isMemorySession:()=>false,getAccessToken:()=>state.token,getCurrentUserId:()=>state.actor,getChannel:async id=>({id,workspace_id:'workspace'})};
  const base={URL,Set,Uint8Array,TextDecoder,atob,window,location,crypto:{randomUUID},Date,sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}};
  const compile=path=>ts.transpileModule(fs.readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const lib={};vm.runInNewContext(compile('../src/lib/native-links.ts'),{...base,exports:lib,require:()=>({api})});
  const exports={};vm.runInNewContext(compile('../src/components/NativeLinkController.tsx'),{...base,exports,history:{replaceState:()=>{}},queueMicrotask,setTimeout:fn=>{timers.set(++timer,fn);return timer},clearTimeout:id=>timers.delete(id),require:name=>name==='react'?{useRef:value=>({current:value}),useEffect:fn=>{cleanup=fn()}}:name.endsWith('/api')?{api}:lib});
  if(browserSource){location.hash='#open='+Buffer.from(JSON.stringify(browserSource)).toString('base64url');location.href=location.origin+'/'+location.hash;lib.rememberNativeSource(browserSource);}
  exports.NativeLinkController({onOpen,onError:message=>state.errors.push(message)});
  return {state,api,window,location,lib,async tick(){const next=[...timers.values()];timers.clear();for(const fn of next)fn();await settle()},stop(){cleanup?.()}};
}
test('a newer OS generation invalidates an awaited dirty guard and preserves the newer pending link',async()=>{
  const calls=[];const f=fixture((value,current)=>new Promise(resolve=>calls.push({value,current,resolve})));await settle();assert.equal(calls.length,1);
  const old=f.state.pending;f.state.pending={url:link('new-document'),generation:randomUUID()};await f.tick();assert.equal(calls[0].current(),false);
  calls[0].resolve(true);await settle();assert.equal(f.state.cleared.length,0);assert.equal(calls.length,2);assert.equal(calls[1].value.item_id,'new-document');
  calls[1].resolve(true);await settle();assert.deepEqual(JSON.parse(JSON.stringify(f.state.cleared)),[{url:link('new-document'),generation:f.state.cleared[0].generation}]);assert.notEqual(f.state.cleared[0].generation,old.generation);assert.equal(f.state.errors.length,0);f.stop();
});
test('the same URI retries only for a fresh OS event, and accepted leases survive hint consumption',async()=>{
  const calls=[];const f=fixture(async(value,current)=>{calls.push(current);return calls.length>1});await settle();await f.tick();assert.equal(calls.length,1);
  f.state.pending={url:link(),generation:randomUUID()};await f.tick();await settle();assert.equal(calls.length,2);assert.equal(f.state.cleared.length,1);assert.equal(calls[1](),true);
  f.state.actor='another';f.window.dispatchEvent(new Event('freecollab:auth-changed'));assert.equal(calls[1](),false);f.stop();
});
test('hash navigation and auth ABA cancel a held fresh channel authorization',async()=>{
  let release;const opened=[];const f=fixture(async value=>{opened.push(value);return true});
  f.api.getChannel=id=>new Promise(resolve=>{release=()=>resolve({id,workspace_id:'workspace'})});await settle();assert.ok(release);
  f.state.token='other';f.window.dispatchEvent(new Event('freecollab:auth-changed'));f.state.token='token';f.window.dispatchEvent(new Event('freecollab:auth-changed'));
  release();await settle();assert.equal(opened.length,0);assert.equal(f.state.cleared.length,0);f.stop();
});

test('a newer browser fragment cannot be opened by an older held authorization',async()=>{
 const opened=[],held=[];const f=fixture(async value=>{opened.push(value.item_id);return true},source);
 f.api.getChannel=id=>new Promise(resolve=>held.push(()=>resolve({id,workspace_id:'workspace'})));await settle();assert.equal(held.length,1);
 const newer={...source,item_id:'newer-document'};f.location.hash='#open='+Buffer.from(JSON.stringify(newer)).toString('base64url');f.location.href=f.location.origin+'/'+f.location.hash;f.window.dispatchEvent(new Event('hashchange'));await settle();
 held[0]();await settle();assert.equal(opened.length,0);assert.equal(held.length,2);held[1]();await settle();assert.deepEqual(opened,['newer-document']);f.stop();
});
