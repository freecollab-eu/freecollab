'use strict';
// The publisher key is supplied by the installation operator, never downloaded.
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
const MAX_PACKAGE = 768 * 1024 * 1024, MAX_EXPANDED = 2 * 1024 * 1024 * 1024;
function httpsUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.hash || url.search || value.length > 2048)
    throw new Error('Updates require an explicit HTTPS address without credentials, query or fragment.');
  return url;
}
function version(value) {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)) throw new Error('Invalid stable update version.');
  return value.split('.').map(Number);
}
function newer(next, previous) {
  const a = version(next), b = version(previous);
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}
function configuration(input) {
  if (!input || typeof input !== 'object') throw new Error('Automatic updates have not been configured by this installation operator.');
  const manifest = httpsUrl(input.manifest_url);
  if (typeof input.public_key !== 'string' || input.public_key.length > 2048 || !/^-----BEGIN PUBLIC KEY-----\r?\n[A-Za-z0-9+/=\r\n]+-----END PUBLIC KEY-----\s*$/.test(input.public_key))
    throw new Error('Configure only the publisher public key; private signing keys never belong in the app.');
  const key = crypto.createPublicKey(input.public_key);
  if (key.asymmetricKeyType !== 'ed25519') throw new Error('Updates require a pinned Ed25519 publisher public key.');
  const origins = new Set([manifest.origin]);
  for (const origin of input.artifact_origins || []) {
    const url = httpsUrl(origin);
    if (url.pathname !== '/') throw new Error('An update artifact origin cannot include a path.');
    origins.add(url.origin);
  }
  return { manifest: manifest.href, key, origins };
}
function verifyManifest(envelope, config, { platform, arch, currentVersion, sequence = 0, now = Date.now() }) {
  if (!envelope || Object.keys(envelope).sort().join(',') !== 'payload,signature'
      || typeof envelope.payload !== 'string' || typeof envelope.signature !== 'string'
      || envelope.payload.length > 16384 || !/^[A-Za-z0-9+/]+={0,2}$/.test(envelope.payload)
      || !/^[A-Za-z0-9+/]+={0,2}$/.test(envelope.signature)) throw new Error('Invalid signed update manifest.');
  const payload = Buffer.from(envelope.payload, 'base64'), signature = Buffer.from(envelope.signature, 'base64');
  if (signature.length !== 64 || !crypto.verify(null, payload, config.key, signature)) throw new Error('Update publisher signature is invalid.');
  const value = JSON.parse(payload.toString('utf8'));
  const keys = ['protocol','version','sequence','platform','arch','issued_at','expires_at','url','size','sha256','format'];
  if (Object.keys(value).sort().join(',') !== keys.sort().join(',') || value.protocol !== 'freecollab-update-v1'
      || value.platform !== platform || value.arch !== arch || !Number.isSafeInteger(value.sequence) || value.sequence <= sequence
      || !newer(value.version, currentVersion) || !Number.isSafeInteger(value.size) || value.size <= 0 || value.size > MAX_PACKAGE
      || typeof value.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.sha256)) throw new Error('Update version, platform, architecture, sequence or size is invalid.');
  const issued = Date.parse(value.issued_at), expires = Date.parse(value.expires_at);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || issued > now + 300000 || expires <= now
      || expires <= issued || expires - issued > 31 * 86400000) throw new Error('The update manifest has expired or has an invalid lifetime.');
  const url = httpsUrl(value.url);
  if (!config.origins.has(url.origin) || value.format !== (platform === 'linux' ? 'appimage' : 'zip'))
    throw new Error('This update has an unapproved artifact origin or package format.');
  return value;
}
async function zipEntries(file, { platform = 'win32' } = {}) {
  const handle = await fs.open(file, 'r');
  try {
    const stat = await handle.stat(), tailSize = Math.min(stat.size, 65557), tail = Buffer.alloc(tailSize);
    await handle.read(tail, 0, tailSize, stat.size - tailSize);
    let end = -1;
    for (let i = tail.length - 22; i >= 0; i--) if (tail.readUInt32LE(i) === 0x06054b50 && i + 22 + tail.readUInt16LE(i + 20) === tail.length) { end = i; break; }
    if (end < 0 || tail.readUInt16LE(end + 4) || tail.readUInt16LE(end + 6) || tail.readUInt16LE(end + 8) !== tail.readUInt16LE(end + 10)) throw new Error('Invalid update ZIP directory.');
    const count = tail.readUInt16LE(end + 10), length = tail.readUInt32LE(end + 12), offset = tail.readUInt32LE(end + 16);
    if (!count || count === 65535 || count > 20000 || length > 16 * 1024 * 1024 || offset + length > stat.size - tailSize + end) throw new Error('Unsupported or oversized update ZIP.');
    const directory = Buffer.alloc(length); await handle.read(directory, 0, length, offset);
    const entries = [], names = new Set(), links = new Map(), linkPaths = new Map(); let cursor = 0, expanded = 0;
    for (let i = 0; i < count; i++) {
      if (cursor + 46 > length || directory.readUInt32LE(cursor) !== 0x02014b50) throw new Error('Invalid update ZIP entry.');
      const flags = directory.readUInt16LE(cursor + 8), method = directory.readUInt16LE(cursor + 10);
      const compressed = directory.readUInt32LE(cursor + 20), size = directory.readUInt32LE(cursor + 24);
      const nameSize = directory.readUInt16LE(cursor + 28), extra = directory.readUInt16LE(cursor + 30), comment = directory.readUInt16LE(cursor + 32);
      const mode = directory.readUInt32LE(cursor + 38) >>> 16, local = directory.readUInt32LE(cursor + 42);
      if (cursor + 46 + nameSize + extra + comment > length) throw new Error('Invalid update ZIP bounds.');
      const name = directory.subarray(cursor + 46, cursor + 46 + nameSize).toString('utf8'), parts = name.split('/');
      if (!name || Buffer.from(name,'utf8').compare(directory.subarray(cursor + 46,cursor + 46 + nameSize)) || (!(flags & 0x800) && /[^\x20-\x7e]/.test(name)) || parts.slice(0,-1).some(part => !part) || directory.readUInt16LE(cursor + 34) !== 0 || name.startsWith('/') || /[\\:\x00-\x1f]/.test(name) || parts.some(part => part === '..' || part === '.')
          || parts.some(part => /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part) || /[. ]$/.test(part))
          || names.has(name.normalize('NFC').toLowerCase()) || flags & 1 || ![0, 8].includes(method)
          || mode && ![0, 0o100000, 0o040000, ...(platform === 'darwin' ? [0o120000] : [])].includes(mode & 0o170000)) throw new Error('Unsafe update archive path, link, type or encoding.');
      validateZipExtra(directory.subarray(cursor + 46 + nameSize, cursor + 46 + nameSize + extra));
      expanded += size; if (expanded > MAX_EXPANDED || size > MAX_PACKAGE || local + 30 + compressed > offset) throw new Error('Expanded update exceeds its bounds.');
      const header = Buffer.alloc(30); await handle.read(header, 0, 30, local);
      if (header.readUInt32LE(0) !== 0x04034b50 || header.readUInt16LE(6) !== flags || header.readUInt16LE(8) !== method
          || header.readUInt16LE(26) !== nameSize) throw new Error('Update ZIP local metadata differs from its signed archive directory.');
      const localName = Buffer.alloc(nameSize); await handle.read(localName, 0, nameSize, local + 30);
      if (localName.toString('utf8') !== name || local + 30 + nameSize + header.readUInt16LE(28) + compressed > offset) throw new Error('Unsafe update ZIP local path or bounds.');
      const localExtra = Buffer.alloc(header.readUInt16LE(28)); await handle.read(localExtra, 0, localExtra.length, local + 30 + nameSize); validateZipExtra(localExtra);
      if ((mode & 0o170000) === 0o120000) {
        if (!name.startsWith('FreeCollab.app/') || size > 2048 || compressed > 4096) throw new Error('Unsafe macOS bundle link.');
        const data = Buffer.alloc(compressed); await handle.read(data, 0, compressed, local + 30 + nameSize + localExtra.length);
        const target = (method === 8 ? require('node:zlib').inflateRawSync(data, { maxOutputLength: 2048 }) : data).toString('utf8');
        if (Buffer.byteLength(target) !== size || !target || /[\\:\x00-\x1f]/.test(target) || target.startsWith('/')) throw new Error('Unsafe macOS bundle link target.');
        links.set(name, target);linkPaths.set(name.normalize('NFC').toLowerCase(),target);
      }
      names.add(name.normalize('NFC').toLowerCase()); entries.push(name); cursor += 46 + nameSize + extra + comment;
    }
    if (cursor !== length) throw new Error('Unexpected update ZIP directory data.');
    for (const name of entries) {
      const pieces = name.split('/');
      for (let i = 1; i < pieces.length; i++) if (linkPaths.has(pieces.slice(0, i).join('/').normalize('NFC').toLowerCase())) throw new Error('An archive entry cannot write through a bundle link.');
    }
    for (const name of links.keys()) {
      let queue = name.split('/'), stack = [], traversed = 0;
      while (queue.length) {
        const part = queue.shift(); if (!part || part === '.') continue;
        if (part === '..') { if (stack.length < 2) throw new Error('A bundle link escapes the application.'); stack.pop(); continue; }
        stack.push(part); if (stack[0] !== 'FreeCollab.app') throw new Error('A bundle link escapes the application.');
        const link = linkPaths.get(stack.join('/').normalize('NFC').toLowerCase());
        if (link) { if (++traversed > 64) throw new Error('Cyclic macOS bundle links.'); stack.pop(); queue.unshift(...link.split('/')); }
      }
    }
    return entries;
  } finally { await handle.close(); }
}
function validateZipExtra(bytes) {
  let cursor = 0;
  while (cursor < bytes.length) {
    if (cursor + 4 > bytes.length) throw new Error('Invalid update ZIP extra metadata.');
    const tag = bytes.readUInt16LE(cursor), length = bytes.readUInt16LE(cursor + 2); cursor += 4;
    if (cursor + length > bytes.length || ![0x5455, 0x7875, 0x000a].includes(tag)) throw new Error('Unsafe update ZIP path or type override.');
    cursor += length;
  }
}
async function verifyExtractedBundle(bundle) {
  const root = await fs.realpath(bundle); let count = 0;
  async function visit(directory) {
    for (const name of await fs.readdir(directory)) {
      if (++count > 20000) throw new Error('The extracted application exceeds its entry bound.');
      const target = path.join(directory, name), stat = await fs.lstat(target), actual = await fs.realpath(target);
      if (actual !== root && !actual.startsWith(root + path.sep)) throw new Error('An extracted bundle link escaped the application.');
      if (stat.isDirectory()) await visit(target);
      else if (!stat.isFile() && !stat.isSymbolicLink()) throw new Error('The extracted application contains an unsupported file type.');
    }
  }
  await visit(bundle);
}
async function stagePackage(response, manifest, root) {
  if (!response.ok || response.redirected || !response.body) throw new Error('The update package could not be downloaded directly.');
  await fs.mkdir(root, { recursive: true, mode: 0o700 });
  const directory = await fs.mkdtemp(path.join(root, 'verified-')), file = path.join(directory, 'package.' + manifest.format);
  const output = await fs.open(file, 'wx', 0o600), hash = crypto.createHash('sha256'); let bytes = 0;
  try {
    for await (const chunk of response.body) {
      const data = Buffer.from(chunk); bytes += data.length;
      if (bytes > manifest.size || bytes > MAX_PACKAGE) throw new Error('The update exceeds its signed size.');
      hash.update(data); await output.write(data);
    }
    if (bytes !== manifest.size || hash.digest('hex') !== manifest.sha256) throw new Error('The downloaded update does not match its signed SHA-256 and size.');
    await output.close();
    if (manifest.format === 'zip') await zipEntries(file, { platform: manifest.platform });
    await fs.writeFile(path.join(directory, 'manifest.json'), JSON.stringify(manifest), { mode: 0o600 });
    return { directory, file, manifest };
  } catch (error) { await output.close().catch(() => {}); await fs.rm(directory, { recursive: true, force: true }); throw error; }
}
async function unpack(staged, platform, previous) {
  if (staged.manifest.format === 'appimage') { await fs.chmod(staged.file, 0o700); return staged.file; }
  const entries = await zipEntries(staged.file, { platform }), directory = path.join(staged.directory, 'unpacked');
  await fs.mkdir(directory, { mode: 0o700 });
  if (platform === 'darwin') {
    const previousExecutable = (await run('/usr/libexec/PlistBuddy',['-c','Print :CFBundleExecutable',path.join(previous,'Contents/Info.plist')],{timeout:30000})).stdout.trim();
    if (!/^freecollab$/i.test(previousExecutable) || !entries.every(name => name.startsWith('FreeCollab.app/')) || !entries.includes('FreeCollab.app/Contents/MacOS/' + previousExecutable)) throw new Error('The macOS update must contain only FreeCollab.app.');
    await run('/usr/bin/ditto', ['-x', '-k', staged.file, directory], { timeout: 120000 });
    const bundle = path.join(directory, 'FreeCollab.app');
    await verifyExtractedBundle(bundle);
    const nextExecutable = (await run('/usr/libexec/PlistBuddy',['-c','Print :CFBundleExecutable',path.join(bundle,'Contents/Info.plist')],{timeout:30000})).stdout.trim();
    if (nextExecutable !== previousExecutable) throw new Error('The update changed the application executable identity.');
    await run('/usr/bin/codesign', ['--verify', '--deep', '--strict', bundle], { timeout: 60000 });
    const identities = await Promise.all([bundle, previous].map(target => run('/usr/bin/codesign', ['-d', '--verbose=4', target], { timeout: 30000 })));
    const teams = identities.map(result => /(?:^|\n)TeamIdentifier=([A-Z0-9]{10})(?:\n|$)/.exec(result.stderr)?.[1]);
    if (!teams[0] || teams[0] !== teams[1]) throw new Error('The macOS update must retain this application publisher identity.');
    return bundle;
  }
  if (platform === 'win32') {
    if (!entries.some(name => /^FreeCollab\.exe$/i.test(name)) && !entries.some(name => /^freecollab\.exe$/i.test(name))) throw new Error('The Windows update must contain the desktop executable at its root.');
    // All archive paths were independently validated before the OS extractor.
    const script = path.join(staged.directory, 'extract.ps1');
    await fs.writeFile(script, 'param([string]$Archive,[string]$Destination,[string]$Previous)\n$ErrorActionPreference="Stop"\nExpand-Archive -LiteralPath $Archive -DestinationPath $Destination\n$a=Get-AuthenticodeSignature -LiteralPath (Join-Path $Destination "FreeCollab.exe")\n$b=Get-AuthenticodeSignature -LiteralPath $Previous\nif($a.Status -ne "Valid" -or $b.Status -ne "Valid" -or $a.SignerCertificate.Subject -ne $b.SignerCertificate.Subject){throw "The signed Windows publisher does not match"}\n', { mode: 0o600 });
    await run('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script, staged.file, directory, path.join(previous, 'FreeCollab.exe')], { timeout: 120000 });
    return directory;
  }
  throw new Error('This desktop package does not support automatic installation.');
}
const POSIX_GUARDIAN = '#!/bin/sh\nset -eu\npid="$1"; staged="$2"; target="$3"; backup="$4"; health="$5"; nonce="$6"; launcher="$7"; pending="$8"\nwhile kill -0 "$pid" 2>/dev/null; do sleep 1; done\nif [ -e "$backup" ]; then exit 20; fi\nmv "$target" "$backup"\nif ! mv "$staged" "$target"; then mv "$backup" "$target"; exit 21; fi\n"$launcher" "--freecollab-update-health=$nonce" >/dev/null 2>&1 &\nchild=$!\ni=0\nwhile [ "$i" -lt 45 ]; do\n if [ -f "$health" ] && [ "$(cat "$health")" = "$nonce" ]; then rm -f "$pending"; exit 0; fi\n if ! kill -0 "$child" 2>/dev/null; then break; fi\n sleep 1; i=$((i+1))\ndone\nkill "$child" 2>/dev/null || true\nmv "$target" "$staged.failed"\nmv "$backup" "$target"\nrm -f "$pending"\n"$launcher" >/dev/null 2>&1 &\n';
const WINDOWS_GUARDIAN = 'param([int]$ParentPid,[string]$Staged,[string]$Target,[string]$Backup,[string]$Health,[string]$Nonce,[string]$Launcher,[string]$Pending)\n$ErrorActionPreference="Stop"\nwhile(Get-Process -Id $ParentPid -ErrorAction SilentlyContinue){Start-Sleep -Seconds 1}\nif(Test-Path -LiteralPath $Backup){exit 20}\nMove-Item -LiteralPath $Target -Destination $Backup\ntry{Move-Item -LiteralPath $Staged -Destination $Target}catch{Move-Item -LiteralPath $Backup -Destination $Target;exit 21}\n$child=Start-Process -FilePath $Launcher -ArgumentList ("--freecollab-update-health="+$Nonce) -PassThru\nfor($i=0;$i -lt 45;$i++){if((Test-Path -LiteralPath $Health) -and ((Get-Content -LiteralPath $Health -Raw) -eq $Nonce)){Remove-Item -LiteralPath $Pending -ErrorAction SilentlyContinue;exit 0};Start-Sleep -Seconds 1;$child.Refresh();if($child.HasExited){break}}\nStop-Process -Id $child.Id -ErrorAction SilentlyContinue\nMove-Item -LiteralPath $Target -Destination ($Staged+".failed")\nMove-Item -LiteralPath $Backup -Destination $Target\nRemove-Item -LiteralPath $Pending -ErrorAction SilentlyContinue\nStart-Process -FilePath $Launcher\n';
async function verifyStagedArtifact(staged) {
  const constants=require('node:fs').constants,handle=await fs.open(staged.file,constants.O_RDONLY|(constants.O_NOFOLLOW||0));
  try{
    const stat=await handle.stat();if(!stat.isFile()||stat.size!==staged.manifest.size||stat.size>MAX_PACKAGE)throw new Error('The staged update changed before installation.');
    const hash=crypto.createHash('sha256');for await(const chunk of handle.createReadStream({autoClose:false}))hash.update(chunk);
    if(hash.digest('hex')!==staged.manifest.sha256)throw new Error('The staged update hash changed before installation.');
  }finally{await handle.close();}
}
async function prepareInstallation(staged, { platform, target, launcher, root, parentPid = process.pid }) {
  await verifyStagedArtifact(staged);
  const stat = await fs.lstat(target);
  if (stat.isSymbolicLink() || (platform === 'linux' ? !stat.isFile() : !stat.isDirectory())) throw new Error('The installed application must be a real owned file or directory.');
  await fs.access(path.dirname(target), require('node:fs').constants.W_OK);
  const ready = await unpack(staged, platform, target), nonce = crypto.randomBytes(24).toString('hex');
  const sibling = target + '.freecollab-new-' + nonce, backup = target + '.freecollab-previous-' + nonce;
  await fs.cp(ready, sibling, { recursive: platform !== 'linux', errorOnExist: true, force: false, dereference: false });
  const health = path.join(root, 'health-' + nonce), pending = path.join(root, 'pending.json');
  await fs.writeFile(pending, JSON.stringify({ nonce, version: staged.manifest.version, sequence: staged.manifest.sequence, target, backup, platform, created_at: new Date().toISOString() }), { mode: 0o600 });
  const guardian = path.join(staged.directory, platform === 'win32' ? 'install.ps1' : 'install.sh');
  await fs.writeFile(guardian, platform === 'win32' ? WINDOWS_GUARDIAN : POSIX_GUARDIAN, { mode: 0o700 });
  return { nonce, backup, async cancel() {
    const stored = JSON.parse(await fs.readFile(pending, 'utf8').catch(() => '{}'));
    if (stored.nonce === nonce) await fs.rm(pending, { force: true });
    await fs.rm(sibling, { force: true, recursive: platform !== 'linux' });
  }, launch() {
    const command = platform === 'win32' ? 'powershell.exe' : '/bin/sh';
    const args = platform === 'win32' ? ['-NoProfile','-NonInteractive','-File',guardian] : [guardian];
    const child = spawn(command, [...args, String(parentPid), sibling, target, backup, health, nonce, launcher, pending], { detached: true, stdio: 'ignore', windowsHide: true }); child.unref();
  } };
}
async function acknowledgeStartup(root, currentVersion, argument) {
  if (!argument || !/^[a-f0-9]{48}$/.test(argument)) return false;
  const pending = JSON.parse(await fs.readFile(path.join(root, 'pending.json'), 'utf8'));
  if (pending.nonce !== argument || pending.version !== currentVersion || Date.now() - Date.parse(pending.created_at) > 86400000) throw new Error('The pending desktop update no longer matches this startup.');
  await fs.writeFile(path.join(root, 'health-' + argument), argument, { mode: 0o600 });
  await fs.writeFile(path.join(root, 'sequence.json'), JSON.stringify({ sequence: pending.sequence, version: currentVersion }), { mode: 0o600 });
  return true;
}
module.exports = { configuration, verifyManifest, newer, zipEntries, stagePackage, prepareInstallation, acknowledgeStartup, MAX_PACKAGE };
