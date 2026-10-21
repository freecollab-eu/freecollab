import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const directory = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const destination = resolve(process.argv[2] || resolve(directory, 'artifacts'));
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const manifest = JSON.parse(await readFile(resolve(directory, 'package.json'), 'utf8'));
if (!/^0\.12\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(manifest.version)) throw new Error('Review the compatibility line before releasing a new minor version.');
execFileSync(npm, ['run', 'build'], { cwd: directory, stdio: 'inherit' });
await mkdir(destination, { recursive: true });
// npm pack still writes its content cache. Keep that disposable cache outside
// the artifact directory, including on read-only-home build machines.
const packCache = await mkdtemp(resolve(tmpdir(), 'freecollab-sdk-pack-cache-'));
let packed;
try { packed = JSON.parse(execFileSync(npm, ['pack', '--json', '--ignore-scripts', '--cache', packCache, '--pack-destination', destination], { cwd: directory, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }))[0]; }
finally { await rm(packCache, { recursive: true, force: true }); }
if (packed.name !== manifest.name || packed.version !== manifest.version || basename(packed.filename) !== packed.filename) throw new Error('Unexpected npm artifact identity.');
const paths = new Set(packed.files.map(file => file.path));
for (const path of paths) {
  if (!/^(?:dist\/|package\.json$|README\.md$|LICENSE$|COMPATIBILITY\.md$)/.test(path) || path.includes('..') || /(?:^|\/)(?:\.env|node_modules|\.git|proof-results)(?:\/|$)/.test(path)) throw new Error('Unreviewed file in npm artifact: ' + path);
}
for (const entry of Object.values(manifest.exports)) {
  for (const target of typeof entry === 'string' ? [entry] : [entry.import, entry.types]) {
    if (target && !paths.has(target.replace(/^\.\//, ''))) throw new Error('Missing public export: ' + target);
  }
}
for (const required of ['LICENSE', 'COMPATIBILITY.md', 'dist/release.json']) if (!paths.has(required)) throw new Error('Missing release contract: ' + required);
if (packed.files.some(file => file.mode !== 0o644)) throw new Error('Public package files must use canonical readable permissions.');
const bytes = await readFile(resolve(destination, packed.filename));
const sha256 = createHash('sha256').update(bytes).digest('hex');
const shasum = createHash('sha1').update(bytes).digest('hex');
const integrity = 'sha512-' + createHash('sha512').update(bytes).digest('base64');
if (shasum !== packed.shasum || integrity !== packed.integrity) throw new Error('Packed bytes do not match npm integrity metadata.');
const metadata = { schemaVersion: 1, name: manifest.name, version: manifest.version, artifact: packed.filename, sha256, shasum, integrity, files: [...paths].sort(), publishTag: manifest.publishConfig.tag, registry: manifest.publishConfig.registry };
await writeFile(resolve(destination, 'release-manifest.json'), JSON.stringify(metadata, null, 2) + '\n');
await writeFile(resolve(destination, 'SHA256SUMS'), sha256 + '  ' + packed.filename + '\n');
for (const name of [packed.filename, 'release-manifest.json', 'SHA256SUMS']) await chmod(resolve(destination, name), 0o644);
process.stdout.write('Verified package artifact: ' + packed.filename + '\nSHA-256: ' + sha256 + '\nPublishing was not performed.\n');
