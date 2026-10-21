import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source=readFileSync(new URL('../src/lib/personal-oauth-api.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exports={};
vm.runInNewContext(compiled,{exports,require:()=>({api:{}}),URL});
const destination=exports.providerAuthorizationUrl;

test('native and embedded renderers explicitly use standalone browser consent setup',()=>{
  assert.equal(exports.providerConsentNeedsBrowser({}),false);
  assert.equal(exports.providerConsentNeedsBrowser({desktop:true}),true);
  assert.equal(exports.providerConsentNeedsBrowser({mobile:true}),true);
  assert.equal(exports.providerConsentNeedsBrowser({embedded:true}),true);
});

test('provider consent navigation accepts only exact Google and tenant-specific Microsoft destinations',()=>{
  const google='https://accounts.google.com/o/oauth2/v2/auth?response_type=code&state=opaque-browser-state';
  const microsoft='https://login.microsoftonline.com/01234567-89ab-cdef-0123-456789abcdef/oauth2/v2.0/authorize?response_type=code';
  assert.equal(destination(google,'google'),google);
  assert.equal(destination(microsoft,'microsoft'),microsoft);
});

test('provider navigation refuses deceptive hosts, insecure origins, credentials and client-secret query parameters',()=>{
  for(const url of [
    'http://accounts.google.com/o/oauth2/v2/auth',
    'https://accounts.google.com.evil.example/o/oauth2/v2/auth',
    'https://user:password@accounts.google.com/o/oauth2/v2/auth',
    'https://accounts.google.com:444/o/oauth2/v2/auth',
    'https://accounts.google.com/o/oauth2/v2/auth#access_token=secret',
    'https://accounts.google.com/o/oauth2/v2/auth?client_secret=secret',
    'https://accounts.google.com/o/oauth2/v2/auth?ACCESS_TOKEN=secret',
    'https://accounts.google.com/o/oauth2/v2/auth?refresh_token=secret',
    'https://accounts.google.com/o/oauth2/v2/auth?code=secret',
    'javascript:alert(1)',
  ])assert.throws(()=>destination(url,'google'));
  for(const url of [
    'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    'https://login.microsoftonline.com/01234567-89ab-cdef-0123-456789abcdef/unsafe',
    'https://accounts.google.com/o/oauth2/v2/auth',
  ])assert.throws(()=>destination(url,'microsoft'));
});
