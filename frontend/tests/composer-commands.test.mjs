import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function load(path, imports = {}) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  vm.runInNewContext(output, { exports, require(name) { assert.ok(Object.hasOwn(imports, name), 'Unexpected dependency: ' + name); return imports[name]; } });
  return exports;
}
const i18n = load('../src/i18n/core.ts');
i18n.registerCatalog('sv', load('../src/i18n/composer-commands.sv.ts').default);
const slash = load('../src/lib/slash-commands.ts', { '../i18n/core': i18n });
const references = load('../src/lib/message-references.ts');
const { parseComposerCommand, getComposerCommandQuery, isComposerCommandDraft, composerCommandUsage, prepareCommandPeople } = load('../src/lib/composer-commands.ts', {
  '../i18n/core': i18n, './slash-commands': slash, './message-references': references,
});
const context = { hasChannel: true, hasWorkspace: true, secure: false, channelType: 'group', embedded: false, nativeSession: true, workspaceRole: 'owner', hasAdmin: false };
const parse = (value, selectedAction, changes = {}) => parseComposerCommand(value, { ...context, ...changes }, selectedAction);
const plain = value => JSON.parse(JSON.stringify(value));

test('ordinary prose, URLs and literal // remain outside command handling', () => {
  for (const value of ['', 'A / task', 'https://freecollab.eu/', '//literal', ' /task title']) {
    assert.equal(getComposerCommandQuery(value), null, value);
    assert.equal(isComposerCommandDraft(value, context), false, value);
  }
  assert.equal(isComposerCommandDraft('/unknown', context), true);
  assert.equal(isComposerCommandDraft('/unknown\nordinary text', context), false);
  assert.equal(isComposerCommandDraft('/\nordinary text', context), false);
  assert.equal(isComposerCommandDraft('/uppgift Titel\nSammanhang', context), true);
});

test('a bare slash still selects every real suggestion without inventing arguments', () => {
  assert.equal(parse('/').status, 'menu');
  for (const action of ['document', 'task', 'groups', 'appearance']) {
    const result = parse('/', action);
    assert.equal(result.status, 'ready', action);
    assert.deepEqual(plain(result.intent), { kind: 'open', action });
    assert.equal(result.arguments, '');
  }
});

test('English and Swedish commands create actual draft intents while retaining the title', () => {
  for (const name of ['task', 'uppgift', 'TODO']) {
    const result = parse('/' + name + ' Granska budget');
    assert.equal(result.status, 'ready');
    assert.equal(result.query, name);
    assert.equal(result.command.id, 'task');
    assert.deepEqual(plain(result.intent), { kind: 'task', title: 'Granska budget', text: '' });
  }
  assert.deepEqual(plain(parse('/dokument Mötesanteckningar').intent), { kind: 'document', title: 'Mötesanteckningar', text: '' });
  assert.deepEqual(plain(parse('/beslut Vi lanserar').intent), { kind: 'decision', title: 'Vi lanserar', content: 'Vi lanserar' });
});

test('quoted titles preserve escaped quotes, backslashes, Unicode and multiline context', () => {
  const result = parse('/document "Budget \\"A\\" \\\\ 2026" Åäö 😀\n  Second line\n');
  assert.equal(result.status, 'ready');
  assert.deepEqual(plain(result.intent), { kind: 'document', title: 'Budget "A" \\ 2026', text: 'Åäö 😀\n  Second line\n' });
  assert.equal(result.arguments, '"Budget \\"A\\" \\\\ 2026" Åäö 😀\n  Second line\n');
  assert.deepEqual(plain(parse('/task Review\r\nFirst\r\nSecond').intent), { kind: 'task', title: 'Review', text: 'First\nSecond' });
  assert.deepEqual(plain(parse('/decision "Launch"\nExplain why.').intent), { kind: 'decision', title: 'Launch', content: 'Explain why.' });
});

