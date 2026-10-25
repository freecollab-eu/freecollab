import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/installed-app-commands.ts', import.meta.url), 'utf8');
const tree = ts.createSourceFile('installed-app-commands.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const actual = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'parseInstalledAppArguments');
assert.ok(actual);
const context = { exports: {}, Object, Number };
vm.runInNewContext(ts.transpileModule(actual.getText(tree), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
const parse = (command, raw) => JSON.parse(JSON.stringify(context.exports.parseInstalledAppArguments(command, raw)));
const command = arguments_ => ({ key: 'installation:run', installation_id: 'installation', app_id: 'customer.app', app_name: 'App', generation: 3, origin: 'https://app.example', name: 'run', description: 'Runs installed action', arguments: arguments_ });
const field = (name, options = {}) => ({ name, label: name, required: true, max_length: 100, type: 'text', ...options });

test('named arguments preserve quoted Unicode, escaped quotes and values containing equals', () => {
  const value = parse(command([field('title'), field('body')]), 'title="Granska 🧭" body="a=b and \\"quoted\\""');
  assert.deepEqual(value, { title: 'Granska 🧭', body: 'a=b and "quoted"' });
});
test('one declared text argument accepts ordinary positional content without shell execution', () => {
  const value = '$(do-not-execute) ; literal <script> text';
  assert.deepEqual(parse(command([field('text')]), value), { text: value });
});
test('unknown, duplicate, unterminated and malformed named arguments are rejected', () => {
  const definition = command([field('title'), field('body', { required: false })]);
  for (const raw of ['title=a title=b', 'other=value', 'title="open', 'title=a trailing\\', 'missing=value']) assert.throws(() => parse(definition, raw));
});
test('required and bounded text is checked before preparing a command', () => {
  assert.throws(() => parse(command([field('title')]), ''));
  assert.throws(() => parse(command([field('title', { max_length: 3 })]), 'longer'));
  assert.deepEqual(parse(command([field('title'), field('body', { required: false })]), 'title=ready'), { title: 'ready', body: '' });
});
test('installed number, boolean and enumerated argument contracts are enforced', () => {
  const definition = command([field('amount', { type: 'number' }), field('enabled', { type: 'boolean' }), field('region', { type: 'choice', choices: ['eu', 'us'] })]);
  assert.deepEqual(parse(definition, 'amount=1.25 enabled=false region=eu'), { amount: '1.25', enabled: 'false', region: 'eu' });
  for (const raw of ['amount=Infinity enabled=true region=eu', 'amount=1 enabled=yes region=eu', 'amount=1 enabled=true region=unknown']) assert.throws(() => parse(definition, raw));
});
test('NUL and oversized input cannot enter an installed request', () => {
  const definition = command([field('text')]); assert.throws(() => parse(definition, 'a\0b')); assert.throws(() => parse(definition, 'x'.repeat(64001)));
});
