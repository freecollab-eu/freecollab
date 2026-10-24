import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { fieldReferences, resourceReferenceTargets } from './load-field-references.mjs';

function load(name, dependencies = {}) {
  const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => name === './field-references' ? fieldReferences : dependencies[name], TextEncoder, URL, URLSearchParams, Intl, Date, crypto: { randomUUID: () => 'new-draft' } });
  return exports;
}
const calendar = load('calendar'), documents = load('document-links');
const event = (overrides = {}) => ({ title: 'Planering, vår; nästa steg', description: 'First line\nSecond line \\ text', location: 'Studio',
  starts_at: '2026-10-25T00:30:00.000Z', ends_at: '2026-10-25T01:30:00.000Z', all_day: false,
  timezone: 'Europe/Stockholm', reminder_minutes: 15, external_uid: 'original@example.com', ...overrides });
const plain = value => JSON.parse(JSON.stringify(value));

test('ICS round-trip escapes text injection and folds UTF-8 lines without breaking characters', () => {
  const fields = event({ title: 'Möte '.repeat(35), description: 'Agenda\r\nATTENDEE:mailto:evil@example.com\nEND:VEVENT\nBEGIN:VEVENT' });
  const ics = calendar.exportCalendar([{ id: 'one', fields }], new Date('2026-10-20T10:00:00Z'));
  assert.ok(ics.split('\r\n').every(line => new TextEncoder().encode(line).length <= 75));
  assert.equal(ics.split('\r\n').filter(line => line === 'BEGIN:VEVENT').length, 1);
  const imported = calendar.importCalendar(ics);
  assert.equal(imported.length, 1);
  assert.equal(imported[0].title, fields.title.trim());
  assert.equal(imported[0].description, fields.description.replace(/\r\n/g, '\n'));
  assert.equal(imported[0].external_uid, fields.external_uid);
  assert.equal(imported[0].reminder_minutes, 15);
});

test('IANA wall times reject the spring gap and resolve autumn ambiguity to first occurrence', () => {
  assert.throws(() => calendar.zonedInstant('2026-03-29T02:30', 'Europe/Stockholm'), /clock changes/);
  assert.equal(calendar.zonedInstant('2026-10-25T02:30', 'Europe/Stockholm'), '2026-10-25T00:30:00.000Z');
  assert.equal(calendar.zonedInstant('2026-10-25T03:30', 'Europe/Stockholm'), '2026-10-25T02:30:00.000Z');
  assert.equal(calendar.calendarWallInput('2026-10-25T00:30:00Z', 'Europe/Stockholm'), '2026-10-25T02:30');
  assert.throws(() => calendar.zonedInstant('2026-02-30T09:00', 'UTC'), /Invalid calendar date/);
  assert.throws(() => calendar.validateCalendar(event({ starts_at: '2026-02-30T09:00:00Z', ends_at: '2026-03-03T09:00:00Z' })), /Timed events/);
});

test('TZID and UTC calendar imports resolve exact instants across DST', () => {
  const ics = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:dst@example.com\r\nDTSTART;TZID=Europe/Stockholm:20261025T023000\r\nDTEND:20261025T023000Z\r\nSUMMARY:Clock change\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n';
  const [fields] = calendar.importCalendar(ics);
  assert.equal(fields.starts_at, '2026-10-25T00:30:00.000Z');
  assert.equal(fields.ends_at, '2026-10-25T02:30:00.000Z');
  assert.equal(fields.timezone, 'Europe/Stockholm');
});

test('all-day dates retain an exclusive end and zero-minute display reminder through export/import', () => {
  const fields = event({ all_day: true, starts_at: '2026-10-25', ends_at: '2026-10-27', reminder_minutes: 0, external_uid: 'odd;uid,with\\slash@example.com' });
  const output = calendar.exportCalendar([{ id: 'day', fields }]);
  assert.match(output, /DTEND;VALUE=DATE:20261027\r\n/);
  assert.match(output, /TRIGGER:PT0M\r\n/);
  const [restored] = calendar.importCalendar(output);
  assert.equal(restored.starts_at, '2026-10-25'); assert.equal(restored.ends_at, '2026-10-27');
  assert.equal(restored.external_uid, fields.external_uid); assert.equal(restored.reminder_minutes, 0);
});

