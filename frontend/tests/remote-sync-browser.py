#!/usr/bin/env python3
"""Real private API/worker/Chromium proof against an ephemeral HTTPS DAV server.

No provider credentials or external services are used. The private launcher
replaces only the remote transport for one exact synthetic HTTPS origin and two
exact collection paths; production destination validation is never weakened.
Run: CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/remote-sync-browser.py
"""
import base64
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from tests.oidc_mock import tls_fixture
from app.services.remote_dav import serialize_calendar, parse_calendar

RUN = secrets.token_hex(6)
PASSWORD = 'Private-sync-browser!2026'
ORIGIN = 'https://dav.remote-proof.example'
REPORT = Path(os.getenv('REMOTE_SYNC_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/remote-sync-browser.json')))
EVENT = {'title': 'Provider review', 'description': 'Remote calendar notes', 'location': 'Studio',
         'starts_at': '2026-11-02T10:00:00Z', 'ends_at': '2026-11-02T11:00:00Z', 'timezone': 'Europe/Stockholm',
         'all_day': False, 'reminder_minutes': 15, 'external_uid': 'remote-calendar-proof@example.invalid'}


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def api(base, method, path, user=None, data=None, raw=False):
    headers = {'Content-Type': 'application/json'}
    if user: headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(base + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=45) as response:
        value = response.read()
        return value if raw else json.loads(value) if value else None


def rejects(base, method, path, user=None, data=None, statuses=(400, 401, 403, 404, 409, 422)):
    try: api(base, method, path, user, data)
    except HTTPError as failure:
        assert failure.code in statuses, 'Unexpected rejection status'; return failure.code
    raise AssertionError('Forbidden remote synchronization operation succeeded')


