import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { webcrypto, randomUUID } from 'node:crypto';
import ts from 'typescript';
const require = createRequire(import.meta.url), Y = require('yjs'), cache = new Map();
function load(name, extra = {}, globals = {}) {
  if (!Object.keys(extra).length && cache.has(name)) return cache.get(name);
  const exports = {}, source = readFileSync(new URL('../src/lib/' + name + '.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, require: dependency => dependency === 'yjs' ? Y : Object.hasOwn(extra, dependency) ? extra[dependency] : dependency === './socket' ? { getSocket: () => ({ on() {}, off() {} }) } : load(dependency.replace('./', '')),
      Uint8Array, ArrayBuffer, atob, btoa, crypto: { randomUUID, subtle: webcrypto.subtle }, setTimeout, clearTimeout, Map, Set, Date, Symbol, ...globals });
  if (!Object.keys(extra).length) cache.set(name, exports);
  return exports;
}
const refs = load('document-references'), plain = value => JSON.parse(JSON.stringify(value));
function doc(body = 'Ask @Eva and @Maja.') { const value = new Y.Doc(); value.getText('title').insert(0, 'Shared page'); value.getText('body').insert(0, body); return value; }
function copy(value) { const result = new Y.Doc(); Y.applyUpdate(result, Y.encodeStateAsUpdate(value)); return result; }
const eva = { kind: 'person', id: 'eva-id', start: 4, end: 8 }, maja = { kind: 'person', id: 'maja-id', start: 13, end: 18 };
const read = value => plain(refs.readDocumentReferences(value));

test('legacy text documents remain valid without creating an empty metadata map', () => {
  const value = doc(); try { refs.validateDocumentReferences(value); refs.writeDocumentReferences(value, 'body', []); assert.equal(value.share.has(refs.DOCUMENT_REFERENCE_MAP), false); assert.deepEqual(read(value), []); } finally { value.destroy(); }
});
test('explicit IDs survive Yjs reload while labels remain plain readable text', () => {
  const value = doc(); try { refs.writeDocumentReferences(value, 'body', [eva]); assert.equal(value.getText('body').toString(), 'Ask @Eva and @Maja.'); assert.deepEqual(read(value), [eva]); const saved = copy(value); try { refs.validateDocumentReferences(saved); assert.deepEqual(read(saved), [eva]); } finally { saved.destroy(); } } finally { value.destroy(); }
});
test('actual concurrent prefix insertion preserves identity and UTF-16 offsets', () => {
  const first = doc(), second = copy(first); try { refs.writeDocumentReferences(first, 'body', [eva]); second.getText('body').insert(0, '🌿 '); Y.applyUpdate(first, Y.encodeStateAsUpdate(second)); Y.applyUpdate(second, Y.encodeStateAsUpdate(first)); const expected = { ...eva, start: 7, end: 11 }; assert.deepEqual(read(first), [expected]); assert.deepEqual(read(second), [expected]); assert.equal(first.getText('body').toString().slice(7, 11), '@Eva'); } finally { first.destroy(); second.destroy(); }
});
test('offline coauthors retain both distinct choices and a stale local render cannot erase its peer', () => {
  const first = doc(), second = copy(first); try { refs.writeDocumentReferences(first, 'body', [eva]); refs.writeDocumentReferences(second, 'body', [maja]); Y.applyUpdate(first, Y.encodeStateAsUpdate(second)); Y.applyUpdate(second, Y.encodeStateAsUpdate(first)); assert.deepEqual(read(first), [eva, maja]); assert.deepEqual(read(second), [eva, maja]); refs.writeDocumentReferences(first, 'body', [eva]); assert.deepEqual(read(first), [eva, maja]); } finally { first.destroy(); second.destroy(); }
});
test('typing exactly at a mention end never expands its identity', () => {
  const value = doc(); try { refs.writeDocumentReferences(value, 'body', [eva]); value.getText('body').insert(eva.end, '!'); assert.deepEqual(read(value), [eva]); } finally { value.destroy(); }
});
test('an edited label remains visible and drops its former identity', () => {
  const value = doc(); try { refs.writeDocumentReferences(value, 'body', [eva]); value.getText('body').insert(6, 'x'); assert.deepEqual(read(value), []); assert.equal(value.getText('body').toString(), 'Ask @Exva and @Maja.'); } finally { value.destroy(); }
});
test('conflicting offline identities over one visible name are never arbitrarily inferred', () => {
  const first = doc('Ask @Alex.'), second = copy(first); try { refs.writeDocumentReferences(first, 'body', [{ ...eva, id: 'alex-one', end: 9 }]); refs.writeDocumentReferences(second, 'body', [{ ...eva, id: 'alex-two', end: 9 }]); Y.applyUpdate(first, Y.encodeStateAsUpdate(second)); assert.deepEqual(read(first), []); assert.equal(first.getText('body').toString(), 'Ask @Alex.'); } finally { first.destroy(); second.destroy(); }
});
test('duplicate offline choices collapse and explicit same-name replacement binds the chosen new ID', () => {
  const first = doc(), second = copy(first); try { refs.writeDocumentReferences(first, 'body', [eva]); refs.writeDocumentReferences(second, 'body', [eva]); Y.applyUpdate(first, Y.encodeStateAsUpdate(second)); assert.deepEqual(read(first), [eva]); refs.writeDocumentReferences(first, 'body', [{ ...eva, id: 'other-eva' }]); assert.deepEqual(read(first), [{ ...eva, id: 'other-eva' }]); } finally { first.destroy(); second.destroy(); }
});
test('revision restoration rebases old identities onto newly inserted characters', () => {
  const current = doc(), snapshot = copy(current); try { refs.writeDocumentReferences(snapshot, 'body', [eva, maja]); current.getText('body').delete(0, current.getText('body').length); current.getText('body').insert(0, 'Newer text'); current.transact(() => { current.getText('body').delete(0, current.getText('body').length); current.getText('body').insert(0, snapshot.getText('body').toString()); refs.restoreDocumentReferences(current, snapshot); }); assert.deepEqual(read(current), [eva, maja]); const saved = copy(current); try { assert.deepEqual(read(saved), [eva, maja]); } finally { saved.destroy(); } } finally { current.destroy(); snapshot.destroy(); }
});
test('fabricated offsets and malformed reference maps fail before editing text', () => {
  const value = doc(); try { assert.throws(() => refs.validateDocumentReferenceWrite(value, 'Changed', [eva]), /references/i); assert.equal(value.getText('body').toString(), 'Ask @Eva and @Maja.'); value.getMap('references').set('bad', { version: 9 }); assert.throws(() => refs.validateDocumentReferences(value), /references/i); } finally { value.destroy(); }
});
test('the actual session emits one atomic update for text and reference choice, then shifts it through plain edits', async () => {
  let token = 'owner'; const metadata = { id: 'doc', channel_id: 'room', archived: false, version: 1, update_count: 0 };
  const api = { getAccessToken: () => token, getCurrentUserId: () => 'actor', request: async (_path, options) => options ? metadata : { document: metadata, items: [], has_more: false } };
  const model = load('shared-documents-api', { './api': { api, APIError: class extends Error {} }, './secure-client': { getSecureMessaging: async () => ({ handles: async () => false }) } });
  const session = new model.SharedDocumentSession('room', metadata); try { await session.sync(); let updates = 0; session.doc.on('update', () => updates++); session.replace('body', 'Ask @Eva and @Maja.', [eva]); assert.equal(updates, 1); assert.deepEqual(plain(session.state().references), [eva]); session.replace('body', 'Earlier Ask @Eva and @Maja.'); assert.deepEqual(plain(session.state().references), [{ ...eva, start: 12, end: 16 }]); token = 'replacement'; assert.throws(() => session.replace('body', 'Do not transfer'), /account changed/); } finally { session.stop(); }
});

