import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createClient, SDKError } from '../dist/index.js';

function fixture(options = {}) {
  const requests = []; let token = 'alpha-token';
  const fetch = async (url, init) => {
    const path = new URL(url).pathname, auth = new Headers(init.headers).get('Authorization'); requests.push({ path, init, auth });
    if (options.fetch) { const handled = await options.fetch(url, init); if (handled) return handled; }
    const who = auth?.replace('Bearer ', '');
    if (!['alpha-token', 'beta-token', 'outsider-token'].includes(who)) return Response.json({}, { status: 401 });
    if (path === '/api/auth/me') return Response.json({ id: who, session_scope: 'workspace', workspace_id: options.principalWorkspace || 'team' });
    if (path === '/api/channels/shared') return who === 'outsider-token' ? Response.json({}, { status: 403 }) : Response.json({ id: 'shared', workspace_id: options.channelWorkspace || 'team', secure: !!options.secure });
    if (path === '/api/messages/channels/shared') return Response.json(init.method === 'POST' ? { id: 'sent' } : [{ id: who, channel_id: 'shared', content: who, created_at: '2026-11-01' }]);
    if (path === '/api/channels/shared/activity-feed') return Response.json({ items: [{ id: 'post', channel_id: 'shared', content_mode: 'server', fields: { title: 'Post', body: 'Text' } }] });
    if (path === '/api/files/channels/shared') return Response.json([{ id: 'file', channel_id: 'shared', filename: 'note.txt', size: 3 }]);
    if (path === '/api/files/file') return new Response('abc');
    if (path === '/api/notifications') return Response.json([{ id: who + '-notification', channel_id: options.notificationChannel === undefined ? 'shared' : options.notificationChannel, title: who, read: false }]);
    if (path.endsWith('/read')) return Response.json({ marked_read: true });
    throw new Error('Unexpected SDK request: ' + path);
  };
  const client = createClient({ apiBase: 'https://collab.example/api', workspaceId: 'team', channelId: 'shared', tokenFactory: options.tokenFactory || (() => token), fetch });
  return { client, requests, setToken(value) { token = value; } };
}

test('modular native reads share one authenticated, scoped preflight; no cookie credentials', async () => {
  let calls = 0; const { client, requests } = fixture({ tokenFactory: async () => { calls++; await Promise.resolve(); return 'alpha-token'; } });
  const rows = await Promise.all([client.listMessages(), client.listPosts(), client.listFiles(), client.listNotifications()]);
  assert.equal(calls, 1); assert.equal(requests.filter(r => r.path === '/api/auth/me').length, 1);
  assert.equal(requests.filter(r => r.path === '/api/channels/shared').length, 1); assert.equal(rows.length, 4);
  for (const { init, auth } of requests) { assert.equal(init.credentials, 'omit'); assert.equal(init.cache, 'no-store'); assert.equal(init.redirect, 'error'); assert.equal(auth, 'Bearer alpha-token'); }
  client.destroy();
});

test('host reauthentication clears old user resources synchronously and binds fresh user', async () => {
  const { client, setToken, requests } = fixture(); const notifications = await client.listNotifications();
  setToken('beta-token'); const reauth = client.reauthenticate(); assert.equal(client.state.status, 'authenticating');
  await assert.rejects(client.markNotificationRead(notifications[0].id), error => error.code === 'scope-mismatch');
  await reauth; assert.equal(client.state.userId, 'beta-token'); const next = await client.listNotifications(); assert.equal(next[0].id, 'beta-token-notification');
  assert.equal(requests.filter(r => r.path.includes('alpha-token-notification/read')).length, 0); client.destroy();
});

test('an account change discovered during submit cannot send an old draft as the new user', async () => {
  const { client, setToken, requests } = fixture(); await client.listMessages(); setToken('beta-token');
  await assert.rejects(client.sendMessage('Private old draft'), error => error.name === 'AbortError');
  assert.equal(requests.filter(r => r.init.method === 'POST').length, 0);
  await client.sendMessage('New user chose to send'); assert.equal(requests.at(-1).auth, 'Bearer beta-token'); client.destroy();
});

test('logout produces no native content request and destroys usable file/notification grants', async () => {
  const { client, requests, setToken } = fixture(); await client.listFiles(); const before = requests.length;
  setToken(null); await assert.rejects(client.reauthenticate(), error => error.code === 'unauthenticated');
  assert.equal(client.state.status, 'signed-out'); await assert.rejects(client.downloadFile('file'), error => error.code === 'scope-mismatch');
  assert.equal(requests.length, before); client.destroy();
});

test('outsiders and workspace mismatches fail before a content endpoint', async () => {
  for (const options of [{ tokenFactory: () => 'outsider-token' }, { principalWorkspace: 'other' }, { channelWorkspace: 'other' }]) {
    const { client, requests } = fixture(options); await assert.rejects(client.listMessages(), SDKError);
    assert.equal(client.state.status, 'denied'); assert.equal(requests.some(r => r.path.includes('/messages/')), false); client.destroy();
  }
});

