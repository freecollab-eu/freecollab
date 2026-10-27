import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function load(path, imports = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { exports, require(name) { if (name in imports) return imports[name]; throw new Error('Unexpected import ' + name); }, Intl, Date, document: { body: {} } });
  return exports;
}
const jsx = { jsx: (type, props, key) => ({ type, props, key: key ?? null }), jsxs: (type, props, key) => ({ type, props, key: key ?? null }), Fragment: 'fragment' };
const translations = { t: value => value, useI18n: () => ({ locale: 'en' }) };
const preview = load('../src/lib/conversation-source-ribbon.ts');
const { ConversationSourceRibbon } = load('../src/components/ConversationSourceRibbon.tsx', {
  'react/jsx-runtime': jsx, 'lucide-react': { ArrowUpLeft: 'ArrowUpLeft', MessageSquare: 'MessageSquare' },
  '../lib/conversation-source-ribbon': preview, '../i18n': translations, '../styles/conversation-source-ribbon.css': {},
});
const source = { id: 'source-message', channel_id: 'courtyard', author_id: 'maja', content: 'Could the long table sit beside the doors?', encrypted: false, created_at: '2026-10-27T10:14:00Z' };
function nodes(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(node => nodes(node, predicate));
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
}
function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  return Array.isArray(node) ? node.map(text).join('') : text(node.props?.children);
}
function button(tree, name) { return nodes(tree, node => node.type === 'button' && text(node) === name)[0]; }

test('source preview is an available message from this channel with a bounded Unicode quote', () => {
  assert.equal(preview.conversationSourcePreview(source, 'courtyard').quote, source.content);
  const compact = preview.conversationSourcePreview({ ...source, content: '  A\n shared\tthought  ' }, 'courtyard');
  assert.equal(compact.quote, 'A shared thought');
  const long = preview.conversationSourcePreview({ ...source, content: '🌿'.repeat(200) }, 'courtyard');
  assert.equal(Array.from(long.quote).length, 180);
  assert.equal(long.quote, '🌿'.repeat(179) + '…');
  for (const message of [null, { ...source, channel_id: 'other' }, { ...source, deleted: true }, { ...source, locked: true }]) {
    assert.equal(preview.conversationSourcePreview(message, 'courtyard'), null);
  }
});

test('actual ribbon shows the channel, supplied author and quote and delegates return without closing work', () => {
  const actions = [];
  const tree = ConversationSourceRibbon({ sourceMessage: source, channelId: 'courtyard', channelName: 'Courtyard', authorName: 'Maja', isCurrent: () => true, onBeforeReveal: () => actions.push('capture-editor-selection'), onRevealSource: () => actions.push('reveal-actual-source') });
  assert.equal(tree.props['aria-label'], 'Source conversation');
  assert.ok(text(tree).includes('CourtyardMaja'));
  assert.ok(text(tree).includes(source.content));
  assert.equal(nodes(tree, node => node.type === 'time')[0].props.dateTime, source.created_at.replace('Z', '.000Z'));
  assert.equal(nodes(tree, node => node.type === 'button').length, 1);
  const returning = button(tree, 'Back to source message');
  returning.props.onPointerDown(); returning.props.onClick();
  assert.deepEqual(actions, ['capture-editor-selection', 'reveal-actual-source']);
});

test('missing author or invalid timestamp adds no fabricated identity, and unavailable context cannot return', () => {
  let current = true, actions = 0;
  const props = { sourceMessage: { ...source, created_at: 'not-a-date' }, channelId: 'courtyard', channelName: 'Courtyard', isCurrent: () => current, onBeforeReveal: () => actions++, onRevealSource: () => actions++ };
  const tree = ConversationSourceRibbon(props);
  assert.equal(nodes(tree, node => node.type === 'time').length, 0);
  assert.ok(!text(tree).includes('maja'));
  current = false;
  const returning = button(tree, 'Back to source message'); returning.props.onPointerDown(); returning.props.onClick();
  assert.equal(actions, 0);
  assert.equal(ConversationSourceRibbon(props), null);
});