def upload(base, user, channel, name, body, version=None):
    boundary = 'freecollab-proof-' + secrets.token_hex(12)
    payload = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + name + '"\r\nContent-Type: text/plain\r\n\r\n').encode() + body + ('\r\n--' + boundary + '--\r\n').encode()
    path = '/files/' + version + '/versions' if version else '/files/channels/' + channel
    request = Request(base + path, data=payload, method='POST', headers={
        'Authorization': 'Bearer ' + user['access_token'], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urlopen(request, timeout=45) as response: return json.load(response)


def ready(url, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'Private proof service exited before readiness'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200: return
        except (HTTPError, URLError, TimeoutError): pass
        time.sleep(.2)
    raise AssertionError('Private proof service did not become ready')


def stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


class DAVFixture:
    def __init__(self, cert, key):
        self.lock = threading.RLock(); self.files = {}; self.deleted = {}; self.version = 0
        self.requests = []; self.failure = False
        self.username = 'private-sync-member'; self.password = secrets.token_urlsafe(32)
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def send(self, status, body=b'', headers=None):
                self.send_response(status)
                for name, value in (headers or {}).items(): self.send_header(name, value)
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def receive(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                expected = 'Basic ' + base64.b64encode((fixture.username + ':' + fixture.password).encode()).decode()
                if self.headers.get('Authorization') != expected: self.send(401); return None
                if not (self.path.startswith('/files/') or self.path.startswith('/calendar/')): self.send(403); return None
                fixture.requests.append((self.command, self.path))
                if fixture.failure and self.path.startswith('/files/'): self.send(503, b'private fixture response must not escape', {'Retry-After': '1'}); return None
                return body
            def do_GET(self):
                if self.receive() is None: return
                with fixture.lock:
                    row = fixture.files.get(self.path)
                    if row is None: return self.send(404)
                    if self.headers.get('If-Match') and self.headers['If-Match'] != row['etag']: return self.send(412)
                    self.send(200, row['body'], {'ETag': row['etag'], 'Content-Type': row['mime']})
            def do_PUT(self):
                body = self.receive()
                if body is None: return
                with fixture.lock:
                    old = fixture.files.get(self.path)
                    if self.headers.get('If-None-Match') == '*' and old: return self.send(412)
                    if self.headers.get('If-Match') and (not old or self.headers['If-Match'] != old['etag']): return self.send(412)
                    row = fixture.put(self.path, body, self.headers.get('Content-Type', 'application/octet-stream'))
                    self.send(204 if old else 201, headers={'ETag': row['etag']})
            def do_DELETE(self):
                if self.receive() is None: return
                with fixture.lock:
                    row = fixture.files.get(self.path)
                    if row is None: return self.send(404)
                    if self.headers.get('If-Match') != row['etag']: return self.send(412)
                    fixture.version += 1; fixture.deleted[self.path] = fixture.version; del fixture.files[self.path]
                    self.send(204)
            def do_PROPFIND(self): self.multistatus(False)
            def do_REPORT(self): self.multistatus(True)
            def multistatus(self, report):
                body = self.receive()
                if body is None: return
                with fixture.lock:
                    d, c = '{DAV:}', '{urn:ietf:params:xml:ns:caldav}'
                    root = ET.Element(d + 'multistatus'); ET.SubElement(root, d + 'sync-token').text = 'urn:proof:' + str(fixture.version)
                    if not report:
                        row = ET.SubElement(root, d + 'response'); ET.SubElement(row, d + 'href').text = self.path
                        status = ET.SubElement(row, d + 'propstat'); ET.SubElement(status, d + 'status').text = 'HTTP/1.1 200 OK'
                        prop = ET.SubElement(status, d + 'prop'); types = ET.SubElement(prop, d + 'resourcetype'); ET.SubElement(types, d + 'collection')
                    for path, value in fixture.files.items():
                        if not path.startswith(self.path): continue
                        row = ET.SubElement(root, d + 'response'); ET.SubElement(row, d + 'href').text = path
                        status = ET.SubElement(row, d + 'propstat'); ET.SubElement(status, d + 'status').text = 'HTTP/1.1 200 OK'
                        prop = ET.SubElement(status, d + 'prop')
                        for name, value_text in [('getetag', value['etag']), ('displayname', unquote(path.rsplit('/', 1)[-1])), ('getcontenttype', value['mime']), ('getcontentlength', str(len(value['body'])))]: ET.SubElement(prop, d + name).text = value_text
                        if report and value['mime'].startswith('text/calendar'): ET.SubElement(prop, c + 'calendar-data').text = value['body'].decode()
                    if report and b'sync-token' in body:
                        for path in fixture.deleted:
                            if not path.startswith(self.path): continue
                            row = ET.SubElement(root, d + 'response'); ET.SubElement(row, d + 'href').text = path; ET.SubElement(row, d + 'status').text = 'HTTP/1.1 404 Not Found'
                    self.send(207, ET.tostring(root), {'Content-Type': 'application/xml'})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def put(self, path, body, mime='text/plain'):
        with self.lock:
            self.version += 1
            row = {'body': body, 'mime': mime, 'etag': '"proof-' + str(self.version) + '"'}
            self.files[path] = row; self.deleted.pop(path, None); return row
    def close(self): self.server.shutdown(); self.server.server_close(); self.thread.join(3)


def launcher(private, fixture, cert, api_port):
    source = private / 'private_services.py'
    source.write_text('''import sys, ssl
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app.services import remote_transport, remote_dav
fixture_origin = ''' + repr(ORIGIN) + '''
fixture_endpoint = ''' + repr('https://127.0.0.1:' + str(fixture.server.server_port)) + '''
context = ssl.create_default_context(cafile=''' + repr(str(cert)) + ''')
def fixture_request(method, url, *, headers=None, body=None, max_bytes=remote_transport.MAX_JSON_BYTES, timeout=30):
    parsed = urlsplit(url)
    assert parsed.scheme + '://' + parsed.netloc == fixture_origin and not parsed.query and not parsed.fragment
    assert parsed.path.startswith('/files/') or parsed.path.startswith('/calendar/')
    assert method in {'GET','PUT','DELETE','PROPFIND','REPORT','OPTIONS'}
    request = Request(fixture_endpoint + parsed.path, data=body, headers=headers or {}, method=method)
    try: response = urlopen(request, timeout=timeout, context=context)
    except HTTPError as failure: response = failure
    with response:
        value = response.read(max_bytes + 1)
        if len(value) > max_bytes: raise remote_transport.ProviderError('response_too_large')
        return remote_transport.Response(response.status, {key.lower(): value for key,value in response.headers.items()}, value)
remote_transport.request = fixture_request
remote_dav.request = fixture_request
from app.main import socket_app
if len(sys.argv) > 1 and sys.argv[1] == 'worker':
    from sqlmodel import Session
    from app.database import engine
    from app.services.remote_sync import process_pending
    with Session(engine) as session: print('completed=' + str(process_pending(session, limit=10)))
else:
    import uvicorn
    uvicorn.run(socket_app, host='127.0.0.1', port=''' + str(api_port) + ''', proxy_headers=False, access_log=False)
''')
    return source


def vite_launcher(private, api_base, app_port):
    source = private / 'vite.mjs'
    source.write_text('import {createServer} from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';\n'
                      'import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';\n'
                      'const server=await createServer({configFile:false,root:' + json.dumps(str(ROOT / 'frontend')) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'vite-cache')) + ",optimizeDeps:{exclude:['@matrix-org/matrix-sdk-crypto-wasm']},server:{host:'127.0.0.1',port:" + str(app_port) + ",strictPort:true,proxy:{'/api':{target:" + json.dumps(api_base) + ",changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,'')},'/socket.io':{target:" + json.dumps(api_base) + ',ws:true}}}});await server.listen();\n')
    return source


def native_user(base, name):
    email = name.lower().replace(' ', '-') + '-' + RUN + '@example.com'
    user = api(base, 'POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD})
    user['email'] = email; return user


def confirm(panel, action, title, wrong=False):
    panel.get_by_role('button', name=action, exact=True).click()
    form = panel.get_by_role('form', name=title, exact=True)
    form.get_by_label('FreeCollab password', exact=True).fill('Wrong-private-password!' if wrong else PASSWORD)
    form.get_by_role('button', name='Confirm', exact=True).click()
    if wrong: expect(panel.get_by_role('alert')).to_contain_text(re.compile('password', re.I))
    else: expect(form).to_have_count(0)


def create_connection(panel, fixture, channel, kind, name):
    panel.get_by_role('button', name='Add cloud connection', exact=True).click()
    form = panel.get_by_role('form', name='Cloud synchronization configuration', exact=True)
    form.get_by_label('Connection name', exact=True).fill(name)
    form.get_by_label('Provider', exact=True).select_option('nextcloud' if kind == 'files' else 'caldav')
    form.get_by_label('Channel granted to this connection', exact=True).select_option(channel['id'])
    options = form.get_by_label('Channel granted to this connection', exact=True).locator('option').all_text_contents()
    assert 'Secret room' not in options, 'An encrypted channel appeared in the server sync selector'
    form.get_by_label('WebDAV folder URL' if kind == 'files' else 'CalDAV calendar collection URL', exact=True).fill(ORIGIN + '/' + kind + '/')
    form.get_by_label('Provider username', exact=True).fill(fixture.username)
    form.get_by_label('Provider app password', exact=True).fill(fixture.password)
    form.get_by_role('checkbox').check()
    confirm(panel, 'Save paused connection', 'Confirm cloud synchronization')
    expect(panel.get_by_role('region', name='Cloud connection details', exact=True)).to_be_visible()
    assert panel.get_by_label('Provider app password', exact=True).count() == 0
    details = panel.get_by_role('region', name='Cloud connection details', exact=True)
    expect(details.get_by_role('button', name='Enable sync', exact=True)).to_be_disabled()
    confirm(panel, 'Test connection', 'Test this saved connection?')
    confirm(panel, 'Enable sync', 'Enable two-way synchronization?')


def connections(base, workspace, owner): return api(base, 'GET', '/workspaces/' + workspace['id'] + '/sync', owner)['connections']
def files(base, channel, owner): return api(base, 'GET', '/files/channels/' + channel['id'], owner)
def events(base, channel, owner): return api(base, 'GET', '/channels/' + channel['id'] + '/integrations/calendar', owner)
def refresh(panel): panel.get_by_role('button', name='Refresh cloud synchronization', exact=True).click()
def select(panel, name): panel.get_by_role('button', name=re.compile('^' + re.escape(name))).click()
def secret_check(page, values):
    assert page.evaluate('(values) => values.every(secret => !Object.values(localStorage).some(value => value.includes(secret)) && !Object.values(sessionStorage).some(value => value.includes(secret)))', values), 'Provider credential entered browser storage'
    assert all(value not in page.url for value in values), 'Provider credential entered URL'


def main():
    stage, results, processes, streams, fixture = 'startup', {}, [], [], None
    with tempfile.TemporaryDirectory(prefix='freecollab-remote-sync-') as directory:
        private = Path(directory)
        try:
            cert, key = tls_fixture(private / 'tls'); fixture = DAVFixture(cert, key)
            fixture.put('/files/provider-notes.txt', b'Provider initial bytes')
            fixture.put('/calendar/provider-review.ics', serialize_calendar(EVENT), 'text/calendar')
            api_port, app_port = port(), port(); base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(app_port)
            database = private / 'proof.db'; environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]),
                OIDC_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='true', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            service = launcher(private, fixture, cert, api_port)
            for command, filename, cwd in [([python, str(service)], 'api.log', ROOT / 'backend'), (['node', str(vite_launcher(private, base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            ready(base + '/ready', processes); ready(app + '/', processes)
            def worker():
                completed = subprocess.run([python, str(service), 'worker'], cwd=ROOT / 'backend', env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
                assert completed.returncode == 0, 'Private one-shot synchronization worker failed'
                print('Worker states: ' + json.dumps([{'kind': row['kind'], 'status': row['status'], 'error': row['last_error']} for row in connections(base, workspace, owner)]), flush=True)
            stage = 'native fixtures and server-encrypted channel content'
            owner, member, other_owner = native_user(base, 'Sync Owner'), native_user(base, 'Sync Member'), native_user(base, 'Other Owner')
            workspace = api(base, 'POST', '/workspaces', owner, {'name': 'Studio Sync'})
            other_workspace = api(base, 'POST', '/workspaces', other_owner, {'name': 'Other Studio'})
            channel = api(base, 'POST', '/channels', owner, {'name': 'Shared project', 'type': 'group', 'workspace_id': workspace['id']})
            secret = api(base, 'POST', '/channels', owner, {'name': 'Secret room', 'type': 'group', 'workspace_id': workspace['id']})
            with sqlite3.connect(database) as connection:
                connection.execute('INSERT INTO securechannel (channel_id,matrix_room_id,created_by,status,created_at) VALUES (?,?,?,?,?)',
                    (secret['id'], '!fixture-secret:example.invalid', owner['user_id'], 'ready', datetime.utcnow().isoformat(' ')))
            invitation = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
            api(base, 'POST', '/workspaces/invites/accept', member, {'token': invitation['token']})
            channel_invitation = api(base, 'POST', '/channels/' + channel['id'] + '/invites', owner, {'expires_in_hours': 24})
            api(base, 'POST', '/channels/invites/accept', member, {'token': channel_invitation['token']})
            local_file = upload(base, owner, channel['id'], 'channel-notes.txt', b'Channel initial bytes')
            local_event = api(base, 'POST', '/channels/' + channel['id'] + '/integrations/calendar', owner,
                {'id': 'native-event-' + RUN, 'expected_revision': 0, 'fields': {**EVENT, 'title': 'Channel review', 'external_uid': 'native-proof@example.invalid'}})
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(30000); page_errors, request_urls = [], []
                page.on('pageerror', lambda error: page_errors.append(type(error).__name__))
                page.on('request', lambda request: request_urls.append(request.url))
                try:
                    stage = 'native browser login and customer-owned DAV configurations'
                    page.goto(app, wait_until='domcontentloaded'); page.evaluate('([key,value]) => localStorage.setItem(key,value)', ['freecollab.workspace:' + owner['user_id'], workspace['id']])
                    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
                    page.get_by_label('Email', exact=True).fill(owner['email']); page.get_by_label('Password', exact=True).fill(PASSWORD)
                    page.get_by_role('button', name='Sign In', exact=True).click()
                    page.get_by_role('button', name='Integrations', exact=True).click()
                    panel = page.get_by_role('dialog', name='Integrations', exact=True); panel.get_by_role('button', name='Sync', exact=True).click()
                    create_connection(panel, fixture, channel, 'files', 'Studio files')
                    create_connection(panel, fixture, channel, 'calendar', 'Studio calendar')
                    configs = connections(base, workspace, owner); assert len(configs) == 2
                    file_connection = next(row for row in configs if row['kind'] == 'files'); calendar_connection = next(row for row in configs if row['kind'] == 'calendar')
                    assert all(row['enabled'] and row['tested'] for row in configs)
                    assert fixture.password not in json.dumps(configs)
                    results['real_ui_customer_dav_and_caldav_connect_test_enable'] = True
                    results['test_required_before_enable'] = True
                    results['server_encrypted_only_channel_selector'] = True
                    stage = 'real worker initial two-way file bytes and calendar persistence'
                    worker()
                    stored_files = files(base, channel, owner); stored_events = events(base, channel, owner)
                    imported = next(row for row in stored_files if row['filename'] == 'provider-notes.txt')
                    assert api(base, 'GET', '/files/' + imported['id'], owner, raw=True) == b'Provider initial bytes', 'Remote bytes did not persist locally'
                    assert any(row['body'] == b'Channel initial bytes' for path, row in fixture.files.items() if path.startswith('/files/')), 'Native file bytes did not export'
                    assert {row['fields']['title'] for row in stored_events} == {'Channel review', 'Provider review'}, 'Two-way native calendar persistence missing'
                    assert any(parse_calendar(row['body'])['title'] == 'Channel review' for path, row in fixture.files.items() if path.startswith('/calendar/')), 'Native calendar event did not export'
                    results['real_worker_two_way_file_bytes_and_calendar'] = True
                    stage = 'ordinary member other tenant and encrypted-channel fences'
                    for actor, wid in [(member, workspace['id']), (other_owner, workspace['id']), (owner, other_workspace['id'])]: rejects(base, 'GET', '/workspaces/' + wid + '/sync', actor, statuses=(403, 404))
                    draft = {'name': 'Forbidden secure export', 'provider': 'nextcloud', 'kind': 'files', 'channel_id': secret['id'], 'config': {'base_url': ORIGIN + '/files/'}, 'credentials': {'username': fixture.username, 'app_password': fixture.password}, 'password': PASSWORD, 'consent': True}
                    seen = len(fixture.requests)
                    rejects(base, 'POST', '/workspaces/' + workspace['id'] + '/sync', owner, draft, statuses=(409,))
                    assert len(fixture.requests) == seen
                    rejects(base, 'POST', '/workspaces/' + workspace['id'] + '/sync', owner, {**draft, 'channel_id': channel['id'], 'config': {'base_url': 'https://127.0.0.1/private/'}}, statuses=(422,))
                    assert len(fixture.requests) == seen
                    results['member_cross_tenant_and_secure_channel_export_denied'] = True
                    results['private_destination_rejected_before_outbound'] = True
                    shared_status = api(base, 'GET', '/channels/' + channel['id'] + '/sync-status', member)
                    assert len(shared_status['connections']) == 2
                    assert all(set(row) == {'id', 'name', 'provider', 'kind', 'enabled', 'channel_id'} for row in shared_status['connections'])
                    results['channel_member_gets_sharing_status_without_provider_config'] = True
                    stage = 'remote-only changes append immutable local versions'
                    fixture.put('/files/provider-notes.txt', b'Provider updated bytes')
                    fixture.put('/calendar/provider-review.ics', serialize_calendar({**EVENT, 'title': 'Provider revised review'}), 'text/calendar')
                    for name in ['Studio files', 'Studio calendar']:
                        select(panel, name); confirm(panel, 'Sync now', 'Run synchronization now?')
                    worker()
                    latest = next(row for row in files(base, channel, owner) if row['filename'] == 'provider-notes.txt')
                    assert api(base, 'GET', '/files/' + latest['id'], owner, raw=True) == b'Provider updated bytes'
                    history = api(base, 'GET', '/files/' + latest['id'] + '/versions', owner)
                    assert len(history) == 2 and api(base, 'GET', '/files/' + imported['id'], owner, raw=True) == b'Provider initial bytes'
                    assert any(row['fields']['title'] == 'Provider revised review' for row in events(base, channel, owner))
                    results['remote_updates_append_retained_immutable_versions'] = True
                    stage = 'native calendar update exports to provider without changing UID'
                    api(base, 'POST', '/channels/' + channel['id'] + '/integrations/calendar', owner,
                        {'id': local_event['id'], 'expected_revision': local_event['revision'], 'fields': {**local_event['fields'], 'title': 'Channel revised review'}})
                    select(panel, 'Studio calendar'); confirm(panel, 'Sync now', 'Run synchronization now?'); worker()
                    exported_event = next(parse_calendar(row['body']) for path, row in fixture.files.items() if path.startswith('/calendar/') and parse_calendar(row['body'])['title'] == 'Channel revised review')
                    assert exported_event['external_uid'] == 'native-proof@example.invalid'
                    results['native_calendar_update_exports_with_stable_uid'] = True
                    stage = 'both-changed conflict and explicit provider-version resolution'
                    local_latest = upload(base, owner, channel['id'], 'provider-notes.txt', b'Native conflicting bytes', latest['id'])
                    fixture.put('/files/provider-notes.txt', b'Provider conflicting bytes')
                    select(panel, 'Studio files'); confirm(panel, 'Sync now', 'Run synchronization now?'); worker(); refresh(panel)
                    conflict_path = '/workspaces/' + workspace['id'] + '/sync/' + file_connection['id'] + '/conflicts'
                    pending = api(base, 'GET', conflict_path, owner)['conflicts']; assert len(pending) == 1
                    assert api(base, 'GET', '/files/' + local_latest['id'], owner, raw=True) == b'Native conflicting bytes'
                    panel.get_by_role('button', name='Refresh sync activity and conflicts', exact=True).click()
                    expect(panel.get_by_role('button', name='Use provider version', exact=True)).to_be_visible()
                    confirm(panel, 'Use provider version', 'Resolve this synchronization conflict?'); worker(); refresh(panel)
                    assert not api(base, 'GET', conflict_path, owner)['conflicts']
                    resolved = next(row for row in files(base, channel, owner) if row['filename'] == 'provider-notes.txt')
                    assert api(base, 'GET', '/files/' + resolved['id'], owner, raw=True) == b'Provider conflicting bytes'
                    assert len(api(base, 'GET', '/files/' + resolved['id'] + '/versions', owner)) == 4
                    results['both_changed_preserves_copies_until_explicit_resolution'] = True
                    stage = 'explicit channel-version conflict resolution exports exact bytes'
                    upload(base, owner, channel['id'], 'provider-notes.txt', b'Explicit channel choice', resolved['id'])
                    fixture.put('/files/provider-notes.txt', b'Other provider choice')
                    confirm(panel, 'Sync now', 'Run synchronization now?'); worker(); refresh(panel)
                    panel.get_by_role('button', name='Refresh sync activity and conflicts', exact=True).click()
                    expect(panel.get_by_role('button', name='Use channel version', exact=True)).to_be_visible()
                    confirm(panel, 'Use channel version', 'Resolve this synchronization conflict?'); worker(); refresh(panel)
                    assert not api(base, 'GET', conflict_path, owner)['conflicts']
                    assert fixture.files['/files/provider-notes.txt']['body'] == b'Explicit channel choice'
                    results['explicit_channel_resolution_conditional_remote_write'] = True
                    stage = 'provider failure visible and explicit retry succeeds'
                    fixture.failure = True
                    confirm(panel, 'Sync now', 'Run synchronization now?'); worker(); refresh(panel)
                    current = next(row for row in connections(base, workspace, owner) if row['id'] == file_connection['id'])
                    assert current['last_error'] == 'provider_unavailable' and current['enabled']
                    expect(panel.get_by_text('The last synchronization did not finish.', exact=False)).to_be_visible()
                    fixture.failure = False
                    confirm(panel, 'Sync now', 'Run synchronization now?'); worker(); refresh(panel)
                    assert next(row for row in connections(base, workspace, owner) if row['id'] == file_connection['id'])['last_error'] is None
                    results['provider_failure_safe_status_and_manual_retry'] = True
                    stage = 'pause blocks worker exports while preserving remote copies'
                    confirm(panel, 'Pause sync', 'Pause synchronization?')
                    prior_remote = {path: row['body'] for path, row in fixture.files.items() if path.startswith('/files/')}
                    upload(base, owner, channel['id'], 'paused-file.txt', b'Not shared while paused')
                    worker()
                    assert prior_remote == {path: row['body'] for path, row in fixture.files.items() if path.startswith('/files/')}
                    results['pause_prevents_future_exports_and_retains_copies'] = True
                    stage = 'draft retarget clears provider grant before any new destination contact'
                    grant_body = {'name': 'Temporary draft', 'provider': 'nextcloud', 'kind': 'files', 'channel_id': channel['id'],
                        'config': {'base_url': ORIGIN + '/unconnected-draft/'}, 'credentials': {'username': fixture.username, 'app_password': fixture.password},
                        'password': PASSWORD, 'consent': True}
                    draft_grant = api(base, 'POST', '/workspaces/' + workspace['id'] + '/sync', owner, grant_body)
                    contact_count = len(fixture.requests)
                    retargeted = api(base, 'PUT', '/workspaces/' + workspace['id'] + '/sync/' + draft_grant['id'], owner,
                        {**grant_body, 'config': {'base_url': 'https://different.remote-proof.example/other/'}, 'credentials': {}})
                    assert not retargeted['has_credentials'] and not retargeted['enabled'], 'Draft target change retained another resource’s provider credentials'
                    rejects(base, 'POST', '/workspaces/' + workspace['id'] + '/sync/' + draft_grant['id'] + '/test', owner,
                        {'password': PASSWORD}, statuses=(409,))
                    assert len(fixture.requests) == contact_count, 'Retargeted draft contacted a provider before fresh credentials'
                    results['draft_retarget_requires_fresh_provider_grant_without_outbound'] = True
                    stage = 'admin mobile fit focus and cleared private fields'
                    secret_check(page, [fixture.password]); assert page.get_by_label('Provider app password', exact=True).count() == 0
                    assert page.get_by_label('FreeCollab password', exact=True).count() == 0
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    panel.locator('.fc-account-body').evaluate('element => { element.scrollTop = 0; }')
                    page.screenshot(path=str(REPORT.with_name('remote-sync-desktop.png')))
                    page.set_viewport_size({'width': 320, 'height': 900})
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    assert panel.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
                    panel.get_by_role('button', name='Close integrations', exact=True).focus()
                    for key in ['Tab', 'Shift+Tab', 'Tab']:
                        page.keyboard.press(key); assert panel.evaluate('element => element.contains(document.activeElement)')
                    panel.get_by_role('button', name='Sync', exact=True).scroll_into_view_if_needed()
                    panel.locator('.fc-account-body').evaluate('element => { element.scrollTop = 0; }')
                    page.screenshot(path=str(REPORT.with_name('remote-sync-mobile.png')))
                    results['admin_sync_mobile_320px_and_keyboard_focus'] = True
                    assert not page_errors, 'Browser page exception'
                    assert not any('access_token=' in url or re.search(r'eyJ[A-Za-z0-9_-]+\.', url) for url in request_urls)
                    assert all(fixture.password not in url for url in request_urls)
                    for path in [private / 'api.log', private / 'vite.log']: assert fixture.password not in path.read_text(errors='replace')
                    with sqlite3.connect(database) as connection:
                        secrets_at_rest = [row[0] for row in connection.execute('SELECT encrypted_credentials FROM remotesyncconnection')]
                    assert secrets_at_rest and all(fixture.password not in (value or '') for value in secrets_at_rest)
                    results['secrets_encrypted_no_plaintext_browser_storage_logs_urls'] = True
                    results['zero_browser_page_errors'] = True
                    print('PASS real remote synchronization UI, HTTPS DAV adapters, durable worker, two-way bytes/calendar, conflicts, pause/retry and320px', flush=True)
                finally: context.close(); browser.close()
        except Exception as failure:
            print('FAIL remote-sync browser stage: ' + stage + '; exception=' + type(failure).__name__, file=sys.stderr, flush=True)
            detail = str(failure).replace(PASSWORD, '[redacted]')
            if fixture: detail = detail.replace(fixture.password, '[redacted]')
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', detail)
            print(detail[:2500], file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            stop(processes)
            for stream in streams: stream.close()
            if fixture: fixture.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Private real API/Vite/Chromium and one-shot durable worker against local trusted HTTPS DAV; no external provider calls or credentials', 'results': results, 'cleanup': 'Own processes, SQLite database, storage, keys, certificates and browser contexts removed in finally'}, indent=2) + '\n')


if __name__ == '__main__': main()
