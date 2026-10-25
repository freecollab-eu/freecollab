import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function load(path, imports = {}) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  vm.runInNewContext(compiled, { exports, require(name) { assert.ok(Object.hasOwn(imports, name), 'Unexpected dependency: ' + name); return imports[name]; } });
  return exports;
}

const i18n = load('../src/i18n/core.ts');
const catalog = {};
for (const name of readdirSync(new URL('../src/i18n/', import.meta.url)).filter(name => name === 'sv.ts' || name.endsWith('.sv.ts'))) {
  Object.assign(catalog, load('../src/i18n/' + name).default);
}
i18n.registerCatalog('sv', catalog);
const { getSlashCommands, getSlashQuery } = load('../src/lib/slash-commands.ts', { '../i18n/core': i18n });
const context = { hasChannel: true, hasWorkspace: true, secure: false, channelType: 'group', embedded: false, nativeSession: true, workspaceRole: 'owner', hasAdmin: true };
const ids = (changes = {}, query = '') => Array.from(getSlashCommands({ ...context, ...changes }, query), command => command.id);

test('the slash parser distinguishes intentional shortcuts from ordinary drafts and literal slashes', () => {
  assert.equal(getSlashQuery('/'), '');
  assert.equal(getSlashQuery('/  dokument '), 'dokument');
  assert.equal(getSlashQuery('/nästa steg'), 'nästa steg');
  for (const draft of ['', 'ordinary text', 'Meet on 10/23', 'https://freecollab.eu/', 'a / b', ' /task', '//literal', '/task\nthen text', '/task\rtext', '/task\u2028text']) assert.equal(getSlashQuery(draft), null, draft);
  assert.equal(getSlashQuery('/' + 'a'.repeat(128)), 'a'.repeat(128));
  assert.equal(getSlashQuery('/' + 'a'.repeat(129)), null);
});

test('empty search exposes the full feature inventory without truncating the list', () => {
  const all = getSlashCommands({ ...context, secure: true, sharedWork: true });
  const allNative = getSlashCommands(context);
  const union = new Set([...all, ...allNative].map(command => command.id));
  assert.deepEqual([...union].sort(), [
    'document', 'task', 'decision', 'meetings', 'files', 'members', 'groups', 'channel', 'bookmarks', 'pins', 'notification-settings', 'search', 'activity', 'workflows', 'cloud', 'external-meetings', 'apps', 'copilot', 'data', 'mobile', 'shared-work', 'schedule', 'focus', 'appearance', 'account', 'my-day', 'personal-calendar', 'integrations', 'direct-message', 'new-channel', 'channels', 'secure-settings', 'status', 'notifications', 'get-started', 'feedback', 'admin',
  ].sort());
  for (const commands of [all, allNative]) {
    assert.equal(new Set(commands.map(command => command.id)).size, commands.length);
    assert.equal(new Set(commands.map(command => command.command)).size, commands.length);
    assert.ok(commands.length > 25, 'Every available feature must remain reachable, including the last groups.');
    for (const command of commands) {
      assert.match(command.command, /^[a-z][a-z-]+$/);
      assert.ok(command.label && command.description);
      assert.ok(['conversation', 'workspace', 'personal'].includes(command.group));
    }
  }
});

test('member and scoped sessions keep their usable work while hiding privileged configuration', () => {
  const member = ids({ workspaceRole: 'member', hasAdmin: false });
  for (const id of ['document', 'task', 'decision', 'workflows', 'apps', 'integrations', 'feedback']) assert.ok(member.includes(id), id);
  for (const id of ['data', 'get-started', 'admin']) assert.ok(!member.includes(id), id);
  assert.ok(ids({ workspaceRole: 'admin', hasAdmin: false }).includes('get-started'));
  assert.ok(!ids({ workspaceRole: 'admin', hasAdmin: false }).includes('data'));
  const scoped = ids({ nativeSession: false });
  for (const id of ['data', 'get-started', 'admin']) assert.ok(!scoped.includes(id), id);
  for (const id of ['document', 'task', 'account', 'appearance']) assert.ok(scoped.includes(id), id);
});

