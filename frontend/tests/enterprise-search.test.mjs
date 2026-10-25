import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture({ secure = false } = {}) {
  const state = { token: 'owner-session', calls: [], secureCalls: 0, sources: [] };
  const api = { getAccessToken: () => state.token, request: async (path, options) => {
    state.calls.push({path, options}); state.onRequest?.();
    if (path.endsWith('/search/sources')) return {items:state.sources, complete:true, skipped:[]};
    if (path === '/search/resolve') return state.sources[0];
    return [];
  } };
  const modules = { './api':{api}, './secure-client':{getSecureMessaging:async()=>({handles:async()=>secure})},
    './shared-documents-api':{sharedDocumentsApi:{list:async()=>[{id:'doc',created_by:'owner',created_at:'2026-10-21'}]},readSharedDocumentForSearch:async()=>{
      state.secureCalls++; return {title:'Private title',text:'Private decrypted spruce',sequence:1};}},
    './project-tasks-api':{projectTasksApi:{projects:async()=>[],tasks:async()=>[]}},
    './activity-feed-api':{activityFeedApi:{posts:async()=>({items:[],next_before_id:null})}},
    './encrypted-search-sources':{additionalEncryptedSources:async()=>({items:[],skipped:[],complete:true})},
    './files-api':{listChannelFiles:async()=>[],downloadChannelFile:async()=>{throw Error('Unexpected download');}},
  };
  const source=readFileSync(new URL('../src/lib/enterprise-search.ts',import.meta.url),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const exports={};vm.runInNewContext(compiled,{exports,require:name=>modules[name],DOMException,URL,TextDecoder,Uint8Array,AbortController});
  return {...exports,state};
}
const source = {id:'doc',channel_id:'a',kind:'document',title:'Living design',text:'Spruce and birch',created_by:'owner',created_at:'2026-10-21T10:00:00',version:'one',parent_id:null,truncated:false};

test('literal local source matching supports every facet without mutating query or source',()=>{
  const f=fixture();
  assert.ok(f.sourceMatches(source,'SPRUCE living',{channelId:'a',author:'owner',kind:'document',after:'2026-10-20',before:'2026-10-22',documentId:'doc'}));
  for(const filter of [{channelId:'other'},{author:'other'},{kind:'task'},{after:'2026-10-22'},{before:'2026-10-20'},{documentId:'other'}]) assert.equal(f.sourceMatches(source,'spruce',filter),false);
  assert.equal(f.sourceMatches(source,'[spruce]'),false);
  assert.equal(f.state.calls.length,0);
});

test('native content sources load without any query payload',async()=>{
  const f=fixture();f.state.sources=[source];
  assert.equal((await f.loadSearchSources({id:'a'})).items[0].text,source.text);
  assert.equal(f.state.calls[0].path,'/channels/a/search/sources');
  assert.equal(f.state.calls[0].options.body,undefined);
});

test('encrypted documents are reconstructed on device and never sent to native or external search',async()=>{
  const f=fixture({secure:true});const result=await f.loadSearchSources({id:'a'});
  assert.equal(result.items[0].text,'Private decrypted spruce');assert.equal(result.items[0].secure,true);
  assert.equal(f.state.secureCalls,1);assert.equal(f.state.calls.length,0);
});

test('account change during native source read discards content',async()=>{
  const f=fixture();f.state.sources=[source];f.state.onRequest=()=>{f.state.token='replacement';};
  await assert.rejects(f.loadSearchSources({id:'a'}),/account changed/);
});

test('citation open performs a fresh current-source authorization request',async()=>{
  const f=fixture();f.state.sources=[source];
  await f.verifySearchSource({...source,reference:'encrypted-source-reference'},{id:'a'});
  assert.equal(f.state.calls[0].path,'/search/resolve');
  assert.equal(JSON.parse(f.state.calls[0].options.body).reference,'encrypted-source-reference');
});

test('secure citation rejects a changed or removed current encrypted revision',async()=>{
  const f=fixture({secure:true});
  await assert.rejects(f.verifySearchSource({...source,secure:true,version:'stale'},{id:'a'}),/changed or access/);
  assert.equal(f.state.calls.length,0);
});
