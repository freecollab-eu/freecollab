import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  const state = { token: 'current-account', calls: [], response: { recipes: [] }, beforeResponse: null };
  const api = { getAccessToken: () => state.token };
  const connectionSetupApi = { get: async (...scope) => { state.calls.push(scope); await state.beforeResponse?.(); return state.response; } };
  const dependencies = { react: {}, './api': { api }, './connection-setup-api': { connectionSetupApi },
    './external-meetings-api': { providerNames: { google_meet: 'Google Meet', microsoft_teams: 'Microsoft Teams', zoom: 'Zoom' } } };
  const source = readFileSync(new URL('../src/lib/connection-form-setup.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, URL, require: name => {
    if (!(name in dependencies)) throw new Error('Unexpected dependency: ' + name);
    return dependencies[name];
  } });
  return { ...exports, state };
}

test('preset draft preparation cannot authorize or carry a previous customer application credential', () => {
  const f = fixture();
  const box = f.freshCloudApplication('box', true);
  assert.equal(box.name, 'Box files'); assert.equal(box.folder_id, '0');
  box.client_secret = 'previous-secret'; box.password = 'native-password'; box.code = 'native-code'; box.consent = true;
  const dropbox = f.freshCloudApplication('dropbox', true);
  assert.equal(dropbox.provider, 'dropbox'); assert.equal(dropbox.folder_id, ''); assert.equal(dropbox.client_secret, '');
  assert.equal(dropbox.password, ''); assert.equal(dropbox.code, ''); assert.equal(dropbox.consent, false);
  const google = f.freshMeetingApplication('google_meet', true);
  assert.equal(google.calendarId, 'primary'); google.clientSecret = 'previous-meeting-secret'; google.source = 'old-calendar-grant';
  for (const provider of ['microsoft_teams', 'zoom']) {
    const next = f.freshMeetingApplication(provider, true);
    assert.equal(next.clientSecret, ''); assert.equal(next.clientId, ''); assert.equal(next.source, ''); assert.equal(next.calendarId, ''); assert.equal(next.tenantId, '');
  }
  assert.equal(f.state.calls.length, 0);
});

test('callback reads use the requested native scope and the selected recipe, without fallback to another provider', async () => {
  const f = fixture(), exact = 'https://customer.example.org/api/external-meetings/oauth/callback';
  f.state.response = { recipes: [{ id: 'meetings.zoom', callback_url: 'https://another.example.org/callback' }, { id: 'meetings.google_meet', callback_url: exact }] };
  assert.equal(await f.meetingSetupCallback('workspace /?', 'channel &?', 'google_meet'), exact);
  assert.equal(f.state.calls[0][0], 'workspace /?'); assert.equal(f.state.calls[0][1], 'channel &?');
  assert.equal(await f.meetingSetupCallback('workspace', 'channel', 'microsoft_teams'), '');
});

test('logout or account replacement during a callback read discards the old response', async () => {
  const f = fixture();
  f.state.response = { recipes: [{ id: 'meetings.google_meet', callback_url: 'https://customer.example.org/callback' }] };
  f.state.beforeResponse = () => { f.state.token = null; };
  await assert.rejects(f.meetingSetupCallback('workspace', 'channel', 'google_meet'), /account changed/);
});

test('only exact safe server callbacks may be displayed or copied', () => {
  const f = fixture();
  for (const exact of ['https://customer.example.org/api/external-meetings/oauth/callback', 'http://localhost:8000/api/cloud-files/oauth/callback', 'http://127.0.0.1:8000/api/cloud-files/oauth/callback', 'http://[::1]:8000/api/external-meetings/oauth/callback']) assert.equal(f.validSetupCallback(exact), exact);
  for (const unsafe of ['', null, {}, 'javascript:alert(1)', '//customer.example.org/callback', 'http://customer.example.org/callback', 'https://user:secret@customer.example.org/callback', 'https://customer.example.org/callback?access_token=private', 'https://customer.example.org/callback#private', 'https://customer.example.org/callback\n', 'https://customer.example.org\\@other.example/callback']) assert.equal(f.validSetupCallback(unsafe), '');
});