test('calendar import rejects unbounded recurrence and active alarms instead of silently changing behavior', () => {
  const base = calendar.exportCalendar([{ id: 'one', fields: event() }]);
  assert.throws(() => calendar.importCalendar(base.replace('SUMMARY:', 'RRULE:FREQ=DAILY\r\nSUMMARY:')), /with COUNT/);
  assert.throws(() => calendar.importCalendar(base.replace('ACTION:DISPLAY', 'ACTION:EMAIL')), /Only display reminders/);
  assert.throws(() => calendar.importCalendar(base.replace('TRIGGER:-PT15M', 'TRIGGER;RELATED=END:-PT15M')), /Only display reminders/);
  assert.throws(() => calendar.importCalendar(base.replace('TRIGGER:-PT15M', 'TRIGGER:-PT10081M')), /seven days/);
});

test('malformed or oversized ICS cannot create partial events', () => {
  const base = calendar.exportCalendar([{ id: 'one', fields: event() }]);
  assert.throws(() => calendar.importCalendar(base.replace('END:VCALENDAR', '')), /complete ICS/);
  assert.throws(() => calendar.importCalendar(base.replace('SUMMARY:', 'DTSTART:20261025T003000Z\r\nSUMMARY:')), /Duplicate calendar property/);
  assert.throws(() => calendar.importCalendar('x'.repeat(1024 * 1024 + 1)), /smaller than 1 MB/);
  assert.throws(() => calendar.validateCalendar(event({ external_uid: 'id\r\nATTENDEE:evil' })), /Invalid calendar UID/);
});

test('calendar compose links use fixed destinations and encode event details as parameters', () => {
  const fields = event({ title: 'Plan & invite?next=https://evil.example', description: 'Private details #1' });
  const google = new URL(calendar.calendarComposeUrl('google', fields));
  assert.equal(google.origin, 'https://calendar.google.com'); assert.equal(google.searchParams.get('text'), fields.title);
  assert.equal(google.searchParams.get('dates'), '20261025T003000Z/20261025T013000Z');
  const outlook = new URL(calendar.calendarComposeUrl('outlook', fields));
  assert.equal(outlook.origin, 'https://outlook.office.com'); assert.equal(outlook.searchParams.get('subject'), fields.title);
});

test('document providers accept real links and reject spoofed hosts and URL credentials', () => {
  for (const [provider, url] of [['nextcloud', 'https://cloud.example.com/s/Abc123'], ['google_drive', 'https://docs.google.com/document/d/abc/edit'], ['sharepoint', 'https://team.sharepoint.com/sites/studio/file.docx']])
    assert.equal(documents.validateDocument({ title: 'Source', notes: '', provider, url }).url, url);
  for (const url of ['https://docs.google.com.evil.example/document/d/abc', 'javascript:alert(1)', 'https://user:secret@docs.google.com/document/d/abc', 'https://docs.google.com/document/d/abc?access_token=secret', 'https://docs.google.com/document/d/abc#secret'])
    assert.throws(() => documents.validateDocument({ title: 'Source', notes: '', provider: 'google_drive', url }));
});

function apiFixture(options = {}) {
  let token = 'alice-token', wire;
  const resolutions = [];
  const fields = { title: 'Secret document', provider: 'nextcloud', url: 'https://cloud.example.com/s/secret', notes: 'Secret note' };
  const record = { id: 'new-draft', channel_id: 'room', kind: options.kind || 'documents', created_by: 'alice', revised_by: 'alice', revision: 1, deleted: false, content_mode: 'matrix', matrix_event_id: '$encrypted' };
  let payload;
  const secure = {
    async handles() { if (options.switchDuringHandles) token = 'bob-token'; return !options.native; },
    async sendEvent(channel, type, content) { payload = { channel, type, content }; return { event_id: '$encrypted' }; },
    async getDecryptedEvent() { return { getContent: () => ({ ...payload?.content, ...(options.contentOverride || {}) }), getRoomId: () => options.foreignRoom ? '!other' : '!room', getType: () => 'eu.freecollab.integration.' + (options.kind || 'documents'), getSender: () => options.foreignSender ? '@fc_alice:evil.example' : '@fc_alice:freecollab.test', isRedacted: () => false }; },
    async roomFor() { return { roomId: '!room', c: { getUserId: () => '@fc_alice:freecollab.test' } }; },
  };
  const api = { getAccessToken: () => token, async request(path, init) { wire = { path, body: JSON.parse(init.body) }; return options.native ? { ...record, content_mode: 'server', matrix_event_id: null, fields: wire.body.fields } : record; } };
  const { documentCalendarApi } = load('document-calendar-api', { './resource-reference-targets': resourceReferenceTargets(api, async (_, refs) => { resolutions.push(plain(refs)); return refs.map(ref => ({ ...ref, available: !options.unavailable })); }), './api': { api }, './secure-client': { getSecureMessaging: async () => secure }, './document-links': documents, './calendar': calendar });
  return { fields, api: documentCalendarApi, wire: () => wire, payload: () => payload, resolutions };
}