function scheduledSessionFixture() {
  const timers = new Map(), events = new Map(), socketEvents = new Map(), requests = [];
  let timerId = 0, token = 'credential', actor = 'actor';
  const metadata = { id: 'doc', channel_id: 'room', archived: false, version: 1, update_count: 0 }, rows = [];
  const on = (target, name, callback) => { if (!target.has(name)) target.set(name, new Set()); target.get(name).add(callback); };
  const socket = { on: (name, fn) => on(socketEvents, name, fn), off: (name, fn) => socketEvents.get(name)?.delete(fn) };
  const window = { addEventListener: (name, fn) => on(events, name, fn), removeEventListener: (name, fn) => events.get(name)?.delete(fn) };
  const append = (id, update_base64) => rows.push({ id, document_id: 'doc', channel_id: 'room', sequence: rows.length + 1, content_mode: 'server', fields: { update_base64 } });
  const api = { getAccessToken: () => token, getCurrentUserId: () => actor, request: async (path, options) => {
    requests.push({ path, options });
    if (options?.method === 'POST') { const value = JSON.parse(options.body); append(value.update_id, value.update_base64); return rows.at(-1); }
    const after = Number(new URL(path, 'https://local').searchParams.get('after_sequence'));
    return { document: { ...metadata, update_count: rows.length }, items: rows.filter(row => row.sequence > after), has_more: false };
  } };
  const model = load('shared-documents-api', { './api': { api, APIError: class extends Error {} }, './socket': { getSocket: () => socket }, './secure-client': { getSecureMessaging: async () => ({ handles: async () => false }) } }, {
    window, setTimeout: (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id),
  });
  const lease = () => model.acquireSharedDocumentSession('room', metadata);
  const delay = () => [...timers.values()].at(-1)?.delay;
  const fire = async session => { const [id, timer] = [...timers.entries()].at(-1); timers.delete(id); timer.fn(); await session.sync(); };
  const hint = payload => { for (const fn of socketEvents.get('shared_document_changed') || []) fn(payload); };
  return { model, lease, requests, timers, delay, fire, hint, append, events, socketEvents,
    changeActor() { actor = 'replacement'; token = 'new-credential'; for (const fn of events.get('freecollab:auth-changed') || []) fn(); } };
}

