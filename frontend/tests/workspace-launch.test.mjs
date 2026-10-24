import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function moduleFor(file, dependencies = {}, globals = {}) {
  const exports = {}, compiled = ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(compiled, { exports, URL, AbortController, ...globals, require: name => {
    if (!(name in dependencies)) throw new Error('Unexpected dependency: ' + name);
    return dependencies[name];
  } }); return exports;
}
const helpers = moduleFor('lib/workspace-launch.ts');
const appearance = moduleFor('lib/appearance.ts');
const swedish = { ...moduleFor('i18n/workspace-launch.sv.ts').default, ...moduleFor('i18n/workspace-first-conversation.sv.ts').default };
const workspace = { id: 'own-workspace', name: 'Our studio', logo_url: null, role: 'owner', is_legacy: false, member_count: 1 };
const me = { id: 'native-admin', account_type: 'personal', session_scope: 'account' };
const room = { id: 'shared-room', workspace_id: workspace.id, name: 'Our room', type: 'group', secure: false, encrypted_channel_key: null, role: 'admin' };
const invitation = () => ({ id: 'issued-invitation', workspace_id: workspace.id, role: 'member', token: 'own-private-single-use-token-12345', expires_at: new Date(Date.now() + 86400000).toISOString() });
const snapshot = (channelId = null) => ({ workspace_id: workspace.id, channel_id: channelId, recipes: [] });

test('only native current workspace admins can launch; established and encrypted workspaces do not get an unnecessary suggestion', () => {
  assert.equal(helpers.canLaunchWorkspace(me, workspace), true);
  for (const user of [{ ...me, account_type: 'managed' }, { ...me, session_scope: 'workspace' }, null]) assert.equal(helpers.canLaunchWorkspace(user, workspace), false);
  assert.equal(helpers.canLaunchWorkspace(me, { ...workspace, role: 'member' }), false);
  assert.equal(helpers.shouldSuggestWorkspaceLaunch(workspace, []), true);
  for (const existing of [{ ...workspace, is_legacy: true }, { ...workspace, member_count: 2 }]) assert.equal(helpers.shouldSuggestWorkspaceLaunch(existing, []), false);
  assert.equal(helpers.shouldSuggestWorkspaceLaunch(workspace, [{ ...room, secure: true }]), false);
  assert.equal(helpers.nativeLaunchChannels(workspace.id, [room, { ...room, id: 'foreign', workspace_id: 'foreign' }, { ...room, id: 'encrypted', secure: true }, { ...room, id: 'bound', matrix_room_id: '!opaque:matrix' }, { ...room, id: 'dm', type: 'direct' }]).length, 1);
});