test('choosing a matching prefix preserves arguments and refuses unrelated selections', () => {
  const result = parse('/doc "Plan" A real draft', 'document');
  assert.equal(result.status, 'ready');
  assert.deepEqual(plain(result.intent), { kind: 'document', title: 'Plan', text: 'A real draft' });
  assert.equal(parse('/document Budget', 'task').status, 'invalid');
  assert.equal(parse('/not-a-command Budget', 'document').status, 'invalid');
  const unknown = parse('/not-a-command Budget');
  assert.equal(unknown.status, 'unknown');
  assert.equal(unknown.intent, undefined);
  assert.equal(unknown.arguments, 'Budget');
});

test('malformed or oversized arguments are visible errors rather than truncated drafts', () => {
  for (const text of ['/task "Unfinished', '/document ""', '/decision "Title"body', '/document "Title\ninside"', '/task ' + 'a'.repeat(201)]) {
    const result = parse(text);
    assert.equal(result.status, 'invalid', text);
    assert.equal(result.intent, undefined, text);
    assert.ok(result.error && result.usage, text);
  }
  const title = '😀'.repeat(100);
  assert.equal(parse('/task ' + title).intent.title, title);
  assert.equal(parse('/task ' + title + 'x').status, 'invalid');
  assert.equal(parse('/task Title\n' + 'a'.repeat(10000)).status, 'invalid');
});

test('search commands prefill the real single-line search without executing it', () => {
  assert.deepEqual(plain(parse('/sök Budget 2026').intent), { kind: 'search', query: 'Budget 2026' });
  assert.deepEqual(plain(parse('/search "Möten 2026"\nBudget').intent), { kind: 'search', query: 'Möten 2026 Budget' });
  assert.equal(parse('/search ' + 'a'.repeat(256)).intent.query.length, 256);
  assert.equal(parse('/search ' + 'a'.repeat(257)).status, 'invalid');
});

test('unimplemented argument forms are rejected while their existing controls remain available', () => {
  for (const command of ['status', 'apps', 'ai', 'meet', 'schedule']) {
    assert.equal(parse('/' + command).status, 'ready', command);
    const result = parse('/' + command + ' ignored arguments');
    assert.equal(result.status, 'invalid', command);
    assert.equal(result.intent, undefined, command);
    assert.ok(result.error && result.usage, command);
  }
});

test('group draft names retain explicit member text for reviewed ID-based membership', () => {
  const result = parse('/group "Design team" @Ada @Sam');
  assert.equal(result.status, 'ready');
  assert.deepEqual(plain(result.intent), { kind: 'groups', name: 'Design team', memberText: '@Ada @Sam' });
  assert.deepEqual(plain(parse('/grupp Ｄｅｓｉｇｎ').intent), { kind: 'groups', name: 'Design', memberText: '' });
  assert.equal(parse('/group ' + 'a'.repeat(80)).status, 'ready');
  assert.equal(parse('/group ' + 'a'.repeat(81)).status, 'invalid');
  assert.equal(parse('/groups "Name\ninside"').status, 'invalid');
});

test('current channel and encryption permissions are used before producing an intent', () => {
  assert.equal(parse('/apps', undefined, { secure: true }).intent, undefined);
  assert.equal(parse('/groups Team', undefined, { hasChannel: false }).intent, undefined);
  assert.equal(parse('/', 'task', { hasChannel: false }).intent, undefined);
  assert.equal(parse('/data', undefined, { workspaceRole: 'member' }).intent, undefined);
  assert.equal(parse('/dokument Titel', undefined, { secure: true }).intent.kind, 'document');
  assert.equal(parse('/new-channel', undefined, { embedded: true }).intent, undefined);
});