test('embedded conversations expose local work without workspace navigation or installation administration', () => {
  const embedded = ids({ embedded: true });
  for (const id of ['channels', 'new-channel', 'direct-message', 'integrations', 'get-started', 'my-day', 'admin']) assert.ok(!embedded.includes(id), id);
  for (const id of ['document', 'task', 'decision', 'files', 'schedule', 'appearance', 'mobile']) assert.ok(embedded.includes(id), id);
});

test('no selected channel removes channel handlers while preserving global usable settings', () => {
  const commands = ids({ hasChannel: false });
  for (const id of ['document', 'task', 'decision', 'meetings', 'files', 'members', 'groups', 'channel', 'bookmarks', 'pins', 'notification-settings', 'activity', 'workflows', 'cloud', 'external-meetings', 'apps', 'copilot', 'shared-work', 'schedule', 'focus', 'data']) assert.ok(!commands.includes(id), id);
  for (const id of ['search', 'channels', 'new-channel', 'integrations', 'my-day', 'account', 'appearance', 'mobile', 'secure-settings']) assert.ok(commands.includes(id), id);
  const noWorkspace = ids({ hasChannel: false, hasWorkspace: false });
  for (const id of ['search', 'channels', 'new-channel', 'integrations', 'my-day', 'direct-message', 'feedback', 'status', 'notifications']) assert.ok(!noWorkspace.includes(id), id);
});

test('encryption preserves encrypted work and requires actual eligibility for federated work', () => {
  const secure = ids({ secure: true });
  for (const id of ['document', 'task', 'decision', 'cloud', 'activity', 'workflows']) assert.ok(secure.includes(id), id);
  assert.ok(!secure.includes('apps'));
  assert.ok(!secure.includes('shared-work'));
  assert.ok(ids({ secure: true, sharedWork: true }).includes('shared-work'));
  assert.ok(!ids({ secure: false, sharedWork: true }).includes('shared-work'));
  const cloud = getSlashCommands({ ...context, secure: true }).find(command => command.id === 'cloud');
  assert.equal(cloud.description, 'Share encrypted links to your provider files.');
});

test('direct conversations do not advertise channel metadata or unsupported app automation', () => {
  for (const channelType of ['direct', 'group_direct']) {
    const commands = ids({ channelType });
    assert.ok(!commands.includes('channel'));
    assert.ok(!commands.includes('apps'));
    for (const id of ['files', 'members', 'document', 'task', 'decision']) assert.ok(commands.includes(id), id);
  }
});

test('shortcuts rank exact actions before fuzzy matches and bilingual aliases work in English', () => {
  for (const [query, action] of [['/task', 'task'], ['files', 'files'], ['ai', 'copilot'], ['doc', 'document'], ['nästa steg', 'task'], ['typsnitt', 'appearance'], ['schemalägg', 'schedule'], ['molnfiler', 'cloud'], ['möten', 'meetings'], ['kryptering', 'secure-settings']]) assert.equal(ids({}, query)[0], action, query);
  assert.deepEqual(ids({}, '  NEXT   STEP  '), ['task']);
  assert.deepEqual(ids({}, 'a feature that does not exist'), []);
});

test('translated labels and descriptions are complete while English and Swedish searches both work', () => {
  i18n.applyLocale('sv');
  try {
    const translated = getSlashCommands(context);
    assert.equal(translated.find(command => command.id === 'document').label, 'Dokument och staplar');
    assert.equal(translated.find(command => command.id === 'task').label, 'Nästa steg och uppgifter');
    assert.deepEqual(Array.from(translated).flatMap(command => [command.label, command.description]).filter(text => !Object.values(catalog).includes(text)), [], 'Every command label and description needs a Swedish translation.');
    assert.equal(ids({}, 'Documents & stacks')[0], 'document');
    assert.equal(ids({}, 'Nästa steg och uppgifter')[0], 'task');
    assert.equal(ids({}, 'färger')[0], 'appearance');
    assert.equal(ids({}, 'account')[0], 'account');
  } finally { i18n.applyLocale('en'); }
});

test('search remains local and does not mutate context or reuse a stale translated result', () => {
  const scoped = { ...context, workspaceRole: 'member', hasAdmin: false };
  const before = JSON.stringify(scoped);
  const first = getSlashCommands(scoped, 'task');
  first[0].label = 'Changed by caller';
  assert.equal(getSlashCommands(scoped, 'task')[0].label, 'Next steps & tasks');
  assert.equal(JSON.stringify(scoped), before);
});