test('secure channels fail closed without calling any plaintext content endpoint', async () => {
  const { client, requests } = fixture({ secure: true }); await assert.rejects(client.listMessages(), error => error.code === 'secure-channel-unsupported');
  assert.equal(requests.some(r => /messages|files|activity-feed|notifications/.test(r.path)), false); client.destroy();
});

test('notifications require server-proven channel ownership, including incompatible old servers', async () => {
  for (const notificationChannel of ['other', null]) {
    const { client } = fixture({ notificationChannel }); let events = 0; client.on('notification', () => events++);
    await assert.rejects(client.listNotifications(), error => error.code === 'scope-mismatch'); assert.equal(events, 0); client.destroy();
  }
});

test('only listed files and notifications can be used and unsafe navigation URLs are never followed', async () => {
  const { client, requests } = fixture(); await client.listFiles();
  await assert.rejects(client.downloadFile('outside'), error => error.code === 'scope-mismatch'); assert.equal(await (await client.downloadFile('file')).blob.text(), 'abc');
  const [notification] = await client.listNotifications(); let event; client.on('navigate', value => { event = value; });
  client.navigate({ ...notification, entity_type: 'message', entity_id: 'message-id', action_url: 'javascript:alert(1)' });
  assert.equal(event.entityId, 'message-id'); assert.equal('action_url' in event, false);
  assert.equal(requests.some(r => r.path.includes('outside')), false); client.destroy();
});

test('scope change aborts outstanding authentication and never upgrades it into another workspace', async () => {
  let complete; const first = new Promise(resolve => { complete = resolve; }); let calls = 0;
  const { client, requests } = fixture({ tokenFactory: ({ signal }) => { calls++; if (calls === 1) { signal.addEventListener('abort', () => complete('alpha-token'), { once: true }); return first; } return 'alpha-token'; } });
  const old = client.listMessages(); const replacement = client.setScope({ workspaceId: 'other', channelId: 'shared' });
  await assert.rejects(old, error => error.name === 'AbortError'); await assert.rejects(replacement, error => error.code === 'scope-mismatch');
  assert.equal(requests.some(r => r.path.includes('/messages/')), false); client.destroy();
});

