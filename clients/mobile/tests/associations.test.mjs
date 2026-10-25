import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { associationFiles, writeAssociationFiles } from '../scripts/associations.mjs';
const config = { origin:'https://collab.example.test', applicationId:'eu.example.collab', appleTeamId:'AB123CD456',
  androidFingerprints:['ab:'.repeat(31)+'ab'] };
test('platform association files bind the actual app identity and only explicit resource links', () => {
  const files=associationFiles(config), apple=files['apple-app-site-association'], android=files['assetlinks.json'][0];
  assert.deepEqual(apple.applinks.details[0].appIDs,['AB123CD456.eu.example.collab']);
  assert.deepEqual(apple.applinks.details[0].components,[{'/':'/open',comment:'Only explicit FreeCollab resource links open the app.'}]);
  assert.equal(android.target.package_name,'eu.example.collab');
  assert.equal(android.target.sha256_cert_fingerprints[0],config.androidFingerprints[0].toUpperCase());
  assert.deepEqual(associationFiles({origin:config.origin,applicationId:config.applicationId}),{});
});
test('unconfigured or malformed signing identities never produce a fabricated verification document', () => {
  for(const patch of [{appleTeamId:'not-a-team'}, {androidFingerprints:['FAKE']}, {applicationId:'eu.example/escape'},
    {origin:'http://collab.example.test'},{origin:'https://user:secret@collab.example.test'}]) assert.throws(()=>associationFiles({...config,...patch}));
});
test('the configured public documents are usable static files without storing private signing material',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'fc-native-associations-'));
  try {
    const values=await writeAssociationFiles(root,config);
    for(const[name,value]of Object.entries(values))assert.deepEqual(JSON.parse(await fs.readFile(path.join(root,'.well-known',name),'utf8')),value);
    assert.deepEqual((await fs.readdir(path.join(root,'.well-known'))).sort(),['apple-app-site-association','assetlinks.json']);
  } finally {await fs.rm(root,{recursive:true,force:true});}
});

test('reconfiguration removes only stale owned platform association documents',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'fc-assoc-reconfigure-'));try{await writeAssociationFiles(root,config);await fs.writeFile(path.join(root,'.well-known','operator-owned'),'keep');await writeAssociationFiles(root,{origin:config.origin,applicationId:config.applicationId});assert.deepEqual(await fs.readdir(path.join(root,'.well-known')),['operator-owned']);}finally{await fs.rm(root,{recursive:true,force:true});}
});