test('retained views share one actor/document writer; idle fallback, socket wake and last-view release stay live', async () => {
  const f = scheduledSessionFixture(), first = f.lease(), second = f.lease(), session = first.session;
  try {
    assert.equal(first.session, second.session);
    session.start(); second.session.start(); await session.sync();
    assert.equal(f.requests.length, 1, 'two surfaces issue one initial history request');
    for (let index = 0; index < 6; index++) await f.fire(session);
    assert.equal(f.delay(), 15000, 'quiet pages back off to a bounded live fallback');
    first.release(); session.replace('body', 'Keep @Eva.', [{ ...eva, start: 5, end: 9 }]);
    assert.equal(f.delay(), 250);
    await Promise.all([session.sync(), session.sync(), second.session.sync()]);
    assert.equal(f.requests.filter(row => row.options?.method === 'POST').length, 1, 'shared surfaces never duplicate the local write');
    assert.deepEqual(plain(session.state().references), [{ ...eva, start: 5, end: 9 }]);
    const peer = copy(session.doc);
    try { peer.getText('body').insert(0, '🌿 '); f.append('peer-change', f.model.encodeDocumentUpdate(Y.encodeStateAsUpdate(peer))); }
    finally { peer.destroy(); }
    f.hint({ channel_id: 'different', document_id: 'doc', part: 'updates' });
    assert.notEqual(f.delay(), 0, 'foreign hints never wake this page');
    f.hint({ channel_id: 'room', document_id: 'doc', part: 'updates' }); assert.equal(f.delay(), 0);
    await f.fire(session);
    assert.equal(session.state().body, '🌿 Keep @Eva.');
    assert.deepEqual(plain(session.state().references), [{ ...eva, start: 8, end: 12 }]);
    for (let index = 0; index < 6; index++) await f.fire(session);
    const latePeer = copy(session.doc);
    try { latePeer.getText('body').insert(0, 'Fallback '); f.append('missed-socket', f.model.encodeDocumentUpdate(Y.encodeStateAsUpdate(latePeer))); }
    finally { latePeer.destroy(); }
    await f.fire(session); assert.equal(session.state().body, 'Fallback 🌿 Keep @Eva.', 'polling recovers a missed realtime hint');
  } finally { first.release(); second.release(); }
  assert.equal(f.timers.size, 0);
  assert.equal(f.socketEvents.get('shared_document_changed').size, 0);
});

test('pooled identity changes stop every old timer and reject late writes without resurrecting a closed session', async () => {
  const f = scheduledSessionFixture(), old = f.lease(); old.session.start(); await old.session.sync();
  f.changeActor(); assert.equal(f.timers.size, 0);
  assert.throws(() => old.session.replace('body', 'Never transfer this draft'), /account changed|closed/);
  const replacement = f.lease(); assert.notEqual(replacement.session, old.session);
  try { replacement.session.start(); await replacement.session.sync(); old.release(); assert.ok(f.timers.size); }
  finally { replacement.release(); }
  assert.equal(f.timers.size, 0);
});

test('real sequential prefix edits keep whole emoji codepoints, UTF-16 reference anchors and reloaded body/title text', async () => {
  const f = scheduledSessionFixture(), lease = f.lease(), session = lease.session;
  try {
    await session.sync();
    const original = '🧭 Plan @Eva', chosen = { kind: 'person', id: 'eva-id', start: 8, end: 12 };
    session.replace('body', original, [chosen]);
    let prefix = '';
    for (const character of 'Peer prefix 🪵\n') {
      prefix += character;
      session.replace('body', prefix + original);
      assert.equal(session.state().body, prefix + original, 'a complete inserted codepoint must not split the existing compass');
      assert.deepEqual(plain(session.state().references), [{ ...chosen, start: chosen.start + prefix.length, end: chosen.end + prefix.length }]);
    }
    // These codepoints share a low surrogate, so a common suffix can also end
    // inside a pair. Title edits must widen that boundary as well.
    const before = String.fromCodePoint(0x1f600) + ' @Eva', after = String.fromCodePoint(0x1fa00) + ' @Eva';
    const titleRef = { ...chosen, start: 3, end: 7 };
    session.replace('title', before, [titleRef]); session.replace('title', after);
    assert.equal(session.state().title, after);
    assert.deepEqual(plain(session.state().titleReferences), [titleRef]);
    const reloaded = copy(session.doc);
    try {
      refs.validateDocumentReferences(reloaded);
      assert.equal(reloaded.getText('body').toString(), prefix + original);
      assert.equal(reloaded.getText('title').toString(), after);
      assert.deepEqual(read(reloaded), plain(session.state().references));
      assert.deepEqual(plain(refs.readDocumentReferences(reloaded, 'title')), [titleRef]);
    } finally { reloaded.destroy(); }
  } finally { lease.release(); }
});