test('syntax help and failures follow the selected interface language', () => {
  i18n.applyLocale('sv');
  try {
    const task = parse('/uppgift Titel');
    assert.match(task.usage, /Titel/);
    assert.match(parse('/status text').error, /utan argument/);
    assert.match(composerCommandUsage(slash.getSlashCommands(context).find(command => command.id === 'groups')), /Gruppnamn/);
  } finally { i18n.applyLocale('en'); }
});

test('command people use explicit person IDs and strip only the selected trailing label', () => {
  const text = '/uppgift Granska 😀 budget @Åsa';
  const start = text.indexOf('@');
  const refs = [{ kind: 'person', id: 'user-asa', start, end: text.length }];
  const before = JSON.stringify(refs);
  assert.deepEqual(plain(prepareCommandPeople(text, refs)), { text: '/uppgift Granska 😀 budget', personIds: ['user-asa'] });
  assert.equal(JSON.stringify(refs), before);
  assert.deepEqual(plain(prepareCommandPeople(text, [])), { text, personIds: [] });
});

test('duplicate visible names retain distinct selected identities without guessing an assignee', () => {
  const text = '/task Review @Sam @Sam  ';
  const first = text.indexOf('@'), second = text.lastIndexOf('@');
  const refs = [{ kind: 'person', id: 'sam-one', start: first, end: first + 4 }, { kind: 'person', id: 'sam-two', start: second, end: second + 4 }];
  assert.deepEqual(plain(prepareCommandPeople(text, refs)), { text: '/task Review', personIds: ['sam-one', 'sam-two'] });
  assert.deepEqual(plain(prepareCommandPeople(text, refs.map(reference => ({ ...reference, id: 'same-sam' })))), { text: '/task Review', personIds: ['same-sam'] });
});

test('trailing explicitly selected people can be unsorted and separated by whitespace', () => {
  const text = '/groups "Design" @Ada \n @Sam\t ';
  const ada = text.indexOf('@Ada'), sam = text.indexOf('@Sam');
  const refs = [{ kind: 'person', id: 'sam', start: sam, end: sam + 4 }, { kind: 'person', id: 'ada', start: ada, end: ada + 4 }];
  assert.deepEqual(plain(prepareCommandPeople(text, refs)), { text: '/groups "Design"', personIds: ['ada', 'sam'] });
});

test('a selected person inside context remains literal context while its explicit ID is retained', () => {
  const text = '/task "Review" Ask @Ada about the estimates.  ';
  const start = text.indexOf('@Ada');
  assert.deepEqual(plain(prepareCommandPeople(text, [{ kind: 'person', id: 'ada', start, end: start + 4 }])) , { text, personIds: ['ada'] });
});

test('groups and document references never become people or disappear as name-like suffixes', () => {
  const text = '/task Review @Ada @Team';
  const ada = text.indexOf('@Ada'), team = text.indexOf('@Team');
  const refs = [{ kind: 'person', id: 'ada', start: ada, end: ada + 4 }, { kind: 'group', id: 'team', start: team, end: team + 5 }];
  assert.deepEqual(plain(prepareCommandPeople(text, refs)), { text, personIds: ['ada'] });
  const doc = '/task Review @Budget';
  assert.deepEqual(plain(prepareCommandPeople(doc, [{ kind: 'document', id: 'ada', start: doc.indexOf('@'), end: doc.length }])), { text: doc, personIds: [] });
});

test('malformed, overlapping or split-surrogate reference bindings never assign anyone', () => {
  const text = '/task 😀 @Sam';
  const start = text.indexOf('@Sam');
  const valid = { kind: 'person', id: 'sam', start, end: start + 4 };
  for (const refs of [
    [{ ...valid, start: start + 1 }], [{ ...valid, end: start + 30 }],
    [{ ...valid, start: text.indexOf('😀') + 1 }], [valid, { ...valid, id: 'other-sam' }],
    [{ ...valid, id: '' }], [{ ...valid, kind: 'unknown' }],
  ]) assert.deepEqual(plain(prepareCommandPeople(text, refs)), { text, personIds: [] });
});
