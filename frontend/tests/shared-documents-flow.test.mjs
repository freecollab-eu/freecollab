import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the shipped editor's actual callbacks. State setters intentionally do
// not render, so these checks also exercise same-turn navigation protection.
function fixture(component = 'SharedDocumentsPanel') {
  const source = readFileSync(new URL('../src/components/' + component + '.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile(component + '.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const wanted = new Set(['addComment', 'publishPending', 'clearPending', 'canLeavePendingDocument', 'mayChangeDocument', 'run', 'revealReadyDocument', 'focusReadyDocument', 'focusSourceComment', 'openOrganizedDocument', 'changeDocumentView', 'restoreDocumentViewFocus']);
  const functions = [], inputs = {}, valueInputs = new Set(); let registration;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && wanted.has(node.name?.text)) functions.push(node.getText(tree));
    if (ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect' && node.arguments[0]?.getText(tree).includes('registerConversationNavigationGuard')) registration = node.arguments[0].getText(tree);
    if (ts.isJsxAttribute(node) && ['onChange', 'onValueChange'].includes(node.name.getText(tree)) && ts.isJsxExpression(node.initializer) && node.initializer.expression) {
      const text = node.initializer.expression.getText(tree);
      for (const name of ['setComment', 'setSnapshotName', 'setNewTitle', 'setNewBody', 'setTitle', 'setRenameTitle', 'setRelatedId', 'setSourceStack']) {
        if (text.includes(name + '(')) { inputs[name] = text; if (node.name.getText(tree) === 'onValueChange') valueInputs.add(name); }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  const state = { credential: 'actor', userId: 'member', generation: 1, pending: false, emissions: [], confirmed: 0, confirm: false, errors: [], values: {}, organizationBusy: false };
  const pending = { current: { yjs: false, create: false, comment: false, snapshot: false, organization: false, organizationBusy: false, busy: false } };
  const drafts = { current: { create: false, rename: false, relation: false, source: false, busy: false, collections: false } };
  const context = { exports: {}, pending, drafts, Object, collectionState: {current: {pending: new Map(), busy: new Map()}}, setCollectionBusy() {}, owner: { current: 'actor' }, generation: { current: 1 },
    mutation: { current: false }, pendingCallback: { current: value => { state.pending = value; state.emissions.push(value); } },
    busyCallback: { current: value => state.organizationBusy = value },
    api: { getAccessToken: () => state.credential, getCurrentUserId: () => state.userId }, current: request => request === context.generation.current && context.owner.current === state.credential,
    window: { confirm: () => { state.confirmed++; return state.confirm; }, matchMedia: () => ({ matches: false }) }, t: value => value, describe: failure => failure.message, message: failure => failure.message,
    setError: value => state.errors.push(value), setNotice: () => {}, setBusy: () => {},
    newTitle: '', newBody: '', surfaceId: 'primary', fieldId: name => name, setNewTitleReferences() {}, setNewBodyReferences() {}, setCommentReferences() {}, commentReferenceDraft: { current: [] }, commentDraft: { current: '' }, commentAnchor: null, commentAnchorRef: {current: null}, setCommentAnchor() {}, revisionDraft: { current: '' }, createId: { current: null }, renaming: { title: 'Original' }, flow: true, workspaceId: 'studio', previewRef: { current: false }, readingPage: { current: null },
  };
  const navigation = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/conversation-navigation.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
    exports: navigation, require: () => ({ api: context.api }), Map, Set,
  });
  context.registerConversationNavigationGuard = navigation.registerConversationNavigationGuard;
  for (const name of Object.keys(inputs)) context[name] = value => { assert.equal(state.pending, true, 'dirty callback must precede scheduled React state'); state.values[name] = value; };
  const exports = functions.map(text => 'exports.' + text.match(/function (\w+)/)[1] + '=' + text.match(/function (\w+)/)[1] + ';').join('\n');
  const callbacks = Object.entries(inputs).map(([name, text]) => 'exports.' + name + '=' + (valueInputs.has(name) ? '(event, references = []) => (' + text + ')(event.target.value, references)' : text) + ';').join('\n');
  const compiled = ts.transpileModule(functions.join('\n') + '\n' + exports + '\n' + callbacks + (registration ? '\nexports.mountGuard=' + registration : ''), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(compiled, context);
  return { ...context.exports, context, state, navigation };
}

test('document drafts publish before React state, and one surface cannot clear another pending surface', () => {
  const f = fixture();
  for (const input of ['setComment', 'setSnapshotName', 'setNewTitle', 'setNewBody']) {
    f.clearPending(); f[input]({ target: { value: 'Unsent work' } }); assert.equal(f.state.pending, true);
  }
  f.publishPending('comment', true); f.publishPending('organization', true); f.publishPending('organization', false);
  assert.equal(f.state.pending, true);
  assert.equal(f.mayChangeDocument(), false); assert.equal(f.state.confirmed, 1);
  f.state.confirm = true; assert.equal(f.mayChangeDocument(), true);
  f.clearPending(); assert.equal(f.state.pending, false);
});

test('stack edits and destination selections publish dirty state before their React updates', () => {
  const f = fixture('SharedDocumentOrganization');
  for (const input of ['setTitle', 'setRenameTitle', 'setRelatedId', 'setSourceStack']) {
    f.clearPending(); f[input]({ target: { value: 'Reviewed destination' } }); assert.equal(f.state.pending, true);
  }
  f.publishPending('source', true); f.publishPending('busy', true); f.publishPending('busy', false);
  assert.equal(f.state.pending, true); f.clearPending(); assert.equal(f.state.pending, false);
});

test('native mutations block same-turn duplicate writes and preserve unsent comments after settlement', async () => {
  const f = fixture(); let release, writes = 0;
  f.publishPending('comment', true);
  const saving = f.run('comment', async () => { writes++; await new Promise(resolve => release = resolve); });
  assert.equal(f.state.pending, true); assert.equal(f.mayChangeDocument(), false); assert.equal(f.state.confirmed, 0);
  await f.run('comment', async () => writes++); assert.equal(writes, 1);
  release(); await saving; assert.equal(f.state.pending, true);
  f.publishPending('comment', false); assert.equal(f.state.pending, false);
});

test('global document navigation blocks actual queued Yjs and in-flight writes without another draft confirmation', () => {
  const f = fixture();
  for (const part of ['yjs', 'busy', 'organizationBusy']) {
    f.publishPending(part, true);
    assert.equal(f.canLeavePendingDocument(), false);
    f.state.confirm = true;
    assert.equal(f.canLeavePendingDocument(), false);
    f.publishPending(part, false);
  }
  f.publishPending('comment', true);
  assert.equal(f.canLeavePendingDocument(), true, 'the parent owns the single unsaved-draft confirmation');
  assert.equal(f.state.confirmed, 0);
});

test('actual flow registration blocks a held native write, permits one parent draft warning and revokes stale authority', async () => {
  const f = fixture(); f.navigation.setConversationNavigationWorkspace('studio');
  const remove = f.mountGuard(); let prompts = 0;
  const removeParent = f.navigation.registerConversationNavigationGuard({ userId: 'member', credential: 'actor', workspaceId: 'studio' }, () => { if (f.state.pending) prompts++; return true; });
  let release;
  const writing = f.run('comment', () => new Promise(resolve => release = resolve));
  assert.equal(f.navigation.mayLeaveConversationWork(), false); assert.equal(prompts, 0);
  release(); await writing;
  f.publishPending('comment', true);
  assert.equal(f.navigation.mayLeaveConversationWork(), true); assert.equal(prompts, 1);
  f.publishPending('yjs', true); f.state.credential = 'replacement';
  assert.equal(f.navigation.mayLeaveConversationWork(), true, 'old identity cannot retain authority');
  remove(); removeParent();
  f.state.credential = 'actor'; f.navigation.setConversationNavigationWorkspace('studio');
  assert.equal(f.navigation.mayLeaveConversationWork(), true, 'revoked old guard does not resurrect');
  const removeAgain = f.mountGuard(); assert.equal(f.navigation.mayLeaveConversationWork(), false);
  removeAgain(); assert.equal(f.navigation.mayLeaveConversationWork(), true);
  f.context.flow = false; assert.equal(f.mountGuard(), undefined);
  assert.equal(f.navigation.mayLeaveConversationWork(), true, 'default full presentation keeps its existing parent lifecycle');
});

test('actual stack writes publish a separate synchronous blocker without treating ordinary stack drafts as writes', async () => {
  const f = fixture('SharedDocumentOrganization'); let release;
  f.publishPending('rename', true); assert.equal(f.state.organizationBusy, false);
  const writing = f.run(() => new Promise(resolve => release = resolve));
  assert.equal(f.state.organizationBusy, true);
  release(); await writing;
  assert.equal(f.state.organizationBusy, false); assert.equal(f.state.pending, true);
});

test('a late old-scope mutation cannot clear a replacement scope draft or authorize a changed account', async () => {
  const f = fixture(); let release;
  const saving = f.run('create', () => new Promise(resolve => release = resolve));
  f.context.generation.current++; f.clearPending(); f.publishPending('create', true);
  release(); await saving; assert.equal(f.state.pending, true);
  f.context.owner.current = 'old-actor'; let writes = 0;
  await f.run('create', async () => writes++); assert.equal(writes, 0);
});

test('deferred document readiness preserves composer focus, while retained work-region focus reaches the native editor', async () => {
  const f = fixture(); let focus = 'work', focused = 0;
  Object.assign(f.context, {
    panel: { current: { closest: () => ({ contains: active => active === 'work' }) } },
    document: { get activeElement() { return focus; } }, selectedId: { current: 'brief' },
    session: { current: { state: () => ({ ready: true, locked: false }) } },
    bodyInput: { current: { focus: () => focused++ } }, focusedDocument: { current: null },
  });
  const deferred = Promise.resolve().then(() => f.focusReadyDocument(1, 'brief'));
  focus = 'composer'; await deferred;
  assert.equal(focused, 0); assert.equal(f.context.focusedDocument.current, 'brief');
  focus = 'work'; f.focusReadyDocument(1, 'brief'); assert.equal(focused, 0, 'consumed initial focus never activates later');
  f.context.focusedDocument.current = null;
  f.focusReadyDocument(1, 'brief'); assert.equal(focused, 1);
  f.context.generation.current++; f.focusReadyDocument(1, 'brief'); assert.equal(focused, 1);
});

test('opening an explicit source comment prevents an already deferred body focus from taking its place', async () => {
  const f = fixture(); let bodyFocused = 0, commentFocused = 0, target;
  Object.assign(f.context, {
    channelId: 'channel', selected: { id: 'previous' }, focusedDocument: { current: null },
    panel: { current: { closest: () => ({ contains: () => true }) } }, document: { activeElement: 'source-comment', getElementById: () => ({ scrollIntoView: () => {}, focus: () => commentFocused++ }) },
    selectedId: { current: 'notes' }, session: { current: { state: () => ({ ready: true, locked: false }) } },
    bodyInput: { current: { focus: () => bodyFocused++ } },
    setCreating: () => {}, setNewTitle: () => {}, setNewBody: () => {}, setComments: () => {}, setComment: () => {}, setSnapshotName: () => {}, setCommentsError: () => {},
    setFocusComment: value => target = value, setSelected: () => {},
  });
  const deferred = Promise.resolve().then(() => f.focusReadyDocument(1, 'notes'));
  f.openOrganizedDocument({ id: 'notes', channel_id: 'channel' }, 'source-comment');
  assert.equal(target.documentId, 'notes'); assert.equal(target.commentId, 'source-comment');
  f.focusSourceComment(1, target); await deferred;
  assert.equal(f.context.focusedDocument.current, 'notes'); assert.equal(bodyFocused, 0); assert.equal(commentFocused, 1);
});

test('a delayed explicit comment target respects a later return to the composer', async () => {
  const f = fixture(); let focus = 'source', focused = 0, consumed = false;
  Object.assign(f.context, {
    panel: { current: { closest: () => ({ contains: active => active === 'source' }) } },
    document: { body: 'body', get activeElement() { return focus; }, getElementById: () => ({ scrollIntoView: () => {}, focus: () => focused++ }) },
    selectedId: { current: 'notes' }, setFocusComment: () => consumed = true,
  });
  const deferred = Promise.resolve().then(() => f.focusSourceComment(1, { documentId: 'notes', commentId: 'source' }));
  focus = 'composer'; await deferred;
  assert.equal(focused, 0); assert.equal(consumed, true);
});

test('initial mobile document readiness reveals actual editor space above a retained multiline composer without taking later focus', () => {
  const f = fixture(); let active = 'work', scrolls = 0, editorTop = 501.953125, small = true;
  const room = { top: 262.890625, bottom: 542.03125 }, composer = { top: 542.03125 }, editorHeight = 280.53125;
  const timeline = { getBoundingClientRect: () => room, closest: () => ({ querySelector: () => ({ getBoundingClientRect: () => composer }) }) };
  const editor = {
    closest: () => timeline, getBoundingClientRect: () => ({ top: editorTop, bottom: editorTop + editorHeight, height: editorHeight }),
    focus: () => active = editor,
    scrollIntoView: options => { assert.equal(options.block, 'start'); assert.equal(options.behavior, 'auto'); scrolls++; editorTop = room.top + 24; },
  };
  Object.assign(f.context, {
    panel: { current: { closest: () => ({ contains: value => value === 'work' || value === editor }) } },
    document: { get activeElement() { return active; } }, bodyInput: { current: editor },
    selectedId: { current: 'brief' }, session: { current: { state: () => ({ ready: true, locked: false }) } }, focusedDocument: { current: null },
    window: { matchMedia: () => ({ matches: small }), innerHeight: 850 },
  });
  f.focusReadyDocument(1, 'brief');
  assert.equal(scrolls, 1); assert.equal(active, editor);
  assert.ok(Math.min(editorTop + editorHeight, composer.top) - Math.max(editorTop, room.top) >= 120);
  f.revealReadyDocument(editor); assert.equal(scrolls, 1, 'an already useful editor does not jump');
  editorTop = 501.953125; f.context.focusedDocument.current = null; active = 'composer';
  f.focusReadyDocument(1, 'brief'); assert.equal(scrolls, 1); assert.equal(active, 'composer');
  small = false; f.context.focusedDocument.current = null; active = 'work';
  f.focusReadyDocument(1, 'brief'); assert.equal(scrolls, 1, 'desktop reading position stays unchanged');
});

test('a saved reading page receives scoped initial focus without activating its retained editor', () => {
  const f = fixture(); let focusedReader = 0, focusedEditor = 0, active = 'work';
  Object.assign(f.context, {
    panel: { current: { closest: () => ({ contains: node => node === 'work' }) } },
    document: { get activeElement() { return active; } },
    previewRef: { current: true }, readingPage: { current: { focus: () => focusedReader++ } },
    bodyInput: { current: { focus: () => focusedEditor++ } },
    selectedId: { current: 'brief' }, session: { current: { state: () => ({ ready: true, locked: false }) } }, focusedDocument: { current: null },
  });
  f.focusReadyDocument(1, 'brief');
  assert.equal(focusedReader, 1); assert.equal(focusedEditor, 0);
  f.context.focusedDocument.current = null; active = 'composer';
  f.focusReadyDocument(1, 'brief');
  assert.equal(focusedReader, 1); assert.equal(focusedEditor, 0);
});

function readingFixture() {
  const f = fixture(); let active = null, writes = 0;
  const field = { isConnected: true, value: 'One unchanged shared document.', selectionStart: 4, selectionEnd: 13,
    selectionDirection: 'backward', scrollTop: 72, scrollLeft: 3,
    focus: () => { active = field; }, closest: () => null, getClientRects: () => [{}],
    setSelectionRange(start, end, direction) { this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction; },
  };
  const reader = { isConnected: true, focus: () => { active = reader; }, closest: () => null, getClientRects: () => [{}] };
  Object.assign(f.context, {
    channelId: 'channel', selected: { id: 'brief', channel_id: 'channel', archived: false }, selectedId: { current: 'brief' },
    state: { ready: true, locked: false }, previewRef: { current: false },
    bodyInput: { current: field }, titleInput: { current: null }, readingPage: { current: reader },
    focusedDocument: { current: null }, lastEditingField: { current: 'body' }, editorPosition: { current: null }, viewFocus: { current: null },
    setPreview: value => { f.context.previewRef.current = value; },
    session: { current: { replace: () => writes++ } },
  });
  return { ...f, field, reader, active: () => active, writes: () => writes };
}

test('read/edit view changes retain selection and textarea scroll without writes, even with edits pending', () => {
  const f = readingFixture(); f.publishPending('yjs', true);
  f.changeDocumentView(true); assert.equal(f.context.previewRef.current, true);
  f.field.selectionStart = f.field.selectionEnd = 0; f.field.scrollTop = f.field.scrollLeft = 0;
  f.restoreDocumentViewFocus(); assert.equal(f.active(), f.reader);
  assert.equal(f.canLeavePendingDocument(), false, 'reading keeps the pending document guard');
  f.changeDocumentView(false); f.restoreDocumentViewFocus();
  assert.equal(f.active(), f.field); assert.equal(f.context.bodyInput.current, f.field);
  assert.deepEqual([f.field.selectionStart, f.field.selectionEnd, f.field.selectionDirection, f.field.scrollTop, f.field.scrollLeft], [4, 13, 'backward', 72, 3]);
  assert.equal(f.writes(), 0); assert.equal(f.state.pending, true);
});

test('view restoration respects changed text, workspace, authority and archived editing restrictions', () => {
  const f = readingFixture(); f.changeDocumentView(true); f.restoreDocumentViewFocus();
  f.field.value = 'Remote text replaced the original selection.';
  f.field.selectionStart = f.field.selectionEnd = 2; f.field.scrollTop = 0;
  f.changeDocumentView(false); f.restoreDocumentViewFocus();
  assert.equal(f.active(), f.field); assert.equal(f.field.selectionStart, 2); assert.equal(f.field.scrollTop, 0);
  f.changeDocumentView(true); f.context.workspaceId = 'another-workspace'; f.restoreDocumentViewFocus();
  assert.equal(f.active(), f.field, 'a queued view focus cannot cross its workspace');
  f.context.workspaceId = 'studio'; f.context.viewFocus.current = null;
  f.context.selected.archived = true; f.changeDocumentView(false);
  assert.equal(f.context.previewRef.current, true, 'archived documents cannot enter editing');
  f.context.selected.archived = false; f.state.credential = 'replacement'; f.changeDocumentView(false);
  assert.equal(f.context.previewRef.current, true, 'old authority cannot enter editing');
  assert.equal(f.writes(), 0);
});

test('document guards protect only their retained surface on a local close and all surfaces on channel navigation', () => {
  const primary = fixture(), sibling = fixture();
  primary.navigation.setConversationNavigationWorkspace('studio');
  sibling.context.surfaceId = 'parallel-document';
  sibling.context.registerConversationNavigationGuard = primary.navigation.registerConversationNavigationGuard;
  const removePrimary = primary.mountGuard(), removeSibling = sibling.mountGuard();
  try {
    sibling.publishPending('yjs', true);
    assert.equal(primary.navigation.mayLeaveConversationWork('primary'), true);
    assert.equal(primary.navigation.mayLeaveConversationWork('parallel-document'), false);
    assert.equal(primary.navigation.mayLeaveConversationWork(), false);
  } finally { removePrimary(); removeSibling(); }
});

test('actual document input callbacks retain selected IDs and a same-label replacement survives an earlier comment save', async () => {
  const f = fixture(), chosen = [{ kind: 'person', id: 'chosen-peer', start: 4, end: 9 }];
  f.context.setNewBodyReferences = value => { f.state.values.newBodyReferences = value; };
  f.context.setCommentReferences = value => { f.state.values.commentReferences = value; };
  f.setNewBody({ target: { value: 'Ask @Peer' } }, chosen);
  assert.deepEqual(f.state.values.newBodyReferences, chosen);
  f.setComment({ target: { value: 'Ask @Peer' } }, chosen);
  assert.deepEqual(f.context.commentReferenceDraft.current, chosen);
  let release, written, clears = 0;
  Object.assign(f.context, {
    selected: { id: 'doc' }, selectedId: { current: 'doc' }, channelId: 'channel', comment: 'Ask @Peer', commentReferences: chosen,
    sharedDocumentsApi: { comment: async (...args) => { written = args; await new Promise(resolve => release = resolve); } },
    loadComments: async () => {}, setComment: () => clears++,
  });
  const saving = f.addComment({ preventDefault() {} });
  const replacement = [{ ...chosen[0], id: 'other-peer' }];
  f.context.commentReferenceDraft.current = replacement;
  release(); await saving;
  assert.deepEqual(written, ['channel', 'doc', 'Ask @Peer', chosen, undefined]);
  assert.equal(clears, 0, 'a newer selection with identical visible text is still unsaved work');
  assert.equal(f.state.pending, true);
});

test('an in-flight material collection blocks parent organization writes before React renders', async () => {
  const f = fixture('SharedDocumentOrganization'); let writes = 0;
  f.context.collectionState.current.busy.set('actual-stack:child', true);
  await f.run(async () => { writes++; });
  assert.equal(writes, 0);
  f.context.collectionState.current.busy.set('actual-stack:child', false);
  await f.run(async () => { writes++; });
  assert.equal(writes, 1);
});