test('secure document API sends title and URL only inside the encrypted event, server receives an opaque reference', async () => {
  const f = apiFixture(); const saved = await f.api.saveDocument('room', f.fields);
  assert.deepEqual(plain(saved.fields), f.fields);
  assert.equal(f.payload().content.fields.title, f.fields.title);
  assert.deepEqual(plain(f.wire().body), { id: 'new-draft', expected_revision: 0, deleted: false, matrix_event_id: '$encrypted' });
  assert.ok(!JSON.stringify(f.wire()).includes(f.fields.title)); assert.ok(!JSON.stringify(f.wire()).includes(f.fields.url));
});

test('secure document resolution rejects foreign sender domains, room and revision binding', async () => {
  for (const options of [{ foreignSender: true }, { foreignRoom: true }, { contentOverride: { revision: 2 } }, { contentOverride: { id: 'another' } }]) {
    const f = apiFixture(options), result = await f.api.saveDocument('room', f.fields);
    assert.equal(result.fields, undefined); assert.match(result.resolution_error, /could not be verified/);
  }
});

test('an account switch during secure lookup prevents a document write under the new account', async () => {
  const f = apiFixture({ switchDuringHandles: true });
  await assert.rejects(f.api.saveDocument('room', f.fields), /account changed/);
  assert.equal(f.wire(), undefined); assert.equal(f.payload(), undefined);
});

test('bounded daily and monthly recurrence keeps local wall time across DST and skips absent month days', () => {
  const rows = calendar.expandCalendarOccurrences(event({ starts_at: '2026-10-23T07:00:00Z', ends_at: '2026-10-23T07:30:00Z', recurrence: { frequency: 'daily', interval: 1, count: 4 } }), '2026-01-01', '2027-01-01');
  assert.deepEqual(plain(rows.map(row => row.starts_at)), ['2026-10-23T07:00:00.000Z', '2026-10-24T07:00:00.000Z', '2026-10-25T08:00:00.000Z', '2026-10-26T08:00:00.000Z']);
  const monthly = calendar.expandCalendarOccurrences(event({ all_day: true, starts_at: '2026-01-31', ends_at: '2026-02-01', recurrence: { frequency: 'monthly', interval: 1, count: 3 } }), '2026-01-01', '2027-01-01');
  assert.deepEqual(plain(monthly.map(row => row.starts_at)), ['2026-01-31', '2026-03-31', '2026-05-31']);
});

test('spring-gap recurrence skips invalid local dates and counts valid occurrences', () => {
  const rows = calendar.expandCalendarOccurrences(event({ starts_at: '2026-03-28T01:30:00Z', ends_at: '2026-03-28T02:00:00Z', recurrence: { frequency: 'daily', interval: 1, count: 3 } }), '2026-03-01', '2026-04-02');
  assert.deepEqual(plain(rows.map(row => row.starts_at)), ['2026-03-28T01:30:00.000Z', '2026-03-30T00:30:00.000Z', '2026-03-31T00:30:00.000Z']);
});

test('recurrence exceptions cancel and move an occurrence into the requested window; nullable server fields resolve', () => {
  const fields = event({ starts_at: '2026-10-23T07:00:00Z', ends_at: '2026-10-23T07:30:00Z', recurrence: { frequency: 'weekly', interval: 1, count: 3 }, exceptions: [
    { original_start: '2026-10-30T08:00:00Z', cancelled: true, title: null, starts_at: null, ends_at: null, description: null, location: null },
    { original_start: '2026-11-06T08:00:00Z', cancelled: false, starts_at: '2026-10-27T12:00:00Z', ends_at: '2026-10-27T13:00:00Z', title: 'Moved' },
  ] });
  const rows = calendar.expandCalendarOccurrences(fields, '2026-10-27', '2026-10-28');
  assert.equal(rows.length, 1); assert.equal(rows[0].title, 'Moved'); assert.equal(rows[0].original_start, '2026-11-06T08:00:00.000Z');
  assert.throws(() => calendar.validateCalendar(event({ recurrence: { frequency: 'daily', interval: 1, count: 367 } })), /366/);
  assert.throws(() => calendar.validateCalendar({ ...fields, exceptions: [{ original_start: '2027-01-01T00:00:00Z', cancelled: true }] }), /distinct dates/);
});

