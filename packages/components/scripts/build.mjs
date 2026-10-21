import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const directory = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
function dependency(name, fallback) { try { return require.resolve(name); } catch { return resolve(directory, '../../frontend/node_modules', fallback); } }
const ts = (await import(pathToFileURL(dependency('typescript', 'typescript/lib/typescript.js')).href)).default;
const esbuild = await import(pathToFileURL(dependency('esbuild', 'esbuild/lib/main.js')).href);
const packageJson = JSON.parse(await readFile(resolve(directory, 'package.json'), 'utf8'));
const entryPoints = Object.values(packageJson.exports).filter(value => typeof value === 'object' && value.import).map(value => {
  const name = value.import.replace('./dist/', '').replace(/\.js$/, '');
  return resolve(directory, 'src', name + (name === 'react' ? '.tsx' : '.ts'));
});
const config = ts.readConfigFile(resolve(directory, 'tsconfig.json'), ts.sys.readFile);
if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, directory);
try { require.resolve('@types/react/package.json'); }
catch { parsed.options.baseUrl = directory; parsed.options.paths = { react: [resolve(directory, '../../frontend/node_modules/@types/react/index.d.ts')] }; parsed.options.typeRoots = [resolve(directory, '../../frontend/node_modules/@types')]; }
await rm(resolve(directory, 'dist'), { recursive: true, force: true });
const program = ts.createProgram(parsed.fileNames, parsed.options);
const diagnostics = ts.getPreEmitDiagnostics(program);
if (diagnostics.length) { process.stderr.write(ts.formatDiagnosticsWithColorAndContext(diagnostics, { getCurrentDirectory: () => directory, getCanonicalFileName: name => name, getNewLine: () => '\n' })); process.exit(1); }
program.emit();
await esbuild.build({ absWorkingDir: directory, entryPoints, outdir: 'dist', bundle: true, splitting: true, format: 'esm', platform: 'browser', target: 'es2020', external: ['react', 'react/*', 'yjs'], logLevel: 'info' });
await mkdir(resolve(directory, 'dist'), { recursive: true });
await copyFile(resolve(directory, 'styles.css'), resolve(directory, 'dist/styles.css'));
try { await copyFile(resolve(directory, '../../LICENSE'), resolve(directory, 'LICENSE')); } catch { /* A packed consumer already has LICENSE. */ }
// Deterministic build metadata. No local path, author, Git credentials or runtime
// configuration is copied into the public artifact.
const sources = [];
async function recordSources(path, prefix = '') {
  for (const entry of (await readdir(path, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) await recordSources(resolve(path, entry.name), name + '/');
    else if (entry.isFile()) sources.push([name, createHash('sha256').update(await readFile(resolve(path, entry.name))).digest('hex')]);
  }
}
await recordSources(resolve(directory, 'src'));
await writeFile(resolve(directory, 'dist/release.json'), JSON.stringify({ schemaVersion: 1, name: packageJson.name, version: packageJson.version, apiLine: '0.11', sourceDigest: createHash('sha256').update(JSON.stringify(sources)).digest('hex'), entries: Object.keys(packageJson.exports), encryption: 'trusted-origin-embed', nativeChannels: 'server-processed' }, null, 2) + '\n');
process.stdout.write('Built ' + packageJson.name + '@' + packageJson.version + ' (typed ESM; React remains an optional peer).\n');
