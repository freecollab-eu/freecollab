import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFileSync} from 'node:child_process';
import {mkdtemp, mkdir, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const directory = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';

test('the actual tarball resolves every framework-neutral entry and declarations in a standalone host', async () => {
  const temporary = await mkdtemp(resolve(tmpdir(), 'freecollab-sdk-distribution-'));
  try {
    const [packed] = JSON.parse(execFileSync(npm, ['pack', '--json', '--ignore-scripts', '--cache', resolve(temporary, 'npm-cache'), '--pack-destination', temporary], {cwd: directory, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024}));
    assert.equal(packed.name, '@freecollab/components');
    const modules = resolve(temporary, 'node_modules');
    const installed = resolve(modules, '@freecollab/components');
    await mkdir(resolve(modules, '@freecollab'), {recursive: true});
    execFileSync('tar', ['-xzf', resolve(temporary, packed.filename), '--directory', temporary]);
    await symlink(resolve(temporary, 'package'), installed, 'dir');
    // Resolve the exact lock-installed runtime, without installing React or
    // relying on a monorepo ancestor's node_modules or a registry connection.
    await symlink(resolve(directory, 'node_modules/yjs'), resolve(modules, 'yjs'), 'dir');
    await symlink(resolve(directory, 'node_modules/yjs'), resolve(temporary, 'package/node_modules/yjs'), 'dir').catch(async error => {
      if (error.code !== 'ENOENT') throw error;
      await mkdir(resolve(temporary, 'package/node_modules'), {recursive: true});
      await symlink(resolve(directory, 'node_modules/yjs'), resolve(temporary, 'package/node_modules/yjs'), 'dir');
    });
    const manifest = JSON.parse(await readFile(resolve(installed, 'package.json'), 'utf8'));
    const metadata = JSON.parse(await readFile(resolve(installed, 'dist/release.json'), 'utf8'));
    assert.equal(metadata.version, manifest.version);
    assert.equal(metadata.apiLine, '0.11');
    assert.deepEqual(metadata.entries, Object.keys(manifest.exports));
    assert.equal(manifest.peerDependenciesMeta.react.optional, true);
    assert.equal(manifest.dependencies.yjs, '13.6.27');
    assert.equal(packed.files.some(file => /^(?:src|tests|scripts|artifacts)\//.test(file.path)), false);
    for (const entry of Object.values(manifest.exports)) {
      if (typeof entry === 'object') await readFile(resolve(installed, entry.types));
    }
    const expected = {'.': 'createClient', './chat': 'mountChat', './files': 'mountFiles', './feed': 'mountFeed', './notifications': 'mountNotifications', './tasks': 'mountTasks', './documents': 'mountDocuments', './meetings': 'mountMeetings', './calendar': 'mountCalendar', './workflows': 'mountWorkflows', './decisions': 'mountDecisions', './search': 'mountSearch', './auth': 'createEmbedSignIn', './embed': 'mountWorkspace', './elements': 'defineCustomElements'};
    const script = 'import assert from "node:assert/strict";\n' + Object.entries(expected).map(([entry, name]) => 'assert.equal(typeof (await import(' + JSON.stringify('@freecollab/components' + (entry === '.' ? '' : entry.slice(1))) + '))[' + JSON.stringify(name) + '],"function");').join('\n');
    const host = resolve(temporary, 'host.mjs'); await writeFile(host, script);
    execFileSync(process.execPath, [host], {cwd: temporary, stdio: 'pipe'});
    const consumer = resolve(temporary, 'consumer.mts');
    await writeFile(consumer, Object.entries(expected).map(([entry, name], index) => 'import {' + name + ' as entry' + index + '} from ' + JSON.stringify('@freecollab/components' + (entry === '.' ? '' : entry.slice(1))) + ';\nvoid entry' + index + ';').join('\n') + '\nimport type {EmbedConfig} from "@freecollab/components/embed";\nimport type {EmbedSignIn} from "@freecollab/components/auth";\nexport type HostContracts = [EmbedConfig, EmbedSignIn];\n');
    const require = createRequire(import.meta.url);
    const compiler = require.resolve('typescript/lib/tsc.js');
    execFileSync(process.execPath, [compiler, '--noEmit', '--strict', '--target', 'ES2020', '--module', 'NodeNext', '--moduleResolution', 'NodeNext', '--lib', 'ES2020,DOM,DOM.Iterable', consumer], {cwd: temporary, stdio: 'pipe'});
  } finally { await rm(temporary, {recursive: true, force: true}); }
});
