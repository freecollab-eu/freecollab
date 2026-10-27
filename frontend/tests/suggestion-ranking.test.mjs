import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const plain = value => JSON.parse(JSON.stringify(value));
const scope = { actorId: 'actor-a', token: 'session-a', workspaceId: 'workspace-a', channelId: 'channel-a' };
const context = { hasChannel: true, hasWorkspace: true, secure: false, channelType: 'group', embedded: false, nativeSession: true, workspaceRole: 'owner', hasAdmin: true };
const candidate = (kind, id, label = id) => ({ kind, id, label, channel_id: 'channel-a' });

function fixture() {
  const handlers = new Map(), ranking = {}, slash = {}, references = {}, composer = {};
  let storageAccesses = 0;
  const forbiddenStorage = { get() { storageAccesses++; throw new Error('Suggestion ordering must never read retained account storage.'); } };
  const environment = { exports: ranking, window: { addEventListener: (name, callback) => handlers.set(name, callback) } };
  Object.defineProperties(environment, { localStorage: forbiddenStorage, sessionStorage: forbiddenStorage, indexedDB: forbiddenStorage });
  vm.runInNewContext(compile('../src/lib/suggestion-ranking.ts'), environment);
  const i18n = { t: value => value };
  vm.runInNewContext(compile('../src/lib/slash-commands.ts'), { exports: slash, require: name => {
    if (name === '../i18n/core') return i18n;
    assert.equal(name, './suggestion-ranking'); return ranking;
  } });
  vm.runInNewContext(compile('../src/lib/message-references.ts'), { exports: references });
  vm.runInNewContext(compile('../src/lib/composer-commands.ts'), { exports: composer, TextEncoder, require: name => {
    const imports = { '../i18n/core': i18n, './slash-commands': slash, './message-references': references };
    assert.ok(Object.hasOwn(imports, name), name); return imports[name];
  } });
  const commands = (surface, changes = {}, query = '') => slash.getSlashCommands({ ...context, suggestions: { surface }, suggestionScope: scope, ...changes }, query);
  return { ranking, slash, references, composer, commands, emit: name => handlers.get(name)?.(), storageAccesses: () => storageAccesses };
}

test('the actual writing surface leads the complete permitted command inventory', () => {
  const f = fixture(), original = f.slash.getSlashCommands(context);
  for (const [surface, first] of [['document', 'document'], ['task', 'task'], ['decision', 'decision'], ['file', 'files']]) {
    const choices = f.commands(surface);
    assert.equal(choices[0].id, first);
    assert.deepEqual(Array.from(choices, row => row.id).sort(), Array.from(original, row => row.id).sort());
    assert.equal(new Set(choices.map(row => row.id)).size, choices.length);
  }
});

test('recent useful actions break contextual ties while the current work stays first', () => {
  const f = fixture();
  f.ranking.recordCommandChoice(scope, 'task'); f.ranking.recordCommandChoice(scope, 'files'); f.ranking.recordCommandChoice(scope, 'account');
  const choices = Array.from(f.commands('document'), row => row.id);
  assert.deepEqual(choices.slice(0, 3), ['document', 'files', 'task']);
  assert.ok(choices.indexOf('decision') < choices.indexOf('account'));
});

test('exact typed commands outrank contextual and recent matches without altering app identities', () => {
  const f = fixture(); f.ranking.recordCommandChoice(scope, 'task');
  const installed = { key: 'installation:publish', installation_id: 'installation', app_id: 'studio', app_name: 'Studio', generation: 7,
    origin: 'https://apps.example.test', name: 'publish', description: 'Publish a reviewed document', arguments: [{ name: 'text', type: 'text', label: 'Text', max_length: 2000, required: true }] };
  assert.equal(f.commands('task', {}, 'document')[0].id, 'document');
  const choices = f.commands('document', { installedCommands: [installed] }, 'studio:publish');
  assert.equal(choices[0].id, 'app:installation:publish'); assert.equal(choices[0].installedApp, installed);
  const commandContext = { ...context, suggestions: { surface: 'document' }, suggestionScope: scope, installedCommands: [installed] };
  const text = '/studio:publish text="literal @Ada /task https://example.test/?x=1"';
  const before = f.composer.parseComposerCommand(text, commandContext);
  f.ranking.recordCommandChoice(scope, 'app:installation:publish');
  const after = f.composer.parseComposerCommand(text, commandContext);
  assert.deepEqual(plain(after.intent), plain(before.intent));
  assert.equal(after.intent.argumentText, 'text="literal @Ada /task https://example.test/?x=1"');
});

test('ranking cannot reveal actions removed by current permissions or encryption', () => {
  const f = fixture();
  for (const id of ['admin', 'data', 'apps', 'new-channel']) f.ranking.recordCommandChoice(scope, id);
  const choices = f.commands('workspace', { secure: true, embedded: true, nativeSession: false, hasAdmin: false, workspaceRole: 'member' });
  for (const id of ['admin', 'data', 'apps', 'new-channel']) assert.ok(!choices.some(row => row.id === id), id);
  assert.ok(choices.some(row => row.id === 'task'));
});

