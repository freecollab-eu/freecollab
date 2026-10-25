'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), crypto = require('node:crypto');
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path');
const updates = require('../src/updates.cjs');
const keys = crypto.generateKeyPairSync('ed25519');
const config = updates.configuration({ manifest_url: 'https://updates.example.test/stable.json', public_key: keys.publicKey.export({ type: 'spki', format: 'pem' }) });
const base = () => ({ protocol: 'freecollab-update-v1', version: '0.13.1', sequence: 2, platform: 'linux', arch: 'x64',
  issued_at: new Date(Date.now() - 1000).toISOString(), expires_at: new Date(Date.now() + 86400000).toISOString(),
  url: 'https://updates.example.test/FreeCollab.AppImage', size: 8, sha256: 'a'.repeat(64), format: 'appimage' });
const envelope = value => { const payload = Buffer.from(JSON.stringify(value)); return { payload: payload.toString('base64'), signature: crypto.sign(null, payload, keys.privateKey).toString('base64') }; };
const context = { platform: 'linux', arch: 'x64', currentVersion: '0.13.0', sequence: 1 };
test('only the operator pinned Ed25519 signature can authorize an exact platform package', () => {
  assert.equal(updates.verifyManifest(envelope(base()), config, context).version, '0.13.1');
  const value = envelope(base()); value.payload = Buffer.from(JSON.stringify({ ...base(), url: 'https://updates.example.test/attacker' })).toString('base64');
  assert.throws(() => updates.verifyManifest(value, config, context), /signature/);
  const other = crypto.generateKeyPairSync('ed25519');
  assert.throws(() => updates.configuration({ manifest_url: 'https://updates.example.test/x', public_key: other.privateKey.export({ type:'pkcs8',format:'pem' }) + 'invalid' }));
});
test('valid signatures cannot bypass replay, downgrade, recipient, lifetime or package bounds', () => {
  for (const patch of [{ sequence: 1 }, { version: '0.12.0' }, { platform: 'win32' }, { arch: 'arm64' },
    { url: 'https://unapproved.example.test/x' }, { url: 'http://updates.example.test/x' }, { size: updates.MAX_PACKAGE + 1 },
    { expires_at: new Date(Date.now() - 1).toISOString() }, { format: 'zip' }, { unexpected: true }]) {
    assert.throws(() => updates.verifyManifest(envelope({ ...base(), ...patch }), config, context), JSON.stringify(patch));
  }
  assert.throws(() => updates.configuration({ manifest_url: 'https://user:secret@updates.example.test/x', public_key: keys.publicKey.export({ type:'spki',format:'pem' }) }));
});
test('streamed packages require exact signed hash and size and remove partial stages', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fc-updates-'));
  try {
    const bytes = Buffer.from('verified-package'), manifest = { ...base(), size: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex') };
    const staged = await updates.stagePackage(new Response(bytes), manifest, root);
    assert.deepEqual(await fs.readFile(staged.file), bytes);
    const before = await fs.readdir(root);
    await assert.rejects(updates.stagePackage(new Response(Buffer.from('different')), manifest, root));
    assert.deepEqual(await fs.readdir(root), before);
    await assert.rejects(updates.stagePackage(new Response(Buffer.alloc(bytes.length + 1)), manifest, root), /size/);
    assert.deepEqual(await fs.readdir(root), before);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
function zip(name, mode = 0o100644, value = 'x', extra = Buffer.alloc(0)) {
  const text = Buffer.from(name), body = Buffer.from(value), local = Buffer.alloc(30), central = Buffer.alloc(46), end = Buffer.alloc(22);
  local.writeUInt32LE(0x04034b50); local.writeUInt32LE(body.length,18); local.writeUInt32LE(body.length,22); local.writeUInt16LE(text.length,26); local.writeUInt16LE(extra.length,28);
  central.writeUInt32LE(0x02014b50); central.writeUInt32LE(body.length,20); central.writeUInt32LE(body.length,24); central.writeUInt16LE(text.length,28); central.writeUInt16LE(extra.length,30); central.writeUInt32LE((mode * 65536) >>> 0,38);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(1,8); end.writeUInt16LE(1,10); end.writeUInt32LE(central.length+text.length+extra.length,12); end.writeUInt32LE(local.length+text.length+extra.length+body.length,16);
  return Buffer.concat([local,text,extra,body,central,text,extra,end]);
}
test('ZIP preflight rejects traversal, absolute/drive paths, symlinks and local-header confusion', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fc-update-zip-')), file = path.join(root,'package.zip');
  try {
    await fs.writeFile(file,zip('resources/app.asar')); assert.deepEqual(await updates.zipEntries(file), ['resources/app.asar']);
    for (const [name, mode] of [['../escape',0o100644],['/escape',0o100644],['C:/escape',0o100644],['resources/link',0o120777],['resources\\escape',0o100644],['CON.txt',0o100644]]) {
      await fs.writeFile(file,zip(name,mode)); await assert.rejects(updates.zipEntries(file), /Unsafe/);
    }
    const wrong = zip('resources/app.asar'); wrong[30] = 120; await fs.writeFile(file,wrong); await assert.rejects(updates.zipEntries(file), /local/);
  } finally { await fs.rm(root,{recursive:true,force:true}); }
});
async function waitFor(predicate) {
  const deadline = Date.now() + 7000;
  while (Date.now() < deadline) { if (await predicate()) return; await new Promise(resolve => setTimeout(resolve,50)); }
  throw new Error('The real update guardian did not complete');
}
test('the real Linux guardian restores the owned previous binary after failed startup', async () => {
  if (process.platform !== 'linux') return;
  const root = await fs.mkdtemp(path.join(os.tmpdir(),'fc-update-rollback-')), target=path.join(root,'FreeCollab.AppImage'), resumed=path.join(root,'resumed');
  try {
    const previous = '#!/bin/sh\n: > ' + JSON.stringify(resumed) + '\n'; await fs.writeFile(target,previous,{mode:0o700});
    const bytes=Buffer.from('#!/bin/sh\nexit 23\n'), manifest={...base(),size:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
    const staged=await updates.stagePackage(new Response(bytes),manifest,path.join(root,'updates'));
    const plan=await updates.prepareInstallation(staged,{platform:'linux',target,launcher:target,root:path.join(root,'updates'),parentPid:99999999}); plan.launch();
    await waitFor(async()=>{try{await fs.access(resumed);return true;}catch{return false;}});
    assert.equal(await fs.readFile(target,'utf8'),previous);
    await assert.rejects(fs.access(path.join(root,'updates/pending.json')));
  } finally { await fs.rm(root,{recursive:true,force:true}); }
});
test('startup acknowledgment is bound to the pending nonce, actual application version and sequence', async () => {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'fc-update-health-')), nonce='a'.repeat(48);
  try {
    await fs.writeFile(path.join(root,'pending.json'),JSON.stringify({nonce,version:'0.13.1',sequence:9,created_at:new Date().toISOString()}));
    await assert.rejects(updates.acknowledgeStartup(root,'0.13.0',nonce));
    await assert.rejects(updates.acknowledgeStartup(root,'0.13.1','b'.repeat(48)));
    assert.equal(await updates.acknowledgeStartup(root,'0.13.1',nonce),true);
    assert.equal(await fs.readFile(path.join(root,'health-'+nonce),'utf8'),nonce);
    assert.equal(JSON.parse(await fs.readFile(path.join(root,'sequence.json'))).sequence,9);
  } finally { await fs.rm(root,{recursive:true,force:true}); }
});

test('macOS bundle-relative framework symlinks are accepted while escaping links and path overrides are rejected',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'fc-update-mac-links-')),file=path.join(root,'bundle.zip');
  try{
    const name='FreeCollab.app/Contents/Frameworks/Electron.framework/Versions/Current';
    await fs.writeFile(file,zip(name,0o120777,'A'));assert.deepEqual(await updates.zipEntries(file,{platform:'darwin'}),[name]);
    await assert.rejects(updates.zipEntries(file,{platform:'win32'}));
    for(const target of ['/tmp/escape','../../../../../../../../escape','C:/escape','loop/../../../../../../escape']){
      await fs.writeFile(file,zip(name,0o120777,target));await assert.rejects(updates.zipEntries(file,{platform:'darwin'}));
    }
    await fs.writeFile(file,zip('FreeCollab.app/Contents/Link',0o120777,'link'));await assert.rejects(updates.zipEntries(file,{platform:'darwin'}),/Cyclic/);
    const unicodePathOverride=Buffer.from([0x75,0x70,1,0,1]);
    await fs.writeFile(file,zip('safe/file',0o100644,'x',unicodePathOverride));await assert.rejects(updates.zipEntries(file),/override/);
    await fs.writeFile(file,zip('safe//file'));await assert.rejects(updates.zipEntries(file),/Unsafe/);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});

test('a staged package altered after signature/hash verification cannot reach the installer',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'fc-update-stage-tamper-')),target=path.join(root,'app');try{await fs.writeFile(target,'owned-previous',{mode:0o700});const bytes=Buffer.from('reviewed-next'),manifest={...base(),size:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex')},staged=await updates.stagePackage(new Response(bytes),manifest,path.join(root,'updates'));await fs.writeFile(staged.file,'tampered-next');await assert.rejects(updates.prepareInstallation(staged,{platform:'linux',target,launcher:target,root:path.join(root,'updates')}),/changed/);assert.equal(await fs.readFile(target,'utf8'),'owned-previous');await assert.rejects(fs.access(path.join(root,'updates/pending.json')));}finally{await fs.rm(root,{recursive:true,force:true});}
});
