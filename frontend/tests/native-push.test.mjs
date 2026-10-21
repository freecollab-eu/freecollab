import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture({ native = true } = {}) {
  const state = { token: 'native-session', requests: [], registered: 0, unregistered: 0, events: [], switchDuringPermission: false };
  const plugin = { registerPush: async () => { state.registered++; if (state.switchDuringPermission) state.token = 'replacement-session'; return {platform:'android',token:'opaque_device_capability_1234567890'}; }, unregisterPush:async()=>{state.unregistered++;} };
  const api = {getAccessToken:()=>state.token,request:async(...args)=>{state.requests.push(args);}};
  const window = {Capacitor:{isNativePlatform:()=>native,registerPlugin:()=>plugin},dispatchEvent:event=>state.events.push(event.type)};
  const exports = {};
  const source = readFileSync(new URL('../src/lib/native-push.ts',import.meta.url),'utf8');
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{exports,require:()=>({api}),window,Event});
  return {...exports,state};
}

test('valid native opaque references and one-use proof require exact identifier lengths',()=>{
  const f=fixture();
  assert.equal(f.validNativePushItem({key:'a'.repeat(32),id:'a'.repeat(32),type:'incoming_call'}),true);
  assert.equal(f.validNativePushItem({key:'b'.repeat(32),device_id:'b'.repeat(32),challenge:'c'.repeat(43),type:'subscription_confirmation'}),true);
  for (const item of [null,{}, {key:'a'.repeat(32),id:'a'.repeat(32),type:'join_call'}, {key:'other',id:'a'.repeat(32),type:'activity'}, {key:'a'.repeat(32),id:'javascript:alert(1)',type:'activity'}, {key:'b'.repeat(32),device_id:'b'.repeat(32),challenge:'short',type:'subscription_confirmation'}]) assert.equal(f.validNativePushItem(item),false);
});

test('ordinary browser cannot register a native capability',async()=>{
  const f=fixture({native:false});
  await assert.rejects(f.enableNativePush(true),/native mobile app/);
  assert.equal(f.state.registered,0);assert.equal(f.state.requests.length,0);
});

test('device consent precedes scoped API registration and retains call opt-in explicitly',async()=>{
  const f=fixture();await f.enableNativePush(false);
  assert.equal(f.state.registered,1);assert.equal(f.state.requests.length,1);
  const [path,options]=f.state.requests[0];assert.equal(path,'/mobile/native/devices');
  const body=JSON.parse(options.body);assert.equal(body.calls_enabled,false);assert.equal(body.consent,true);assert.equal(body.platform,'android');
  assert.equal(path.includes(body.token),false);assert.deepEqual(f.state.events,['freecollab:mobile-state']);
});

test('account switch while OS registration is pending cannot attach capability to replacement session',async()=>{
  const f=fixture();f.state.switchDuringPermission=true;
  await assert.rejects(f.enableNativePush(true),/sign-in changed/);
  assert.equal(f.state.requests.length,0);assert.equal(f.state.unregistered,1);
});
