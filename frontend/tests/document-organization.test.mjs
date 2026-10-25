import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function fixture(encrypted=false) {
 const state={token:'actor',requests:[],encryptedRequests:[],encrypted,error:false,swap:false};
 const api={getAccessToken:()=>state.token,request:async(path,options)=>{state.requests.push({path,options});return {stacks:[],placements:[],relations:[]};}};
 const exports={}; const source=readFileSync(new URL('../src/lib/document-organization-api.ts',import.meta.url),'utf8');
 const encryptedApi={list:async channel=>{state.encryptedRequests.push(['list',channel]);return {supported:true,documents:[],stacks:[],placements:[],relations:[],message_references:[],stack_comments:[],comments_incomplete:false};},createStack:async(...args)=>{state.encryptedRequests.push(['create',...args]);return {id:args[2],title:args[1]};},previewMessage:async(...args)=>{state.encryptedRequests.push(['preview',...args]);return {id:args[1],channel_id:args[0],text:'Verified encrypted text'};}};
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:name=>name==='./api'?{api}:name==='./encrypted-document-organization-api'?{encryptedDocumentOrganizationApi:encryptedApi}:{getSecureMessaging:async()=>({handles:async()=>{if(state.error)throw Error('Binding unavailable');if(state.swap)state.token='other';return state.encrypted;}})}});
 return {...exports,state};
}
test('encrypted channels delegate organization through the encrypted adapter without native plaintext requests',async()=>{
 const f=fixture(true),view=await f.documentOrganizationApi.list('private');assert.equal(view.supported,true);assert.equal(view.content_mode,'matrix');
 const made=await f.documentOrganizationApi.createStack('private','Private project','id');assert.equal(made.title,'Private project');
 assert.deepEqual(f.state.encryptedRequests,[['list','private'],['create','private','Private project','id']]);assert.equal(f.state.requests.length,0);
});
test('failed binding and account switch fail closed before a metadata request',async()=>{
 for(const reason of ['error','swap']){const f=fixture();f.state[reason]=true;await assert.rejects(f.documentOrganizationApi.createStack('room','Keep private','id'));assert.equal(f.state.requests.length,0);}
});
test('native move and Undo transport preserve the exact server version and old target without carrying document contents',async()=>{
 const f=fixture();await f.documentOrganizationApi.move('room/one','doc','stack',7,'a'.repeat(32));await f.documentOrganizationApi.move('room/one','doc',null,8,null);
 assert.equal(f.state.requests[0].path,'/channels/room%2Fone/shared-document-organization/documents/doc/placement');
 assert.deepEqual(f.state.requests.map(r=>JSON.parse(r.options.body)),[{stack_id:'stack',expected_version:7,stack_generation:'a'.repeat(32)},{stack_id:null,expected_version:8,stack_generation:null}]);
});
test('suggestions use visible same-channel active title words only, exclude saved pairs and create no request',()=>{
 const f=fixture(),doc={id:'a',channel_id:'room',title:'Design research',archived:false};
 const visible=[doc,{...doc,id:'b',title:'Research notes'},{...doc,id:'c',title:'Design sketches'},{...doc,id:'foreign',channel_id:'other'},{...doc,id:'archived',archived:true},{...doc,id:'irrelevant',title:'Budget review'}];
 const proposals=f.suggestDocumentRelations(doc,visible,[{first_document_id:'a',second_document_id:'c'}]);
 assert.deepEqual(JSON.parse(JSON.stringify(proposals)).map(r=>[r.document.id,r.terms]),[['b',['research']]]);assert.equal(f.state.requests.length,0);
 assert.equal(f.suggestDocumentRelations({...doc,archived:true},visible,[]).length,0);
});
test('comment-grounded proposals cite only the selected page current comments and never cross channels or resolved comments',()=>{
 const f=fixture(),source={id:'source',channel_id:'room',title:'Working brief',archived:false},budget={...source,id:'budget',title:'Budget assumptions'},foreign={...source,id:'foreign',channel_id:'other',title:'Budget memo'};
 const comments=[{id:'evidence',channel_id:'room',document_id:'source',text:'Check budget assumptions',resolved:false},{id:'foreign-comment',channel_id:'other',document_id:'source',text:'Other content',resolved:false},{id:'resolved',channel_id:'room',document_id:'source',text:'Resolved topic',resolved:true}];
 const result=JSON.parse(JSON.stringify(f.suggestDocumentRelations(source,[source,budget,foreign],[],comments)));
 assert.equal(result.length,1);assert.equal(result[0].document.id,'budget');assert.deepEqual(result[0].commentIds,['evidence']);assert.deepEqual(result[0].terms,['budget','assumptions']);assert.equal(f.state.requests.length,0);
});
test('saving a reviewed message includes exact source digest and target creation generation and encrypted preview never falls back',async()=>{
 const f=fixture();await f.documentOrganizationApi.saveMessage('room',{id:'stack',generation:'a'.repeat(32)},{id:'message',expected_text_sha256:'b'.repeat(64)},'request-id');
 assert.deepEqual(JSON.parse(f.state.requests[0].options.body),{id:'request-id',message_id:'message',expected_text_sha256:'b'.repeat(64),stack_generation:'a'.repeat(32)});
 const encrypted=fixture(true);assert.equal((await encrypted.documentOrganizationApi.previewMessage('room','message')).text,'Verified encrypted text');assert.deepEqual(encrypted.state.encryptedRequests,[['preview','room','message']]);assert.equal(encrypted.state.requests.length,0);
});