test('recent choices belong to one actor, credential, workspace and channel', () => {
  const f = fixture(); f.ranking.recordCommandChoice(scope, 'files');
  assert.equal(f.commands('conversation')[0].id, 'files');
  for (const change of [{ channelId: 'channel-b' }, { workspaceId: 'workspace-b' }, { token: 'session-b' }, { actorId: 'actor-b' }]) {
    assert.equal(f.commands('conversation', { suggestionScope: { ...scope, ...change } })[0].id, 'document');
  }
});

test('an authentication transition clears activity even if the old identity returns', () => {
  const f = fixture(); f.ranking.recordCommandChoice(scope, 'files');
  f.emit('freecollab:auth-changed');
  assert.equal(f.commands('conversation')[0].id, 'document');
  f.ranking.recordCommandChoice(scope, 'task'); f.emit('freecollab:session-expired');
  assert.equal(f.commands('conversation')[0].id, 'document');
});

test('public demo and incomplete scopes use memory only and never adopt retained activity', () => {
  const f = fixture(); f.ranking.recordCommandChoice(scope, 'files');
  const demo = { actorId: 'example-actor', token: 'memory-demo-session', workspaceId: 'example-workspace', channelId: 'example-channel' };
  assert.equal(f.commands('conversation', { suggestionScope: demo })[0].id, 'document');
  f.ranking.recordCommandChoice(demo, 'task'); assert.equal(f.commands('conversation', { suggestionScope: demo })[0].id, 'task');
  assert.equal(f.commands('conversation')[0].id, 'document');
  f.ranking.recordCommandChoice({}, 'files'); assert.equal(f.commands('conversation', { suggestionScope: {} })[0].id, 'document');
  assert.equal(f.storageAccesses(), 0);
});

test('reference matching wins before context and never drops a returned reference kind', () => {
  const f = fixture(), rows = [candidate('person', 'ada', 'Ada Person'), candidate('group', 'team', 'Team'), candidate('document', 'doc', 'Ada'),
    candidate('task', 'task', 'Review'), candidate('file', 'file', 'Report'), candidate('channel', 'channel', 'General')];
  const ordered = f.ranking.rankReferenceSuggestions(rows, 'Ada', { surface: 'conversation' }, scope);
  assert.equal(ordered[0], rows[2]);
  assert.deepEqual(Array.from(ordered, row => row.kind).sort(), Array.from(rows, row => row.kind).sort());
  assert.deepEqual(rows.map(row => row.id), ['ada', 'team', 'doc', 'task', 'file', 'channel']);
});

test('an assignee field leads with real people while retaining groups and work references', () => {
  const f = fixture(), rows = [candidate('document', 'doc'), candidate('group', 'group'), candidate('person', 'person-b', 'Alex'), candidate('person', 'person-a', 'Alex'), candidate('task', 'task')];
  f.ranking.recordReferenceChoice(scope, rows[3]);
  const ordered = f.ranking.rankReferenceSuggestions(rows, '', { surface: 'task', field: 'assignee' }, scope);
  assert.deepEqual(Array.from(ordered, row => row.id), ['person-a', 'person-b', 'group', 'doc', 'task']);
  const inserted = f.references.insertReference('@', [], { query: '', start: 0, end: 1 }, ordered[0]);
  assert.equal(inserted.references[0].id, 'person-a');
  assert.equal(new Set(ordered.map(row => row.kind + ':' + row.id)).size, rows.length);
});

test('document writing favors shared material and recent choices only within its relevant tier', () => {
  const f = fixture(), rows = [candidate('person', 'ada'), candidate('document', 'doc-a'), candidate('file', 'file-a'), candidate('task', 'task-a')];
  f.ranking.recordReferenceChoice(scope, rows[0]); f.ranking.recordReferenceChoice(scope, rows[2]);
  assert.deepEqual(Array.from(f.ranking.rankReferenceSuggestions(rows, '', { surface: 'document' }, scope), row => row.id), ['file-a', 'doc-a', 'ada', 'task-a']);
});

test('field context and native command context are inferred without rewriting arbitrary app arguments', () => {
  const f = fixture();
  assert.deepEqual(plain(f.ranking.inferSuggestionContext('shared-document-title')), { surface: 'document', field: 'title' });
  assert.deepEqual(plain(f.ranking.inferSuggestionContext('primary:channel:task:id:description')), { surface: 'task', field: 'description' });
  assert.deepEqual(plain(f.ranking.inferSuggestionContext('shared-document-comment')), { surface: 'comment' });
  const normal = { surface: 'conversation' };
  assert.deepEqual(plain(f.ranking.referenceSuggestionContext(normal, '/task "Review" @', 15)), { surface: 'task', field: 'assignee' });
  assert.equal(f.ranking.referenceSuggestionContext(normal, '/studio:task text="@', 19), normal);
  assert.equal(f.ranking.referenceSuggestionContext(normal, 'ordinary /task @', 16), normal);
});

test('ranking leaves all caller-owned command definitions, candidates and context untouched', () => {
  const f = fixture(), input = { ...context, suggestions: { surface: 'task' }, suggestionScope: { ...scope } };
  const before = JSON.stringify(input); f.slash.getSlashCommands(input); assert.equal(JSON.stringify(input), before);
  const rows = Object.freeze([Object.freeze(candidate('person', 'a')), Object.freeze(candidate('file', 'b'))]);
  const sorted = f.ranking.rankReferenceSuggestions(rows, '', { surface: 'document' }, scope);
  assert.equal(sorted[0], rows[1]); assert.equal(sorted[1], rows[0]);
});
