// Paste into a JavaScript Code node immediately after an n8n Webhook node
// configured with Raw Body enabled. See README.md before exposing a receiver.
const crypto = require('crypto');
const item = $input.first();
const headers = item.json.headers || {};
const secret = $env.FREECOLLAB_WEBHOOK_SECRET;
if (typeof secret !== 'string' || secret.length < 32) {
  throw new Error('Configure the dedicated FreeCollab webhook verification secret.');
}
const timestamp = headers['x-freecollab-timestamp'];
const signature = headers['x-freecollab-signature'];
const eventID = headers['x-freecollab-event-id'];
const eventType = headers['x-freecollab-event'];
if (typeof timestamp !== 'string' || !/^[0-9]{1,12}$/.test(timestamp)
    || Math.abs(Math.floor(Date.now() / 1000) - Number(timestamp)) > 300
    || typeof signature !== 'string' || !/^v1=[0-9a-f]{64}$/.test(signature)
    || typeof eventID !== 'string' || !/^[0-9a-f]{32}$/.test(eventID)
    || typeof eventType !== 'string') {
  throw new Error('Invalid or expired FreeCollab webhook.');
}
// This helper reads n8n's original binary data, including when n8n stores it
// externally. JSON.stringify(item.json.body) is never an acceptable substitute.
const raw = await this.helpers.getBinaryDataBuffer(0, 'data');
if (!Buffer.isBuffer(raw) || raw.length > 16384) {
  throw new Error('A bounded raw request body is required.');
}
const actual = Buffer.from(signature.slice(3), 'hex');
const expected = crypto.createHmac('sha256', secret)
  .update(timestamp, 'ascii').update('.').update(raw).digest();
if (!crypto.timingSafeEqual(actual, expected)) {
  throw new Error('Invalid FreeCollab webhook signature.');
}
const event = JSON.parse(raw.toString('utf8'));
const allowed = new Set(['message.created', 'file.created', 'file.version.created',
  'decision.created', 'decision.updated', 'channel.activity', 'webhook.test']);
if (event.schema_version !== 1 || event.id !== eventID || event.type !== eventType
    || !allowed.has(event.type) || typeof event.data?.workspace_id !== 'string') {
  throw new Error('Unexpected FreeCollab event schema or routing headers.');
}
return [{ json: { event } }];