test('single-use invitation URLs bind workspace and role, keep the origin, and omit the bearer from retained metadata', () => {
  const result = helpers.prepareLaunchInvite(invitation(), workspace.id, 'member', 'https://collab.example.org', '//a-different.example/path');
  const url = new URL(result.url);
  assert.equal(url.origin, 'https://collab.example.org'); assert.equal(url.pathname, '//a-different.example/path');
  assert.equal(url.search, ''); assert.match(url.hash, /^#workspace-invite=/);
  assert.equal(Object.hasOwn(result.metadata, 'token'), false);
  for (const changes of [{ workspace_id: 'foreign' }, { role: 'admin' }, { token: 'bad' }, { token: 'x'.repeat(513) }, { accepted_at: new Date().toISOString() }, { expires_at: 'invalid' }, { expires_at: new Date(0).toISOString() }]) assert.throws(() => helpers.prepareLaunchInvite({ ...invitation(), ...changes }, workspace.id, 'member', 'https://collab.example.org', '/'));
  for (const origin of ['javascript:alert(1)', 'https://user:secret@collab.example.org', 'https://collab.example.org?session=private']) assert.throws(() => helpers.prepareLaunchInvite(invitation(), workspace.id, 'member', origin, '/'));
});

test('live invitation and connection summaries stay in their selected scope and distinguish readiness from drafts', () => {
  assert.equal(helpers.activeLaunchInvites(workspace.id, [invitation(), { ...invitation(), workspace_id: 'foreign' }, { ...invitation(), revoked_at: 'now' }, { ...invitation(), accepted_at: 'now' }, { ...invitation(), expires_at: 'invalid' }]).length, 1);
  const status = helpers.summarizeLaunchConnections({ ...snapshot(room.id), recipes: [
    { enabled: true, status: 'ready' }, { enabled: true, status: 'draft' }, { enabled: true, status: 'authorization_required' }, { enabled: false, status: 'ready' }, { enabled: true, status: 'attention' },
  ] }, workspace.id, room.id);
  assert.equal(status.ready, 1); assert.equal(status.drafts, 2); assert.equal(status.attention, 1);
  assert.throws(() => helpers.summarizeLaunchConnections(snapshot('other'), workspace.id, room.id));
  assert.throws(() => helpers.summarizeLaunchConnections({ ...snapshot(room.id), workspace_id: 'foreign' }, workspace.id, room.id));
  assert.equal(helpers.launchGuard(true, true, true), 'busy'); assert.equal(helpers.launchGuard(false, true, true), 'secret'); assert.equal(helpers.launchGuard(false, true, false), 'dirty');
});

async function fixture(options = {}) {
  const hooks = [], effects = [], listeners = new Map(); let cursor = 0, rerender = false;
  const state = { token: 'native-session', userId: me.id, user: { ...me, ...options.user }, workspace: { ...workspace }, rooms: [], invites: [], writes: [], copies: [], close: 0, selected: [], members: [], appearance: 0, connections: [], changed: 0, guards: [], channelRefresh: [], confirm: true, locale: 'en', ...options.state };
  const react = {
    useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = { value: typeof initial === 'function' ? initial() : initial }; return [hooks[index].value, next => { const value = typeof next === 'function' ? next(hooks[index].value) : next; if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; rerender = true; } }]; },
    useRef(value) { const index = cursor++; if (!(index in hooks)) hooks[index] = { current: value }; return hooks[index]; },
    useEffect(effect, dependencies) { const index = cursor++, previous = hooks[index]; if (!previous || dependencies.some((value, key) => !Object.is(value, previous.dependencies[key]))) { hooks[index] = { dependencies, cleanup: previous?.cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); }); } },
  };
  const props = { workspace: { ...workspace }, channels: [], onClose: () => state.close++, onChanged: () => state.changed++, onChannelsChanged: value => state.channelRefresh.push(value), onSelectChannel: value => state.selected.push(value), onOpenChannelMembers: value => state.members.push(value), onOpenAppearance: () => state.appearance++, onOpenConnections: value => state.connections.push(value), onGuardChange: value => state.guards.push(value) };
  const api = {
    getAccessToken: () => state.token, getCurrentUserId: () => state.userId,
    getMe: async () => { if (state.pauseMe) await state.pauseMe; return state.user; },
    getChannels: async () => [...state.rooms], request: async path => { assert.equal(path, '/preferences/appearance'); return appearance.DEFAULT_APPEARANCE; },
    createChannel: async (name, type, members, workspaceId) => { state.writes.push({ kind: 'channel', name, type, members, workspaceId }); const created = { ...room, name }; state.rooms.push(created); return created; },
  };
  const workspaceApi = {
    list: async () => [{ ...state.workspace }], invites: async () => [...state.invites],
    update: async (id, name) => { state.writes.push({ kind: 'name', id, name }); state.workspace.name = name; return { ...state.workspace }; },
    uploadLogo: async id => { state.writes.push({ kind: 'logo', id }); return { ...state.workspace, logo_url: '/safe/workspace/logo' }; },
    invite: async (id, role) => { state.writes.push({ kind: 'invite', id, role }); if (state.inviteFailure) throw new Error('Synthetic invitation timeout'); const next = { ...invitation(), role }; state.invites.push({ ...next, token: undefined }); return next; },
    revokeInvite: async (id, inviteId) => { state.writes.push({ kind: 'revoke', id, inviteId }); state.invites = state.invites.filter(item => item.id !== inviteId); },
  };
  const window = { addEventListener: (name, callback) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); }, removeEventListener: (name, callback) => listeners.get(name)?.delete(callback), confirm: () => state.confirm };
  const jsx = (type, properties) => ({ type, props: properties });
  const t = (text, values = {}) => (state.locale === 'sv' && Object.hasOwn(swedish, text) ? swedish[text] : text).replace(/\{(\w+)\}/g, (_, key) => values[key] ?? '{' + key + '}');
  const { WorkspaceLaunchPanel } = moduleFor('components/WorkspaceLaunchPanel.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' }, 'react-dom': { createPortal: value => value }, 'lucide-react': {},
    '../i18n': { useI18n: () => ({ t, locale: state.locale }) }, '../lib/api': { api }, '../lib/workspace-api': { workspaceApi },
    '../lib/appearance': appearance, '../lib/workspace-launch': helpers, '../lib/useModalFocus': { useModalFocus() {} }, './WorkspaceAvatar': {}, '../styles/workspace-launch.css': {},
    '../lib/connection-setup-api': { connectionSetupApi: { get: async (_workspace, channel) => snapshot(channel) } },
  }, { document: { body: {} }, window, location: { origin: 'https://collab.example.org', pathname: '/' }, navigator: { clipboard: { writeText: async value => state.copies.push(value) } } });
  function render() { for (let count = 0; count < 50; count++) { cursor = 0; rerender = false; state.tree = WorkspaceLaunchPanel(props); effects.splice(0).forEach(effect => effect()); if (!rerender) return; } throw new Error('Component did not settle'); }
  async function flush() { for (let count = 0; count < 30; count++) { await Promise.resolve(); if (rerender) render(); } }
  function nodes(node) { if (!node || typeof node !== 'object') return []; if (Array.isArray(node)) return node.flatMap(nodes); return [node, ...nodes(node.props?.children)]; }
  function find(predicate) { const found = nodes(state.tree).find(predicate); assert.ok(found, 'UI control missing'); return found; }
  function text(node) { if (typeof node === 'string' || typeof node === 'number') return String(node); if (Array.isArray(node)) return node.map(text).join(''); return node?.props ? text(node.props.children) : ''; }
  const byId = id => find(node => node.props?.id === id);
  const button = label => find(node => node.type === 'button' && (node.props['aria-label'] === label || text(node) === label));
  const form = label => find(node => node.type === 'form' && node.props['aria-label'] === label);
  const change = (id, value) => { byId(id).props.onChange({ target: { value } }); render(); };
  const click = async label => { const result = button(label).props.onClick(); render(); await result; await flush(); };
  const submit = async label => { const result = form(label).props.onSubmit({ preventDefault() {} }); render(); await result; await flush(); };
  const emit = name => { for (const callback of listeners.get(name) || []) callback({}); render(); };
  render(); await flush();
  return { state, props, byId, button, change, click, submit, render, flush, emit, text: () => text(state.tree), unmount: () => hooks.forEach(hook => hook.cleanup?.()) };
}