test('bounded RRULE and EXDATE import and expanded ICS export preserve actual occurrence instants', () => {
  const source = calendar.exportCalendar([{ id: 'one', fields: event({ starts_at: '2026-10-23T07:00:00Z', ends_at: '2026-10-23T07:30:00Z' }) }]);
  const [imported] = calendar.importCalendar(source.replace('SUMMARY:', 'RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20261024T070000Z\r\nSUMMARY:'));
  // UTC DTSTART specifies a UTC recurrence; TZID imports retain local-wall DST.
  assert.equal(imported.recurrence.count, 3); assert.equal(imported.exceptions.length, 1);
  const fields = event({ starts_at: '2026-10-23T07:00:00Z', ends_at: '2026-10-23T07:30:00Z', recurrence: { frequency: 'daily', interval: 1, count: 3 }, exceptions: [{ original_start: '2026-10-24T07:00:00Z', cancelled: true }] });
  const restored = calendar.importCalendar(calendar.exportCalendar([{ id: 'series', fields }]));
  assert.equal(restored.length, 2); assert.deepEqual(plain(restored.map(row => row.starts_at)), ['2026-10-23T07:00:00.000Z', '2026-10-25T08:00:00.000Z']);
  assert.notEqual(restored[0].external_uid, restored[1].external_uid);
});

const actualReference = (id = 'actual-document', start = 2, end = 7) => ({ kind: 'document', id, start, end });
for (const native of [true, false]) test(`calendar and linked-document field metadata round-trips ${native ? 'natively' : 'inside the encrypted event'}`, async () => {
  for (const kind of ['calendar', 'documents']) {
    const f = apiFixture({ native, kind });
    const fields = kind === 'calendar' ? event({ title: '  @Plan  ', description: '@Ada review', field_references: { title: [actualReference()] } }) : { ...f.fields, title: '  @Plan  ', notes: '@Ada review', field_references: { title: [actualReference()] } };
    const saved = kind === 'calendar' ? await f.api.saveEvent('room', fields) : await f.api.saveDocument('room', fields);
    assert.equal(saved.fields.title, '  @Plan  '); assert.deepEqual(plain(saved.fields.field_references), fields.field_references);
    assert.equal(f.resolutions.length, 1);
    if (!native) { assert.equal(f.wire().body.fields, undefined); assert.equal(f.wire().body.field_references, undefined); assert.deepEqual(plain(f.payload().content.fields.field_references), fields.field_references); }
  }
});
test('new unavailable resource bindings cannot send an integration event or native revision', async () => {
  const f = apiFixture({ unavailable: true });
  await assert.rejects(f.api.saveDocument('room', { ...f.fields, title: '  @Plan  ', field_references: { title: [actualReference()] } }), /no longer available/);
  assert.equal(f.payload(), undefined); assert.equal(f.wire(), undefined);
});
test('calendar occurrence overrides drop only changed inherited bindings, and ICS carries readable labels only', () => {
  const fields = event({ title: '@Plan', description: '@Ada', recurrence: { frequency: 'daily', interval: 1, count: 2 }, field_references: { title: [actualReference('actual-document', 0, 5)], description: [{ kind: 'person', id: 'actual-person', start: 0, end: 4 }] }, exceptions: [{ original_start: '2026-10-25T00:30:00Z', cancelled: false, title: 'Other agenda' }] });
  const rows = calendar.expandCalendarOccurrences(fields, '2026-10-24T00:00:00Z', '2026-10-28T00:00:00Z');
  assert.equal(rows[0].field_references.title, undefined); assert.equal(rows[0].field_references.description[0].id, 'actual-person'); assert.equal(rows[1].field_references.title[0].id, 'actual-document');
  const ics = calendar.exportCalendar([{ id: 'one', fields }]); assert.ok(!ics.includes('actual-person') && !ics.includes('field_references'));
  const restored = calendar.importCalendar(ics); assert.ok(restored.every(row => row.field_references === undefined));
});
