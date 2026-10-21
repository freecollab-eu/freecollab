import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { createHmac, randomBytes } from 'node:crypto';
import test from 'node:test';
import assert from 'node:assert/strict';

const code = await readFile(new URL('../../examples/n8n/verify-event.js', import.meta.url), 'utf8');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction('$input', '$env', 'require', code);
const require = createRequire(import.meta.url);
const secret = randomBytes(32).toString('base64url');
const event = { schema_version: 1, id: 'a'.repeat(32), type: 'message.created', occurred_at: new Date().toISOString(), data: { workspace_id: 'workspace', channel_id: 'channel', resource_id: 'message' } };

function fixture({ raw = Buffer.from(JSON.stringify(event)), stamp = Math.floor(Date.now() / 1000).toString(), override = {} } = {}) {
  const signature = 'v1=' + createHmac('sha256', secret).update(stamp + '.').update(raw).digest('hex');
  const headers = { 'x-freecollab-timestamp': stamp, 'x-freecollab-signature': signature, 'x-freecollab-event-id': event.id, 'x-freecollab-event': event.type, ...override };
  return { raw, input: { first: () => ({ json: { headers, body: { untrusted: 'never reserialize this' } } }) } };
}

async function verify(value, key = secret) {
  return run.call({ helpers: { getBinaryDataBuffer: async (index, property) => {
    assert.equal(index, 0); assert.equal(property, 'data'); return value.raw;
  } } }, value.input, { FREECOLLAB_WEBHOOK_SECRET: key }, require);
}

test('copied n8n verifier authenticates exact raw bytes before parsing, regardless of parsed body', async () => {
  const value = fixture({ raw: Buffer.from(JSON.stringify(event, null, 2) + '\n') });
  assert.deepEqual(await verify(value), [{ json: { event } }]);
});

test('copied n8n verifier rejects changed bytes and a different dedicated secret', async () => {
  const value = fixture();
  value.raw = Buffer.from(value.raw.toString().replace('message.created', 'channel.activity'));
  await assert.rejects(verify(value), /signature/);
  await assert.rejects(verify(fixture(), randomBytes(32).toString('hex')), /signature/);
});

test('copied n8n verifier rejects expired and future signed attempts', async () => {
  for (const offset of [-301, 301]) {
    const value = fixture({ stamp: String(Math.floor(Date.now() / 1000) + offset) });
    await assert.rejects(verify(value), /expired/);
  }
});

test('copied n8n verifier binds authenticated payload identity and type to routing headers', async () => {
  for (const override of [{ 'x-freecollab-event-id': 'b'.repeat(32) }, { 'x-freecollab-event': 'file.created' }]) {
    await assert.rejects(verify(fixture({ override })), /schema or routing/);
  }
});

test('copied n8n verifier requires private configuration and bounded original binary bytes', async () => {
  await assert.rejects(verify(fixture(), ''), /dedicated/);
  const missing = fixture(); missing.raw = undefined;
  await assert.rejects(verify(missing), /raw request body/);
  const large = fixture({ raw: Buffer.alloc(16385) });
  await assert.rejects(verify(large), /raw request body/);
});