test('the actual guide reads live state without writes, and only explicit channel creation starts a real conversation location', async () => {
  const f = await fixture(); assert.equal(f.state.writes.length, 0); assert.equal(f.state.guards.at(-1), null);
  f.change('launch-channel-name', 'Real team discussion'); await f.submit('Create a shared channel');
  assert.equal(f.state.writes.length, 1); assert.equal(f.state.writes[0].kind, 'channel');
  assert.equal(f.state.writes[0].workspaceId, workspace.id); assert.equal(f.state.channelRefresh.length, 1);
  assert.equal(f.state.selected.length, 0); assert.equal(f.byId('launch-channel-name').props.value, '');
  await f.click('Write the first message'); assert.equal(f.state.selected[0].id, room.id);
});

test('issued links require explicit copying and acknowledgement or revocation before close or navigation', async () => {
  const f = await fixture(); await f.click('Create single-use invitation');
  assert.equal(f.state.writes[0].kind, 'invite'); assert.equal(f.state.guards.at(-1), 'secret');
  assert.match(f.byId('launch-invitation-link').props.value, /^https:\/\/collab\.example\.org\/#workspace-invite=/);
  await f.click('Close workspace launch guide'); await f.click('Language & appearance');
  assert.equal(f.state.close, 0); assert.equal(f.state.appearance, 0); assert.equal(f.state.copies.length, 0);
  await f.click('Copy invitation'); assert.equal(f.state.copies.length, 1); assert.equal(f.state.guards.at(-1), 'secret');
  await f.click('I have saved this invitation privately'); assert.equal(f.state.guards.at(-1), null);
  assert.ok(!f.text().includes(invitation().token)); await f.click('Language & appearance'); assert.equal(f.state.appearance, 1);
  await f.click('Close workspace launch guide'); assert.equal(f.state.close, 1);
});

test('channel access is a separate explicit action bound to the selected channel, never an invitation or membership write', async () => {
  const f = await fixture({ state: { rooms: [room] } });
  assert.ok(f.text().includes('Workspace membership does not grant channel access.'));
  await f.click('Open channel invitations');
  assert.equal(f.state.members.length, 1); assert.equal(f.state.members[0].id, room.id);
  assert.equal(f.state.members[0].workspace_id, workspace.id);
  assert.equal(f.state.writes.length, 0); assert.equal(f.state.selected.length, 0);
  const member = await fixture({ state: { rooms: [{ ...room, role: 'member' }] } });
  assert.ok(member.text().includes('Ask one of its channel admins'));
  assert.ok(!member.text().includes('Open channel invitations'));
  await member.click('View channel members'); assert.equal(member.state.members[0].id, room.id);
  const empty = await fixture({ state: { rooms: [{ ...room, workspace_id: 'foreign' }, { ...room, secure: true }] } });
  assert.ok(!empty.text().includes('Open channel invitations')); assert.ok(!empty.text().includes('Write the first message'));
  assert.equal(empty.state.members.length, 0); assert.equal(empty.state.writes.length, 0);
});

test('channel invitations and first-message navigation preserve issued-link, draft and live role guards', async () => {
  const f = await fixture({ state: { rooms: [room] } }); await f.click('Create single-use invitation');
  await f.click('Open channel invitations'); await f.click('Write the first message');
  assert.equal(f.state.members.length, 0); assert.equal(f.state.selected.length, 0);
  assert.equal(f.state.guards.at(-1), 'secret');
  await f.click('I have saved this invitation privately');
  f.change('launch-channel-name', 'Keep this draft'); f.state.confirm = false;
  await f.click('Open channel invitations'); assert.equal(f.state.members.length, 0);
  assert.equal(f.byId('launch-channel-name').props.value, 'Keep this draft');
  f.state.confirm = true; await f.click('Open channel invitations');
  assert.equal(f.state.members.length, 1); assert.equal(f.byId('launch-channel-name').props.value, '');
  f.props.workspace.role = 'member'; f.render();
  assert.ok(!f.text().includes('Open channel invitations'));
});

test('revocation consumes the current guarded invitation and does not create another link automatically', async () => {
  const f = await fixture(); await f.click('Create single-use invitation'); await f.click('Revoke this invitation');
  assert.deepEqual(f.state.writes.map(write => write.kind), ['invite', 'revoke']); assert.equal(f.state.guards.at(-1), null);
  assert.ok(!f.text().includes(invitation().token));
});

test('unsaved names survive declined navigation and return refresh; accepted saves call the real workspace API', async () => {
  const f = await fixture(); f.change('launch-workspace-name', 'Our changed studio'); f.state.confirm = false;
  await f.click('Close workspace launch guide'); f.emit('freecollab:launch-refresh'); await f.flush();
  assert.equal(f.state.close, 0); assert.equal(f.byId('launch-workspace-name').props.value, 'Our changed studio');
  await f.submit('Workspace identity'); assert.equal(f.state.writes[0].kind, 'name'); assert.equal(f.state.changed, 1);
  assert.equal(f.state.guards.at(-1), null);
});

test('current role is rechecked before writes, and managed/scoped accounts never receive the launch controls', async () => {
  const f = await fixture(); f.change('launch-channel-name', 'Must not be created'); f.state.workspace.role = 'member';
  await f.submit('Create a shared channel'); assert.equal(f.state.writes.length, 0);
  for (const user of [{ session_scope: 'workspace' }, { account_type: 'managed' }]) {
    const denied = await fixture({ user }); assert.ok(denied.text().includes('Only a current workspace administrator'));
    assert.equal(denied.state.writes.length, 0); assert.ok(!denied.text().includes('Create single-use invitation'));
  }
});

test('a token change while rechecking an action prevents a write using the new account', async () => {
  const f = await fixture(); f.change('launch-channel-name', 'Must not escape');
  let release; f.state.pauseMe = new Promise(resolve => { release = resolve; });
  const operation = f.submit('Create a shared channel'); await Promise.resolve(); f.state.token = 'new-account-session'; release(); await operation;
  assert.equal(f.state.writes.length, 0);
});

test('invitation timeouts block blind duplication until an explicit live refresh', async () => {
  const f = await fixture({ state: { inviteFailure: true } }); await f.click('Create single-use invitation');
  assert.equal(f.state.writes.length, 1); assert.equal(f.button('Create single-use invitation').props.disabled, true);
  f.state.inviteFailure = false; await f.click('Refresh workspace launch state'); await f.flush();
  assert.equal(f.button('Create single-use invitation').props.disabled, false); assert.equal(f.state.writes.length, 1);
});

test('auth changes scrub a displayed invitation and cleanup resets the parent guard', async () => {
  const f = await fixture(); await f.click('Create single-use invitation');
  f.state.token = 'new-session'; f.emit('freecollab:auth-changed'); await f.flush();
  assert.ok(!f.text().includes(invitation().token)); assert.ok(!f.text().includes('Single-use invitation link'));
  f.unmount(); assert.equal(f.state.guards.at(-1), null);
});

test('losing admin access while holding an issued link scrubs it and still permits closing the guide', async () => {
  const f = await fixture(); await f.click('Create single-use invitation');
  f.state.workspace.role = 'member'; await f.click('Copy invitation');
  assert.equal(f.state.copies.length, 0); assert.ok(!f.text().includes('Single-use invitation link'));
  assert.equal(f.state.guards.at(-1), null); await f.click('Close workspace launch guide'); assert.equal(f.state.close, 1);
});

test('saved success notices follow language changes in both directions while the guide remains open', async () => {
  const f = await fixture(); await f.click('Create single-use invitation'); await f.click('Revoke this invitation');
  assert.ok(f.text().includes('Invitation revoked.')); f.state.locale = 'sv'; f.render();
  assert.ok(f.text().includes('Inbjudan har återkallats.')); assert.ok(!f.text().includes('Invitation revoked.'));
  f.state.locale = 'en'; f.render(); assert.ok(f.text().includes('Invitation revoked.'));
});
