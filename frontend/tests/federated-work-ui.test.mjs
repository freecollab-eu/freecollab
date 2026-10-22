import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const task = { title: 'Verified private task', description: '', status: 'todo', priority: 'normal', assignee_matrix_id: null, due_date: null, checklist: [] };
const revision = { event_id: '$root', sender: '@fc_member:matrix.example.com', timestamp: 1, id: 'resource', kind: 'task', parents: [], archived: false, fields: task };
const record = { id: 'resource', kind: 'task', root_event_id: '$root', created_by: revision.sender, heads: [revision], selected: revision, history: [revision], conflicts: false, invalid: [] };
function fixture({ records = [], first } = {}) {
  const state = { token: 'current-session', error: null, calls: [], guards: [], stopped: false, scope: true };
  const slots = [], effects = [], listeners = new Map(), intervals = [];
  let cursor = 0, tree;
  const view = { context: { members: [{ matrix_user_id: revision.sender, name: 'Member' }] }, records, warnings: [], pending_pointers: 0, quarantined_event_ids: [] };
  class Client {
    async load() { if (state.error) { state.scope = false; throw state.error; } if (first) { const pending = first; first = null; return pending; } state.scope = true; return view; }
    hasCurrentScope() { return state.scope && !state.stopped; }
    stop() { state.stopped = true; state.scope = false; }
    async save(...args) { state.calls.push(args); return { ...record, kind: args[0], selected: { ...revision, fields: args[1] } }; }
  }
  const react = {
    useState(initial) { const id = cursor++; if (!(id in slots)) slots[id] = typeof initial === 'function' ? initial() : initial; return [slots[id], value => { slots[id] = typeof value === 'function' ? value(slots[id]) : value; }]; },
    useRef(initial) { const id = cursor++; return slots[id] ||= { current: initial }; },
    useEffect(callback, deps) { const id = cursor++, old = slots[id]; if (!old || deps.some((value, index) => value !== old.deps[index])) { slots[id] = { deps, cleanup: old?.cleanup }; effects.push(() => { slots[id].cleanup?.(); slots[id].cleanup = callback(); }); } },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const dependencies = { react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': new Proxy({}, { get: (_, name) => String(name) }),
    '../i18n': { t: (value, variables = {}) => value.replace(/\{(\w+)\}/g, (all, key) => variables[key] ?? all), useI18n() {}, formatDate: String },
    '../lib/api': { api: { getAccessToken: () => state.token } }, '../lib/federated-work-api': { FederatedWorkClient: Client },
    '../lib/calendar': { expandCalendarOccurrences: () => [] }, '../styles/federated-work.css': {} };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/FederatedWorkPanel.tsx', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText, { exports, Error, Date, Intl, crypto: { randomUUID: () => 'uuid' }, window: { confirm: () => true, setInterval: fn => { intervals.push(fn); return 1; }, clearInterval() {}, addEventListener: (key, fn) => listeners.set(key, [...(listeners.get(key) || []), fn]), removeEventListener() {} }, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; } });
  function render() { cursor = 0; tree = exports.FederatedWorkPanel({ channelId: 'local-channel', onPendingChange: value => state.guards.push(value) }); while (effects.length) effects.shift()(); }
  async function flush() { for (let n = 0; n < 16; n++) { await Promise.resolve(); render(); } }
  function nodes() { const rows = []; function visit(item) { if (!item || typeof item !== 'object') return; if (Array.isArray(item)) return item.forEach(visit); rows.push(item); visit(item.props?.children); } visit(tree); return rows; }
  function text(item) { return typeof item === 'string' ? item : Array.isArray(item) ? item.map(text).join('') : item && typeof item === 'object' ? text(item.props?.children) : ''; }
  function find(predicate) { const item = nodes().find(predicate); assert.ok(item, 'Control exists'); return item; }
  function button(name) { return find(item => item.type === 'button' && text(item) === name); }
  function click(name) { button(name).props.onClick(); render(); }
  function field(id, value) { find(item => item.props.id === id).props.onChange({ target: { value } }); render(); }
  function submit() { find(item => item.type === 'form').props.onSubmit({ preventDefault() {} }); render(); }
  render(); return { state, view, render, flush, nodes, text: (item = tree) => text(item), find, button, click, field, submit, poll: () => intervals[0](), authChanged() { for (const listener of listeners.get('freecollab:auth-changed') || []) listener(); render(); } };
}

test('shared editor publishes through the encrypted client and reports pending changes to navigation', async () => {
  const f = fixture(); await f.flush(); f.click('New shared task'); f.field('shared-work-title', 'A deliberate shared edit');
  assert.equal(f.state.guards.at(-1), true); f.submit(); await f.flush();
  assert.equal(f.state.calls.length, 1); assert.equal(f.state.calls[0][0], 'task'); assert.equal(f.state.calls[0][1].title, 'A deliberate shared edit'); assert.equal(f.state.calls[0][1].assignee_matrix_id, null);
  assert.equal(f.state.guards.at(-1), false); assert.match(f.text(), /Encrypted shared changes saved/);
});

test('late decrypted load cannot repopulate the UI after an account switch', async () => {
  let resolve; const first = new Promise(done => { resolve = done; }); const f = fixture({ first });
  f.state.token = 'different-session'; f.authChanged(); resolve({ context: { members: [] }, records: [record], warnings: [], pending_pointers: 0, quarantined_event_ids: [] }); await f.flush();
  assert.equal(f.state.stopped, true); assert.doesNotMatch(f.text(), /Verified private task/); assert.equal(f.nodes().some(item => item.type === 'form'), false);
});

test('revocation during periodic refresh hides existing decrypted work and disables publication', async () => {
  const f = fixture({ records: [record] }); await f.flush(); assert.match(f.text(), /Verified private task/);
  f.state.error = new Error('Current participant access ended'); f.poll(); await f.flush();
  assert.doesNotMatch(f.text(), /Verified private task/); assert.equal(f.button('New shared task').props.disabled, true); assert.match(f.text(), /Current participant access ended/);
});

test('competing revisions need an explicit reviewed set of parent event IDs', async () => {
  const peer = { ...revision, event_id: '$peer', sender: '@fc_peer:matrix.partner.example.com' };
  const f = fixture({ records: [{ ...record, conflicts: true, heads: [revision, peer] }] }); await f.flush();
  f.find(item => item.props.className === 'fc-fw-record').props.onClick(); f.render();
  assert.equal(f.button('Save reviewed merge').props.disabled, true);
  const label = f.find(item => item.type === 'label' && f.text(item).includes('I reviewed the selected versions'));
  label.props.children[0].props.onChange({ target: { checked: true } }); f.render();
  assert.equal(f.button('Save reviewed merge').props.disabled, false); f.submit(); await f.flush();
  assert.deepEqual(Array.from(f.state.calls[0][3]), ['$root', '$peer']);
});

test('an incomplete known resource is visible and read-only while new healthy work remains available', async () => {
  const f = fixture({ records: [{ ...record, invalid: ['$missing'] }] }); await f.flush(); assert.match(f.text(), /Incomplete history/);
  f.find(item => item.props.className === 'fc-fw-record').props.onClick(); f.render();
  assert.equal(f.find(item => item.props.id === 'shared-work-title').props.disabled, true); assert.equal(f.button('Save encrypted changes').props.disabled, true);
  assert.equal(f.button('New shared task').props.disabled, false);
});
