import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute actual Chat callbacks with held transport promises. No copy of their
// authorization or navigation decisions lives in this fixture.
function fixture() {
  const source = readFileSync(new URL('../src/components/Chat.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('Chat.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const wanted = new Set(['handleCreateChannel', 'openMessageReference', 'refreshMemberNames']);
  const declarations = [];
  function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && wanted.has(node.name.text)) declarations.push('const ' + node.getText(tree) + ';');
    ts.forEachChild(node, visit);
  }
  visit(tree); assert.equal(declarations.length, wanted.size);
  const state = { token: 'credential', actor: 'actor', allowed: true, preflights: 0, creates: 0, selected: [], files: [], errors: [], names: {}, channels: [{ id: 'room', role: 'admin' }], active: { id: 'room', role: 'admin' }, posts: [], revoked: 0 };
  class APIError extends Error { constructor(status) { super('Permission changed'); this.status = status; } }
  const context = {
    exports: {}, APIError, Object, Array, Boolean, document: { activeElement: null }, HTMLElement: class HTMLElement {},
    api: { getAccessToken: () => state.token, getCurrentUserId: () => state.actor,
      createChannel: async () => { state.creates++; return { id: 'new-room', workspace_id: 'workspace' }; },
      request: async (...args) => { state.posts.push(args); return []; }, getChannelMembers: async () => [{ user_id: 'actor', name: 'Actor', role: 'member' }] },
    embeddedChannelId: undefined, newChannelName: 'New team', creating: false, createMutation: { current: false },
    workspace: { id: 'workspace' }, workspaceRef: { current: 'workspace' }, activeChannel: { id: 'room', secure: false }, activeChannelRef: { current: 'room' },
    flowScope: 'scope-one', liveFlowScope: { current: 'scope-one' }, flowIntent: { current: 0 }, workReturnRequest: { current: 0 }, referenceOpenRequest: { current: 0 },
    createDialogRef: { current: { isConnected: true } }, secureAvailable: false, createSecure: false,
    mayLeaveConversationWork: () => { state.preflights++; return state.allowed; },
    setCreating() {}, setCreateError: value => { if (value) state.errors.push(value); }, setNewChannelName() {}, setShowNewChannel() {}, setSidebarOpen() {},
    setChannels: update => { state.channels = typeof update === 'function' ? update(state.channels) : update; },
    selectChannel: (channel, preflight) => { state.selected.push({ channel, preflight }); return true; },
    listChannelFiles: async () => [{ id: 'file', channel_id: 'room' }], getVerifiedSecureFileReference: async () => undefined,
    restoreRetainedModals() {}, workLayout: { mode: 'split', toggleExpanded() {} },
    setReferencedFile: value => { if (value) state.files.push(value); }, setActionError: value => state.errors.push(value),
    setChannelDetailsOpen() {}, setReferenceMemberId() {}, setThreadMessage() {}, setToolPanel() {},
    t: value => value, modalRegistry: { current: new Map() }, captureModalReturn: () => undefined, modalReturnCurrent: () => true, parkModalReturn() {}, resumeModalReturn: () => false, activateModalTarget() {}, retainToolSource() {}, memberRefreshRequest: { current: 0 }, setMemberNames: value => { state.names = value; },
    setActiveChannel: update => { state.active = typeof update === 'function' ? update(state.active) : update; },
    sameRoomWork: { current: { revoke: () => state.revoked++ } }, setMessages() {}, setThreadReplies() {}, loadChannels: async () => {},
  };
  vm.runInNewContext(ts.transpileModule(declarations.join('\n') + '\n' + [...wanted].map(name => 'exports.' + name + '=' + name + ';').join('\n'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return { ...context.exports, context, state, APIError };
}
const event = { preventDefault() {} };
const fileReference = id => ({ kind: 'file', id, channel_id: 'room', label: 'File', available: true });

test('channel creation checks global dirty work before any write and admits only one same-turn request', async () => {
  const f = fixture(); f.state.allowed = false;
  await f.handleCreateChannel(event); assert.equal(f.state.creates, 0);
  f.state.allowed = true; let release;
  f.context.api.createChannel = async () => { f.state.creates++; return new Promise(resolve => release = resolve); };
  const pending = f.handleCreateChannel(event);
  await f.handleCreateChannel(event); assert.equal(f.state.creates, 1);
  release({ id: 'new-room', workspace_id: 'workspace' }); await pending;
  assert.equal(f.state.selected.length, 1); assert.equal(f.state.selected[0].preflight, true);
  assert.equal(f.context.createMutation.current, false);
});

for (const change of ['actor', 'workspace', 'channel', 'secure', 'dialog']) test('late channel creation cannot select after ' + change + ' changes', async () => {
  const f = fixture(); let release;
  f.context.api.createChannel = async () => new Promise(resolve => release = resolve);
  const pending = f.handleCreateChannel(event);
  if (change === 'actor') f.state.actor = 'replacement';
  if (change === 'workspace') f.context.workspaceRef.current = 'other';
  if (change === 'channel') f.context.activeChannelRef.current = 'other';
  if (change === 'secure') f.context.liveFlowScope.current = 'same-channel-now-secure';
  if (change === 'dialog') f.context.createDialogRef.current = { isConnected: true };
  release({ id: 'new-room', workspace_id: 'workspace' }); await pending;
  assert.equal(f.state.selected.length, 0); assert.equal(f.state.channels.length, 1);
});

for (const change of ['source-closed', 'secure', 'other-work', 'newer-command']) test('a pending file read cannot open or issue follow-up lookups after ' + change, async () => {
  const f = fixture(); let release, live = true;
  f.context.listChannelFiles = async () => new Promise(resolve => release = resolve);
  const pending = f.openMessageReference(fileReference('old-file'), undefined, () => live);
  if (change === 'source-closed') live = false;
  if (change === 'secure') f.context.liveFlowScope.current = 'same-channel-now-secure';
  if (change === 'other-work') f.context.workReturnRequest.current++;
  if (change === 'newer-command') f.context.referenceOpenRequest.current++;
  release([]); await pending;
  assert.equal(f.state.files.length, 0); assert.equal(f.state.posts.length, 0); assert.equal(f.state.errors.length, 0);
});

test('the newer file click wins when two verified lookups finish in reverse order', async () => {
  const f = fixture(), releases = [];
  f.context.listChannelFiles = async () => new Promise(resolve => releases.push(resolve));
  const first = f.openMessageReference(fileReference('first'), undefined, () => true);
  const second = f.openMessageReference(fileReference('second'), undefined, () => true);
  releases[1]([{ id: 'second', channel_id: 'room' }]); await second;
  releases[0]([{ id: 'first', channel_id: 'room' }]); await first;
  assert.equal(f.state.files.length, 1); assert.equal(f.state.files[0].file.id, 'second'); assert.equal(f.state.files[0].scope, 'scope-one');
});

test('a native historical version lookup retains the same READ lifetime until its second await finishes', async () => {
  const f = fixture(); let release, live = true, requestStarted;
  const started = new Promise(resolve => requestStarted = resolve);
  f.context.listChannelFiles = async () => [];
  f.context.api.request = async () => { requestStarted(); return new Promise(resolve => release = resolve); };
  const pending = f.openMessageReference(fileReference('historical'), undefined, () => live);
  await started; live = false;
  release([{ id: 'historical', channel_id: 'room' }]); await pending;
  assert.equal(f.state.files.length, 0);
});

test('authenticated roster refresh changes role, while an older refresh cannot restore revoked management authority', async () => {
  const f = fixture(), releases = [];
  f.context.api.getChannelMembers = async () => new Promise(resolve => releases.push(resolve));
  const older = f.refreshMemberNames('room'), newer = f.refreshMemberNames('room');
  releases[1]([{ user_id: 'actor', name: 'Current actor', role: 'member' }]); await newer;
  releases[0]([{ user_id: 'actor', name: 'Earlier actor', role: 'admin' }]); await older;
  assert.equal(f.state.active.role, 'member'); assert.equal(f.state.channels[0].role, 'member'); assert.equal(f.state.names.actor, 'Current actor');
});

test('roster denial revokes work immediately; a stale actor response cannot change current names or role', async () => {
  const f = fixture(); f.context.api.getChannelMembers = async () => { throw new f.APIError(403); };
  await f.refreshMemberNames('room'); assert.equal(f.state.revoked, 1); assert.equal(f.state.active, null);
  const g = fixture(); let release;
  g.context.api.getChannelMembers = async () => new Promise(resolve => release = resolve);
  const pending = g.refreshMemberNames('room'); g.state.actor = 'new-actor';
  release([{ user_id: 'actor', name: 'Old actor', role: 'admin' }]); await pending;
  assert.deepEqual(g.state.names, {}); assert.equal(g.state.active.role, 'admin');
});

function readingReturnFixture() {
  const source = readFileSync(new URL('../src/components/Chat.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('Chat.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const wanted = new Set(['markFlowMoved', 'mayRestoreWorkFocus', 'closeWorkHub', 'removeRetainedWork']), declarations = [];
  function visit(node) { if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && wanted.has(node.name.text)) declarations.push('const ' + node.getText(tree) + ';'); ts.forEachChild(node, visit); }
  visit(tree); assert.equal(declarations.length, wanted.size);
  const body = {}, closeButton = {}, composer = { isConnected: true }, sibling = {};
  const frames = new Map(); let nextFrame = 0;
  const state = { token: 'credential', actor: 'actor', active: closeButton, focused: 0 };
  const origin = { top: 473, readingIntent: 0, token: 'credential', workspaceId: 'workspace', channelId: 'room', element: null };
  const timeline = { scrollTop: 0, closest: () => null };
  composer.focus = () => { state.active = composer; state.focused++; };
  const context = { exports: {}, document: { body, get activeElement() { return state.active; }, getElementById: () => null },
    api: { getAccessToken: () => state.token, getCurrentUserId: () => state.actor }, flowConversationMoved: { current: false }, flowIntent: { current: 0 }, readingIntent: { current: 0 },
    workReturnRequest: { current: 0 }, workReturnFrame: { current: 0 }, modalReturnIntentCleanup: { current: null }, roomOverviewRef: { current: false }, followingLatestRef: { current: false }, followTimeoutRef: { current: 0 },
    workHubOrigin: { current: origin }, primaryResource: { current: null }, primaryCollaborationOrigin: { current: null },
    activeChannelRef: { current: 'room' }, workspaceRef: { current: 'workspace' }, timelineRef: { current: timeline }, inputRef: { current: composer }, slashLauncherRef: { current: null },
    threadRequest: { current: 0 }, threadMessageRef: { current: null }, threadInputRef: { current: null }, threadDialogRef: { current: null }, messagesRef: { current: [] },
    retainedLayouts: { current: new Map() }, retainedCloseControllers: { current: new Map() }, retainedWorkRef: { current: [] },
    workAreaRef: { current: null }, workLayout: { reset() {} }, flowScope: 'scope', workScopeCurrent: value => value === 'scope',
    handleTimelineScroll() {}, resumeModalReturn: () => false, publishRetainedWork(value) { context.retainedWorkRef.current = value; },
    cancelAnimationFrame: id => frames.delete(id), clearTimeout() {}, requestAnimationFrame: callback => { const id = ++nextFrame; frames.set(id, callback); return id; },
  };
  for (const match of declarations.join('\n').matchAll(/\b(set[A-Z]\w*)\(/g)) context[match[1]] = () => {};
  vm.runInNewContext(ts.transpileModule(declarations.join('\n') + '\n' + [...wanted].map(name => 'exports.' + name + '=' + name + ';').join('\n'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const flush = () => { for (const [id, callback] of [...frames]) { frames.delete(id); callback(); } };
  return { ...context.exports, context, state, origin, timeline, body, composer, sibling, flush };
}

test('typing in the still-live composer cancels old focus but preserves the conversation reading return', () => {
  const f = readingReturnFixture();
  f.markFlowMoved(false); assert.equal(f.context.flowConversationMoved.current, true); assert.equal(f.context.readingIntent.current, 0);
  f.closeWorkHub(); f.state.active = f.body; f.flush();
  assert.equal(f.timeline.scrollTop, 473); assert.equal(f.state.active, f.composer); assert.equal(f.context.roomOverviewRef.current, true);
});

test('a deliberate reading navigation wins over the old work source position', () => {
  const f = readingReturnFixture();
  f.markFlowMoved(); assert.equal(f.context.readingIntent.current, 1);
  f.closeWorkHub(); f.state.active = f.body; f.flush();
  assert.equal(f.timeline.scrollTop, 0);
});

test('reading restoration remains independent from a newer sibling field focus', () => {
  const f = readingReturnFixture();
  f.markFlowMoved(false); f.closeWorkHub(); f.state.active = f.sibling; f.flush();
  assert.equal(f.timeline.scrollTop, 473); assert.equal(f.state.active, f.sibling); assert.equal(f.state.focused, 0);
});

test('a retained reading return survives composer edits without reopening an old editor or stealing focus', () => {
  const f = readingReturnFixture(), entry = { id: 'retained-task', scope: 'scope', returnIntent: 0, conversationReturn: f.origin };
  f.context.retainedWorkRef.current = [entry]; f.markFlowMoved(false);
  f.removeRetainedWork(entry); f.state.active = f.sibling; f.flush();
  assert.equal(f.timeline.scrollTop, 473); assert.equal(f.state.focused, 0); assert.equal(f.context.retainedWorkRef.current.length, 0);
});

test('revoked actors cannot apply a pending reading return', () => {
  const f = readingReturnFixture();
  f.closeWorkHub(); f.state.actor = 'replacement'; f.state.active = f.body; f.flush();
  assert.equal(f.timeline.scrollTop, 0); assert.equal(f.state.focused, 0);
});

function modalReturnFixture() {
  const source = readFileSync(new URL('../src/components/Chat.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('Chat.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const wanted = new Set(['modalReturnCurrent', 'captureModalReturn', 'publishParkedModals', 'parkModalReturn', 'resumeModalReturn', 'registerCollaborationModal', 'activateModalTarget', 'resumeTargetModal', 'retainToolSource', 'closeToolPanel']), declarations = [];
  function visit(node) { if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && wanted.has(node.name.text)) declarations.push('const ' + node.getText(tree) + ';'); ts.forEachChild(node, visit); }
  visit(tree); assert.equal(declarations.length, wanted.size);
  const state = { scope: 'scope', active: {}, parked: [], calls: [], restored: 0, handoff: { sequence: 0, modal: null, target: null }, heldTools: [], toolPanel: 'files' }, body = {}, input = { isConnected: true, closest: () => null, getClientRects: () => [{}], focus: () => state.restored++ };
  const parentRoot = { isConnected: true, contains: () => false }, sourceRoot = { isConnected: true, contains: element => element === input || element === sourceRoot };
  const root = (id, element, parentId, resourceKey = undefined) => ({ id, root: element, title: id, parentId, resourceKey, isCurrent: () => element.isConnected,
    park: () => state.calls.push('park:' + id), resume: () => state.calls.push('resume:' + id) });
  const parent = root('tools', parentRoot, undefined, 'tools:room:files'), sourceModal = root('file', sourceRoot, 'tools', 'file:room:original');
  const frames = new Map(), microtasks = [], listeners = new Map(); let id = 0;
  const context = { exports: {}, Map, Set, crypto: { randomUUID: () => 'modal-return-' + ++id }, flowScope: 'scope', workScopeCurrent: value => value === state.scope,
    getModalFocusRestoration: () => state.handoff, modalRegistry: { current: new Map() }, modalTargetReturns: { current: new Map() }, activeChannel: { id: 'room' }, setHeldToolPanels: update => { state.heldTools = update(state.heldTools); }, setToolPanel: update => { state.toolPanel = update(state.toolPanel); }, setMaterialRefresh() {}, parkedModalsRef: { current: [] }, setParkedModals: value => state.parked = value,
    document: { body, get activeElement() { return state.active; }, addEventListener: (kind, callback) => { const rows = listeners.get(kind) || new Set(); rows.add(callback); listeners.set(kind, rows); }, removeEventListener: (kind, callback) => listeners.get(kind)?.delete(callback) }, workLayout: { mode: 'split' }, restoreRetainedModals() {},
    workReturnRequest: { current: 0 }, workReturnFrame: { current: 0 }, modalReturnIntentCleanup: { current: null }, flowIntent: { current: 0 },
    cancelAnimationFrame: key => frames.delete(key), requestAnimationFrame: callback => { const key = ++id; frames.set(key, callback); return key; }, queueMicrotask: callback => microtasks.push(callback),
    mayRestoreWorkFocus: (expected, desired, _surface, dialog) => state.active === expected || state.active === body || dialog?.contains(state.active), t: value => value,
  };
  vm.runInNewContext(ts.transpileModule(declarations.join('\n') + '\n' + [...wanted].map(name => 'exports.' + name + '=' + name + ';').join('\n'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const helpers = context.exports;
  const removeParent = helpers.registerCollaborationModal(parent), removeSource = helpers.registerCollaborationModal(sourceModal);
  const origin = { input, isCurrent: () => true, restore: () => { state.restored++; return true; } };
  return { ...helpers, context, state, body, input, parent, sourceModal, sourceRoot, origin, removeParent, removeSource, listeners,
    interact(kind, event = {}) { for (const callback of [...(listeners.get(kind) || [])]) callback(event); },
    flushFrames() { for (const [key, callback] of [...frames]) { frames.delete(key); callback(); } },
    flushMicrotasks() { for (const callback of microtasks.splice(0)) callback(); } };
}

test('modal work parks every explicit portal ancestor and resumes the same current controls and source field', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  assert.deepEqual(Array.from(value.chain, modal => modal.id), ['file', 'tools']);
  f.parkModalReturn(value); assert.deepEqual(f.state.calls, ['park:file', 'park:tools']); assert.equal(f.state.parked.length, 1);
  f.resumeModalReturn(value); f.state.active = f.body; f.flushFrames();
  assert.deepEqual(f.state.calls, ['park:file', 'park:tools', 'resume:tools', 'resume:file']); assert.equal(f.state.parked.length, 0); assert.equal(f.state.restored, 1);
});

test('title or locale re-registration preserves a parked source and resumes its replacement controls', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.parkModalReturn(value); f.removeSource();
  const replacement = { ...f.sourceModal, title: 'Localized preview', resume: () => f.state.calls.push('resume:new-title') };
  f.registerCollaborationModal(replacement); f.flushMicrotasks();
  assert.equal(f.state.parked.length, 1); assert.equal(f.modalReturnCurrent(value), true);
  f.resumeModalReturn(value); f.state.active = f.body; f.flushFrames();
  assert.ok(f.state.calls.includes('resume:new-title')); assert.equal(f.state.restored, 1);
});

test('actual modal removal, resource replacement and authority change cannot resurrect old source drafts', () => {
  for (const change of ['unmount', 'resource', 'scope']) {
    const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin); f.parkModalReturn(value);
    if (change === 'unmount') { f.removeSource(); f.sourceRoot.isConnected = false; }
    if (change === 'resource') { f.removeSource(); f.registerCollaborationModal({ ...f.sourceModal, resourceKey: 'file:room:different' }); }
    if (change === 'scope') f.state.scope = 'different-authority';
    f.flushMicrotasks(); assert.equal(f.modalReturnCurrent(value), false); assert.equal(f.resumeModalReturn(value), false); assert.equal(f.state.restored, 0);
    if (change !== 'scope') assert.equal(f.state.parked.length, 0);
  }
});

test('a later field focus wins over deferred modal-origin focus restoration', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.parkModalReturn(value); f.resumeModalReturn(value); f.state.active = {}; f.flushFrames();
  assert.equal(f.state.restored, 0);
});

test('a command opening its own registered modal never hides the source', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.activateModalTarget('file:room:original', value);
  assert.deepEqual(f.state.calls, []); assert.equal(f.state.parked.length, 0);
});

test('opening the existing parent parks only the child and closing returns to that same child', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.activateModalTarget('tools:room:files', value);
  assert.deepEqual(f.state.calls, ['park:file', 'resume:tools']); assert.equal(f.state.parked.length, 1);
  assert.equal(f.resumeTargetModal('tools:room:files'), true);
  assert.deepEqual(f.state.calls.slice(-2), ['resume:tools', 'resume:file']); assert.equal(f.state.parked.length, 0);
});

test('opening another tools tab retains its source instance and close resumes the saved source', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.retainToolSource(value, 'members'); assert.deepEqual(Array.from(f.state.heldTools), ['files']);
  f.activateModalTarget('tools:room:members', value);
  assert.deepEqual(f.state.calls, ['park:file', 'park:tools']);
  const membersRoot = { isConnected: true, contains: () => false };
  f.registerCollaborationModal({ ...f.parent, id: 'members', root: membersRoot, resourceKey: 'tools:room:members' });
  f.state.toolPanel = 'members'; f.closeToolPanel('members');
  assert.equal(f.state.toolPanel, null); assert.deepEqual(Array.from(f.state.heldTools), ['files']);
  assert.deepEqual(f.state.calls.slice(-2), ['resume:tools', 'resume:file']);
});

test('file READ retains the exact clicked source before its asynchronous lookup can blur focus', async () => {
  const f = fixture(); let release; const sourceElement = {}, captured = [], parked = [], modal = {};
  f.context.captureModalReturn = element => { captured.push(element); return modal; };
  f.context.parkModalReturn = value => parked.push(value);
  f.context.listChannelFiles = async () => new Promise(resolve => release = resolve);
  const pending = f.openMessageReference(fileReference('file'), undefined, () => true, sourceElement);
  assert.equal(captured[0], sourceElement);
  f.context.document.activeElement = {};
  release([{ id: 'file', channel_id: 'room' }]); await pending;
  assert.equal(f.state.files[0].modal, modal); assert.equal(parked[0], modal); assert.equal(captured.length, 1);
});

test('parking a local file retains its actual tools parent through unrelated foreground changes', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.parkModalReturn(value); assert.deepEqual(Array.from(f.state.heldTools), ['files']);
  f.state.toolPanel = null;
  assert.deepEqual(Array.from(f.state.heldTools), ['files']); assert.equal(f.modalReturnCurrent(value), true);
});

test('reopening a parked ancestor without an editing origin still resumes its child on close without a discard prompt', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin);
  f.parkModalReturn(value); f.activateModalTarget('tools:room:files');
  assert.equal(f.state.calls.at(-1), 'resume:tools');
  f.closeToolPanel('files');
  assert.equal(f.state.toolPanel, 'files'); assert.deepEqual(Array.from(f.state.heldTools), ['files']);
  assert.deepEqual(f.state.calls.slice(-2), ['resume:tools', 'resume:file']); assert.equal(f.state.parked.length, 0);
});

test('closing a target may focus the restored source dialog close button before the exact saved field', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin), close = {};
  const contains = f.sourceRoot.contains; f.sourceRoot.contains = element => element === close || contains(element);
  f.parkModalReturn(value); f.resumeModalReturn(value); f.state.active = close; f.flushFrames();
  assert.equal(f.state.restored, 1); assert.equal(f.listeners.get('pointerdown').size, 0); assert.equal(f.listeners.get('keydown').size, 0);
});

