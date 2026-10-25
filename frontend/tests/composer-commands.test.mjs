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
const { parseComposerCommand, getComposerCommandQuery, isComposerCommandDraft, composerCommandUsage, prepareCommandPeople, prepareComposerDraftReferences } = load('../src/lib/composer-commands.ts', {
  '../i18n/core': i18n, './slash-commands': slash, './message-references': references,
});
const context = { hasChannel: true, hasWorkspace: true, secure: false, channelType: 'group', embedded: false, nativeSession: true, workspaceRole: 'owner', hasAdmin: false };
const parse = (value, selectedAction, changes = {}) => parseComposerCommand(value, { ...context, ...changes }, selectedAction);
const plain = value => JSON.parse(JSON.stringify(value));

const installed = (installation, app = 'courtyard', name = 'publish') => ({
  key: `${installation}:${name}`, installation_id: installation, app_id: app, app_name: app,
  generation: 3, origin: 'https://apps.example', name, description: 'Publish a reviewed update',
  arguments: [{ name: 'text', label: 'Text', required: true, max_length: 2000, type: 'text' }],
});

test('installed commands preserve typed arguments for an explicit reviewed invocation', () => {
  const command = installed('installation-a');
  const result = parse('/courtyard:publish text="A quiet 🧭 update"', undefined, { installedCommands: [command] });
  assert.equal(result.status, 'ready');
  assert.equal(result.command.id, 'app:installation-a:publish');
  assert.deepEqual(plain(result.intent), { kind: 'app-command', key: command.key, generation: 3, argumentText: 'text="A quiet 🧭 update"' });
  assert.match(composerCommandUsage(result.command), /text=/);
});

test('unique installed shortcuts work while ambiguous names require selecting the actual installation', () => {
  const first = installed('a'), second = installed('b', 'another');
  assert.equal(parse('/publish text="One"', undefined, { installedCommands: [first] }).intent.key, first.key);
  assert.equal(parse('/publish text="One"', undefined, { installedCommands: [first, second] }).status, 'menu');
  const chosen = parse('/publish text="One"', 'app:b:publish', { installedCommands: [first, second] });
  assert.equal(chosen.intent.key, second.key);
  assert.equal(parse('/task A real native task', undefined, { installedCommands: [installed('c', 'other', 'task')] }).intent.kind, 'task');
});

test('duplicate installed namespaces never choose an installation or send multiline arguments as a literal message', () => {
  const first = installed('a'), second = { ...installed('b'), origin: 'https://second.example.org' };
  const catalogue = { installedCommands: [first, second] };
  const text = '/courtyard:publish text="One\nprivate argument"';
  const ambiguous = parse(text, undefined, catalogue);
  assert.equal(ambiguous.status, 'menu'); assert.equal(ambiguous.intent, undefined); assert.equal(ambiguous.command, undefined);
  assert.equal(ambiguous.arguments, 'text="One\nprivate argument"');
  assert.equal(isComposerCommandDraft(text, { ...context, ...catalogue }), true);
  const selected = parse(text, 'app:b:publish', catalogue);
  assert.equal(selected.intent.key, second.key); assert.equal(selected.intent.generation, second.generation);
  const entries = slash.getSlashCommands({ ...context, ...catalogue });
  assert.notEqual(entries.find(row => row.installedApp?.key === first.key).description, entries.find(row => row.installedApp?.key === second.key).description);
});

test('installed catalogue cannot expose commands in another or encrypted conversation', () => {
  const command = installed('a');
  for (const gate of [{ secure: true }, { channelType: 'direct' }, { hasChannel: false }, { hasWorkspace: false }]) {
    const result = parse('/courtyard:publish text="One"', undefined, { installedCommands: [command], ...gate });
    assert.equal(result.status, 'unknown'); assert.equal(result.command, undefined);
  }
});

test('long registered namespaces remain usable without expanding the unknown command limit', () => {
  const command = installed('a', 'a'.repeat(90));
  assert.equal(parse(`/${command.app_id}:${command.name} text="One"`, undefined, { installedCommands: [command] }).status, 'ready');
  assert.equal(parse('/' + 'x'.repeat(81)).status, 'invalid');
});

function picked(value, label, id, kind = 'document', from = 0) {
  const start = value.indexOf(label, from);
  assert.ok(start >= 0);
  return { kind, id, start, end: start + label.length };
}