test('destroy aborts the body of an active fetch and permanently disables the client', async () => {
  let notify, activeSignal; const started = new Promise(resolve => { notify = resolve; });
  const { client } = fixture({ fetch: async (url, init) => {
    if (new URL(url).pathname !== '/api/messages/channels/shared') return;
    activeSignal = init.signal;
    return new Response(new ReadableStream({ start(controller) { notify(); init.signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')), { once: true }); } }));
  } });
  const pending = client.listMessages(); await started; client.destroy(); assert.equal(activeSignal.aborted, true);
  await assert.rejects(pending, error => error.name === 'AbortError'); await assert.rejects(client.listMessages(), error => error.code === 'destroyed');
});

test('API URL validation rejects credential-bearing URLs and non-HTTPS deployed origins', () => {
  for (const apiBase of ['http://collab.example/api', 'https://user:pass@collab.example', 'https://collab.example?key=secret', 'javascript:alert(1)']) {
    assert.throws(() => createClient({ apiBase, workspaceId: 'team', channelId: 'shared', tokenFactory: () => null }), SDKError);
  }
});

test('revocation detected by an authenticated download immediately invalidates every mounted resource', async () => {
  let revoked = false;
  const { client } = fixture({ fetch: async (url) => revoked && new URL(url).pathname === '/api/files/file' ? Response.json({}, { status: 401 }) : null });
  await client.listFiles(); await client.listNotifications(); revoked = true;
  await assert.rejects(client.downloadFile('file'), error => error.code === 'unauthenticated');
  assert.equal(client.state.status, 'signed-out'); await assert.rejects(client.markNotificationRead('alpha-token-notification'), error => error.code === 'scope-mismatch'); client.destroy();
});

test('published declaration entry points compile under NodeNext without installing a React peer', () => {
  const require = createRequire(import.meta.url); let compiler;
  try { compiler = require.resolve('typescript/lib/tsc.js'); }
  catch { compiler = fileURLToPath(new URL('../../../frontend/node_modules/typescript/lib/tsc.js', import.meta.url)); }
  execFileSync(process.execPath, [compiler, '--noEmit', '--strict', '--target', 'ES2020', '--module', 'NodeNext', '--moduleResolution', 'NodeNext', '--lib', 'ES2020,DOM,DOM.Iterable', fileURLToPath(new URL('./types-consumer.mts', import.meta.url))], { stdio: 'pipe' });
});

test('a channel becoming encrypted while mounted rejects the next native write before its content is sent', async () => {
  const options = { secure: false }, { client, requests } = fixture(options);
  await client.listMessages(); options.secure = true;
  await assert.rejects(client.sendMessage('This draft must not enter the native API'), error => error.code === 'secure-channel-unsupported');
  assert.equal(requests.some(request => request.init.method === 'POST'), false); assert.equal(client.state.status, 'denied'); client.destroy();
});

test('new native modules deny encrypted channels before every project/document/workflow/meeting route', async () => {
  for (const run of [c => c.listProjects(), c => c.listDocuments(), c => c.listCalendar(), c => c.listDecisions(), c => c.listMeetings(), c => c.listWorkflowDefinitions(), c => c.searchMessages('private')]) {
    const {client, requests} = fixture({secure: true}); await assert.rejects(run(client), error => error.code === 'secure-channel-unsupported');
    assert.deepEqual(requests.map(r => r.path), ['/api/auth/me', '/api/channels/shared']); client.destroy();
  }
});

test('nested task operations require a displayed project and are cleared synchronously on logout', async () => {
  const {client, requests, setToken} = fixture({fetch: async (url, init) => {
    const path = new URL(url).pathname;
    if (path === '/api/channels/shared/projects') return Response.json([{id:'project', channel_id:'shared', content_mode:'server', fields:{title:'Project'}}]);
    if (path === '/api/channels/shared/projects/project/tasks') return Response.json({items:[{id:'task', channel_id:'shared', project_id:'project', content_mode:'server', fields:{title:'Task'}}], next_offset:null});
  }});
  await assert.rejects(client.listTasks('outside'), error => error.code === 'scope-mismatch');
  await client.listProjects(); const [task] = await client.listTasks('project');
  assert.throws(() => client.saveTask('outside', task.fields, task), error => error.code === 'scope-mismatch');
  setToken(null); const pending = client.reauthenticate();
  await assert.rejects(client.listTasks('project'), error => error.code === 'scope-mismatch'); await assert.rejects(pending);
  assert.equal(requests.some(r => r.path.includes('outside')), false); client.destroy();
});

test('cross-channel and encrypted resource responses never become usable native grants', async () => {
  for (const record of [{id:'outside',channel_id:'elsewhere',content_mode:'server'}, {id:'outside',channel_id:'shared',content_mode:'matrix'}]) {
    const {client, requests} = fixture({fetch: async url => new URL(url).pathname === '/api/channels/shared/projects' ? Response.json([record]) : null});
    await assert.rejects(client.listProjects(), error => error.code === 'scope-mismatch');
    await assert.rejects(client.listTasks('outside'), error => error.code === 'scope-mismatch'); assert.equal(requests.some(r => r.path.endsWith('/tasks')), false); client.destroy();
  }
});

test('new typed native writes cannot submit a retained item after the host switches account', async () => {
  const {client, requests, setToken} = fixture({fetch: async url => new URL(url).pathname === '/api/channels/shared/decisions' ? Response.json([{id:'decision',channel_id:'shared',content_mode:'server',revision:1,title:'Old private decision'}]) : null});
  const [decision] = await client.listDecisions(); setToken('beta-token');
  await assert.rejects(client.saveDecision({title:'Old draft',content:'Must not be sent',status:'accepted',responsible_user_id:null,decision_date:null,source_message_id:null}, decision), error => error.name === 'AbortError');
  assert.equal(requests.some(r => r.init.method === 'POST'), false); client.destroy();
});

test('Yjs transport verifies each update binds the requested document and current channel', async () => {
  const {client, requests} = fixture({fetch: async url => {
    const path = new URL(url).pathname;
    if (path === '/api/channels/shared/shared-documents') return Response.json([{id:'document',channel_id:'shared',summary_event_id:null}]);
    if (path.endsWith('/document/updates')) return Response.json({document:{id:'document',channel_id:'shared'},items:[{id:'bad',document_id:'outside',channel_id:'shared',content_mode:'server'}],has_more:false});
  }});
  await client.listDocuments(); await assert.rejects(client.documentUpdates('document'), error => error.code === 'scope-mismatch');
  assert.throws(() => client.documentUpdates('outside'), error => error.code === 'scope-mismatch'); assert.equal(requests.some(r => r.path.includes('/outside/')), false); client.destroy();
});

test('meeting creation cannot return a prior account call after follow-up refresh changes identity', async () => {
  let token = 'alpha-token';
  const {client} = fixture({tokenFactory: () => token, fetch: async (url, init) => {
    if (new URL(url).pathname !== '/api/channels/shared/meetings') return;
    if (init.method === 'POST') {token = 'beta-token'; return Response.json({meeting:{id:'alpha-call',channel_id:'shared'}});}
    return Response.json([{id:'alpha-call',channel_id:'shared'}]);
  }});
  await client.listMeetings(); await assert.rejects(client.createMeeting(), error => error.name === 'AbortError'); assert.equal(client.state.userId, 'beta-token'); client.destroy();
});