for (const kind of ['pointerdown', 'keydown']) test('a newer actual ' + kind + ' wins even inside the automatically restored source dialog', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin), other = {};
  const contains = f.sourceRoot.contains; f.sourceRoot.contains = element => element === other || contains(element);
  f.parkModalReturn(value); f.resumeModalReturn(value); f.state.active = other;
  f.interact(kind, { key: 'ArrowLeft' }); f.flushFrames();
  assert.equal(f.state.restored, 0); assert.equal(f.listeners.get('pointerdown').size, 0); assert.equal(f.listeners.get('keydown').size, 0);
});

test('only a new cleanup receipt from the exact closing modal permits its transient fallback composer focus', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin), closingModal = {}, fallback = {};
  const button = { closest: () => closingModal }; f.state.active = button;
  f.parkModalReturn(value); f.resumeModalReturn(value);
  f.state.active = fallback; f.state.handoff = { sequence: 1, modal: closingModal, target: fallback }; f.flushFrames();
  assert.equal(f.state.restored, 1);
});

for (const stale of ['old', 'different-modal', 'different-field', 'pointer']) test('a ' + stale + ' cleanup receipt cannot take focus from a newer field', () => {
  const f = modalReturnFixture(), value = f.captureModalReturn(f.input, f.origin), closingModal = {}, fallback = {};
  f.state.active = { closest: () => closingModal };
  if (stale === 'old') f.state.handoff = { sequence: 1, modal: closingModal, target: fallback };
  f.parkModalReturn(value); f.resumeModalReturn(value);
  f.state.active = fallback;
  if (stale !== 'old') f.state.handoff = { sequence: 1, modal: stale === 'different-modal' ? {} : closingModal, target: stale === 'different-field' ? {} : fallback };
  if (stale === 'pointer') f.interact('pointerdown');
  f.flushFrames(); assert.equal(f.state.restored, 0);
});