function workFixture(overrides = {}) {
  const state = { actor: 'member', token: 'current-session', current: true, actions: [] };
  const hooks = []; let cursor = 0;
  const context = { workspaceId: 'studio', channelId: 'courtyard', isCurrent: () => state.current };
  const api = { getCurrentUserId: () => state.actor, getAccessToken: () => state.token };
  const react = {
    useRef(initial) { const index = cursor++; return hooks[index] ||= { current: initial }; },
    useState(initial) { const index = cursor++; hooks[index] ||= { value: typeof initial === 'function' ? initial() : initial }; return [hooks[index].value, value => { hooks[index].value = typeof value === 'function' ? value(hooks[index].value) : value; }]; },
    useEffect() {},
    useCallback(fn, dependencies) { const index = cursor++; if (!hooks[index] || dependencies.some((value, position) => !Object.is(value, hooks[index].dependencies[position]))) hooks[index] = { callback: fn, dependencies }; return hooks[index].callback; },
  };
  const components = Object.fromEntries(['ProjectTasksPanel', 'MeetingsPanel', 'SharedDocumentsPanel', 'WorkflowsPanel', 'DataControlsPanel', 'MobileSettings', 'ActivityFeedPanel', 'CopilotPanel', 'CloudFilesPicker', 'ExternalMeetingsPanel', 'ChannelSyncNotice', 'ChannelApps', 'FederatedWorkPanel', 'EncryptedWorkPanel', 'ConversationWorkControls'].map(name => ['./' + name, { [name]: name }]));
  const { WorkHub } = load('../src/components/WorkHub.tsx', {
    ...components, react, 'react/jsx-runtime': jsx, 'react-dom': { createPortal: value => value }, '../i18n': translations,
    'lucide-react': new Proxy({}, { get: (_, name) => name }), '../lib/api': { api }, '../lib/useModalFocus': { useModalFocus() {} },
    '../lib/conversation-navigation': { registerConversationNavigationGuard() {}, mayLeaveConversationWork: () => true },
    '../lib/collaboration-context': { useCollaborationContext: () => context }, '../lib/message-references': { normalizeMessageReferences: () => [] },
    './ConversationSourceRibbon': { ConversationSourceRibbon }, '../lib/conversation-source-ribbon': preview, '../styles/work-hub.css': {},
  });
  const draft = { key: 'same-document-draft', title: 'Table layout', text: 'Keep this unsaved edit.' };
  const props = { presentation: 'flow', initialTab: 'documents', initialDocumentId: 'actual-document', initialCommandDraft: draft, workspaceId: 'studio', channel: { id: 'courtyard', name: 'Courtyard' }, sourceMessage: source, sourceAuthorName: 'Maja', layoutActions: { expanded: true, onToggleExpanded: () => state.actions.push('toggle'), onMinimize: () => state.actions.push('minimize'), onBeforeViewChange: () => state.actions.push('remember-editor') }, onRevealSource: () => state.actions.push('reveal-source'), onClose: () => state.actions.push('close'), ...overrides };
  return { state, props, render(changes = {}) { Object.assign(props, changes); cursor = 0; return WorkHub(props); } };
}

test('expanded document uses the ribbon while switching view leaves the actual document target and editor key intact', () => {
  const fixture = workFixture();
  const expanded = fixture.render();
  const ribbon = nodes(expanded, node => node.type === ConversationSourceRibbon);
  assert.equal(ribbon.length, 1);
  assert.equal(ribbon[0].props.authorName, 'Maja');
  const before = nodes(expanded, node => node.type === 'SharedDocumentsPanel')[0];
  const actualRibbon = ConversationSourceRibbon(ribbon[0].props);
  button(actualRibbon, 'Back to source message').props.onPointerDown();
  button(actualRibbon, 'Back to source message').props.onClick();
  assert.deepEqual(fixture.state.actions, ['remember-editor', 'reveal-source']);
  const split = fixture.render({ layoutActions: { ...fixture.props.layoutActions, expanded: false } });
  assert.equal(nodes(split, node => node.type === ConversationSourceRibbon).length, 0);
  assert.ok(button(split, 'Show source message'));
  const after = nodes(split, node => node.type === 'SharedDocumentsPanel')[0];
  assert.equal(after.key, before.key);
  assert.equal(after.props.initialDocumentId, 'actual-document');
  assert.equal(after.props.channelId, before.props.channelId);
  assert.equal(after.props.onPendingChange, before.props.onPendingChange);
});

test('ribbon is limited to expanded documents and a current source context', () => {
  for (const overrides of [{ initialTab: 'tasks' }, { presentation: 'dialog' }, { sourceMessage: { ...source, deleted: true } }, { sourceMessage: { ...source, channel_id: 'other' } }]) {
    const fixture = workFixture(overrides);
    assert.equal(nodes(fixture.render(), node => node.type === ConversationSourceRibbon).length, 0);
  }
  const fixture = workFixture();
  const ribbon = nodes(fixture.render(), node => node.type === ConversationSourceRibbon)[0];
  const returning = button(ConversationSourceRibbon(ribbon.props), 'Back to source message');
  fixture.state.token = 'another-session';
  returning.props.onPointerDown(); returning.props.onClick();
  assert.equal(ribbon.props.isCurrent(), false);
  assert.equal(nodes(fixture.render(), node => node.type === ConversationSourceRibbon).length, 0);
  assert.deepEqual(fixture.state.actions, []);
});
