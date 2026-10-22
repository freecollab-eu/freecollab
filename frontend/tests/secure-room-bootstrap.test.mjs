import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const transactionRetry = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/secure/transaction-retry.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports: transactionRetry });

function fixture({ joined = false, allowed = true, status = 'active', federated = false, unexpected = false } = {}) {
  const calls = [], callbacks = new Map();
  let invited = false, onTransaction = () => {}, onReceipt = () => {}, encryptedFetch;
  const room = { getMyMembership: () => joined ? 'join' : 'invite', currentState: { setStateEvents() {} }, getLiveTimeline: () => ({ getEvents: () => [] }),
    getEventForTxnId: () => undefined,
    getJoinedMembers: () => [{ userId: '@fc_partner:private.test' }, ...(unexpected ? [{ userId: '@fc_unapproved:remote.test' }] : [])] };
  const crypto = { globalBlacklistUnverifiedDevices: false, forceDiscardSession: async () => calls.push('discard') };
  const client = {
    stopClient() {},
    on(event, callback) { callbacks.set(event, [...(callbacks.get(event) || []), callback]); },
    initRustCrypto: async () => {}, getCrypto: () => crypto,
    startClient: async () => { for (const callback of callbacks.get('sync') || []) callback('PREPARED'); },
    getRoom: () => joined ? room : null,
    getUserId: () => '@fc_partner:private.test',
    makeTxnId: () => { onTransaction(); return 'fixture-transaction'; },
    sendEvent: async () => { calls.push('encrypted-send'); onReceipt(); return { event_id: '$fixture' }; },
    isRoomEncrypted: () => true,
    roomState: async () => [],
    async joinRoom() { calls.push('join'); if (!invited) throw new Error('No current Matrix invitation'); joined = true; return room; },
  };
  const source = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  const dependencies = {
    'matrix-js-sdk': { createClient: options => { encryptedFetch = options.fetchFn; return client; }, ClientEvent: { Sync: 'sync' }, RoomEvent: { Timeline: 'timeline', Redaction: 'redaction' }, MatrixEventEvent: { Decrypted: 'decrypted' } },
    'matrix-js-sdk/lib/crypto-api/CryptoEvent': { CryptoEvent: { KeyBackupSessionsRemaining: 'backup' } },
    'matrix-js-sdk/lib/crypto-api/recovery-key': {},
    './device-store': { storageKeyFor: async () => new Uint8Array(32), loadPendingNotices: async () => [] },
    '../api': { APIError: class extends Error {} },
    './transaction-retry': transactionRetry,
  };
  vm.runInNewContext(compiled, { exports, URL, Headers, Uint8Array, setTimeout, clearTimeout,
    location: { origin: 'https://trusted.example.test' },
    require: name => { assert.ok(name in dependencies, name); return dependencies[name]; },
    fetch: async (url, options) => {
      const path = new URL(url, 'https://trusted.example.test').pathname;
      calls.push(path);
      if (path === '/api/secure/session') return { ok: true, json: async () => ({ user_id: '@fc_partner:private.test', access_token: 'fixture-matrix-token', device_id: 'BROWSER' }) };
      if (path === '/api/secure/channels') return { ok: true, json: async () => [{ channel_id: 'shared', room_id: '!room:private.test', status }] };
      if (path === '/api/channels/shared/members') return { ok: true, json: async () => [{ user_id: 'partner', name: 'Partner' }] };
      assert.equal(path, '/api/secure/channels/shared/sync');
      assert.equal(options.method, 'POST');
      if (!allowed) return { ok: false, status: 403, json: async () => ({ detail: 'Current channel membership required' }) };
      invited = true;
      return { ok: true, json: async () => ({ membership_epoch: 'current-epoch', revoked_devices: [],
        ...(federated ? { federated_operators: ['@control:private.test', '@control:remote.test'] } : {}) }) };
    },
  });
  exports.configureSecureMessaging({ baseUrl: '/api', getAccessToken: () => 'fixture-scoped-session', getUserId: () => 'partner' });
  return { calls, stop: () => exports.secureMessaging.stop(), beforeSend: action => { onTransaction = action; }, beforeReceipt: action => { onReceipt = action; }, encryptedWire: () => encryptedFetch('https://trusted.example.test/api/secure/matrix/_matrix/client/v3/rooms/!room%3Aprivate.test/send/m.room.encrypted/fixture-transaction', { method: 'PUT' }), open: writing => exports.secureMessaging.roomFor('shared', writing),
    send: () => exports.secureMessaging.sendEvent('shared', 'eu.freecollab.channel.metadata', { name: 'Private title' }) };
}

test('first encrypted read synchronizes the current product seat before joining Matrix', async () => {
  const f = fixture();
  const result = await f.open(false);
  assert.equal(result.roomId, '!room:private.test');
  assert.ok(f.calls.indexOf('/api/secure/channels/shared/sync') < f.calls.indexOf('join'));
});

test('already joined encrypted reads do not provision or repeat membership synchronization', async () => {
  const f = fixture({ joined: true }); await f.open(false);
  assert.equal(f.calls.includes('/api/secure/channels/shared/sync'), false);
  assert.equal(f.calls.includes('join'), false);
});

test('a revoked seat cannot join when authenticated synchronization rejects it', async () => {
  const f = fixture({ allowed: false });
  await assert.rejects(f.open(false), /Current channel membership required/);
  assert.equal(f.calls.includes('join'), false);
});

test('encrypted writes retain current epoch synchronization and session rotation', async () => {
  const f = fixture({ joined: true });
  assert.equal((await f.open(true)).membershipEpoch, 'current-epoch');
  assert.ok(f.calls.includes('/api/secure/channels/shared/sync'));
  assert.ok(f.calls.includes('discard'));
});

test('federated encryption rejects an unexpected joined device before distributing even metadata keys', async () => {
  const f = fixture({ joined: true, federated: true, unexpected: true });
  await assert.rejects(f.send(), /Unexpected encrypted room occupants/);
});

test('logout clears per-client epoch state and delayed send cannot recreate it', async () => { const f=fixture({joined:true}); await f.open(true); f.beforeSend(()=>f.stop()); await assert.rejects(f.send(), /encryption session changed/); assert.equal(f.calls.includes('encrypted-send'),false); assert.doesNotThrow(()=>f.stop()); });

test('a stopped client cannot write encrypted wire events after logout', async () => { const f=fixture({joined:true}); await f.open(true); f.stop(); assert.throws(()=>f.encryptedWire(), /encryption session changed/); });

test('first encrypted send initializes its session without a false generation rejection', async () => { const f=fixture({joined:true}); assert.equal((await f.send()).event_id,'$fixture'); assert.equal(f.calls.includes('encrypted-send'),true); });

test('logout before receipt resolution prevents returning stale successful send results', async () => { const f=fixture({joined:true}); f.beforeReceipt(()=>f.stop()); await assert.rejects(f.send(), /encryption session changed/); });
