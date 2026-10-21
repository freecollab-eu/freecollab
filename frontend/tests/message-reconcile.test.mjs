import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/lib/message-reconcile.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{exports});
const merge=exports.reconcileMessageChanges;
const message=(id,time,extra={})=>({id,channel_id:'room',author_id:'person',content:id,created_at:time,encrypted:true,...extra});
test('reconciliation retains a bounded historical window while applying edits, tombstones and missed new messages',()=>{
  const current=[message('shown','2026-10-21T10:00:00'),message('deleted','2026-10-21T10:01:00Z')];
  const result=merge(current,[message('old-unloaded','2025-01-01T10:00:00Z'),{...current[0],content:'edited'}, {...current[1],deleted:true,content:'[Message deleted]'},message('new','2026-10-21T10:02:00+00:00')]);
  assert.deepEqual(JSON.parse(JSON.stringify(result.map(row=>row.id))),['shown','deleted','new']);
  assert.equal(result[0].content,'edited');assert.equal(result[1].deleted,true);assert.equal(current[0].content,'shown');
});
test('thread reconciliation never inserts replies into the main timeline or another open thread',()=>{
  const reply=message('reply','2026-10-21T10:00:00Z',{parent_id:'parent'});
  assert.equal(merge([], [reply]).length,0);
  assert.equal(merge([], [reply],'another-parent').length,0);
  assert.equal(merge([], [reply],'parent')[0].id,'reply');
});
test('missed events with equal timestamps are ordered deterministically and duplicate batches remain idempotent',()=>{
  const changes=[message('b','2026-10-21T10:00:00Z'),message('a','2026-10-21T10:00:00')];
  const once=merge([],changes),twice=merge(once,changes);
  assert.deepEqual(JSON.parse(JSON.stringify(once.map(row=>row.id))),['a','b']);
  assert.deepEqual(twice,once);
});