test('command draft transfer distinguishes identical labels by their selected source spans', () => {
  const value = '/task "Review @Brief" Compare @Brief with @Brief';
  const a = picked(value, '@Brief', 'title-document'), b = picked(value, '@Brief', 'first-context-document', 'document', a.end), c = picked(value, '@Brief', 'second-context-task', 'task', b.end);
  const result = prepareComposerDraftReferences(value, [a, b, c], parse(value).intent);
  assert.deepEqual(plain(result), { title: [{ kind: 'document', id: 'title-document', start: 7, end: 13 }], description: [
    { kind: 'document', id: 'first-context-document', start: 8, end: 14 }, { kind: 'task', id: 'second-context-task', start: 20, end: 26 },
  ] });
});

test('draft transfer retains UTF-16 IDs through escaped title syntax, CRLF and trimming', () => {
  const value = '/dokument  "  Quote \\"yes\\" 🧭 @Brief  "\r\n  Related @Brief\r\n@File';
  const a = picked(value, '@Brief', 'title-doc'), b = picked(value, '@Brief', 'body-doc', 'document', a.end), c = picked(value, '@File', 'file-object', 'file');
  const intent = parse(value).intent;
  assert.equal(intent.title, 'Quote "yes" 🧭 @Brief');
  assert.equal(intent.text, '  Related @Brief\n@File');
  const result = prepareComposerDraftReferences(value, [a, b, c], intent);
  for (const [key, text] of [['title', intent.title], ['body', intent.text]]) for (const ref of result[key]) {
    assert.equal(text.slice(ref.start, ref.end), ref.kind === 'file' ? '@File' : '@Brief');
    assert.equal(text.charCodeAt(ref.start - 1), key === 'title' ? 32 : ref.kind === 'file' ? 10 : 32);
  }
  assert.deepEqual(Object.values(result).flat().map(ref => ref.id), ['title-doc', 'body-doc', 'file-object']);
});

test('trailing selected owner is assigned separately while context identities remain bound', () => {
  const value = '/task "Review @Brief" Ask @Ada about @Brief @Sam';
  const a = picked(value, '@Brief', 'title-doc'), b = picked(value, '@Ada', 'ada', 'person'), c = picked(value, '@Brief', 'context-doc', 'document', a.end), d = picked(value, '@Sam', 'sam', 'person');
  const prepared = prepareCommandPeople(value, [a, b, c, d]);
  const result = prepareComposerDraftReferences(value, [a, b, c, d], parse(prepared.text).intent);
  assert.deepEqual(Object.values(result).flat().map(ref => ref.id), ['title-doc', 'ada', 'context-doc']);
  assert.deepEqual(plain(prepared.personIds), ['ada', 'sam']);
  assert.equal(result.description[0].start, 4);
});

test('document commands preserve trailing person links instead of treating them as assignees', () => {
  const value = '/document "Notes" Ask @Ada', ref = picked(value, '@Ada', 'ada', 'person');
  assert.deepEqual(plain(prepareComposerDraftReferences(value, [ref], parse(value).intent)), {
    body: [{ kind: 'person', id: 'ada', start: 4, end: 8 }],
  });
});

test('decision title fallback binds both real fields to the same selected identity', () => {
  const value = '/beslut Granska @Brief', ref = picked(value, '@Brief', 'actual-doc');
  const result = prepareComposerDraftReferences(value, [ref], parse(value).intent);
  assert.deepEqual(plain(result.title), [{ kind: 'document', id: 'actual-doc', start: 8, end: 14 }]);
  assert.deepEqual(plain(result.content), plain(result.title));
});

test('reference transfer rejects stale intents and transformed labels without consuming caller text', () => {
  const value = '/task Granska @Brief', ref = picked(value, '@Brief', 'actual-doc');
  assert.throws(() => prepareComposerDraftReferences(value, [ref], { kind: 'task', title: 'Something else', text: '' }));
  const escaped = '/document "Review @A\\\\B"', transformed = picked(escaped, '@A\\\\B', 'actual-doc');
  assert.throws(() => prepareComposerDraftReferences(escaped, [transformed], parse(escaped).intent));
  assert.throws(() => prepareComposerDraftReferences(value, [{ ...ref, start: ref.start - 1 }], parse(value).intent));
  assert.equal(value, '/task Granska @Brief');
});

test('ordinary command drafts stay compatible and non-resource commands add no field metadata', () => {
  assert.equal(prepareComposerDraftReferences('/task Review', [], parse('/task Review').intent), undefined);
  assert.equal(prepareComposerDraftReferences('/status', [], parse('/status').intent), undefined);
});

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
