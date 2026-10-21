"""Actual local Google/Graph HTTP fixtures; no real provider credentials or calls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest

from app.services import remote_providers as providers
from app.services import remote_transport as transport

CLIENT = {'client_id': 'local-client', 'client_secret': 'local-client-secret'}
GOOGLE_FILES = {**CLIENT, 'folder_id': 'selected-folder'}
GRAPH_FILES = {**CLIENT, 'tenant_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'drive_id': 'selected-drive', 'folder_id': 'selected-folder'}
GOOGLE_CAL = {**CLIENT, 'calendar_id': 'selected-calendar'}
GRAPH_CAL = {**GRAPH_FILES, 'calendar_id': 'selected-calendar'}
CREDS = {'access_token': 'fixture-access-token'}
FIELDS = {'title': 'Review', 'description': 'Notes', 'location': 'Studio', 'starts_at': '2026-10-20T10:00:00Z',
          'ends_at': '2026-10-20T11:00:00Z', 'timezone': 'Europe/Stockholm', 'all_day': False, 'reminder_minutes': 15,
          'external_uid': None}


@pytest.fixture
def api(monkeypatch):
    class State:
        requests = []; google_version = 1; graph_version = 1; content = b'Original remote bytes'; external_parent = False; google_trashed = False
        google_event = {'id': 'event-one', 'etag': '"event-1"', 'summary': 'Review', 'description': 'Notes', 'location': 'Studio',
                        'start': {'dateTime': FIELDS['starts_at'], 'timeZone': 'Europe/Stockholm'},
                        'end': {'dateTime': FIELDS['ends_at'], 'timeZone': 'Europe/Stockholm'}, 'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 15}]}}
        graph_event = {'id': 'event-one', '@odata.etag': 'W/"event-1"', 'subject': 'Review', 'body': {'contentType': 'text', 'content': 'Notes'},
                       'location': {'displayName': 'Studio'}, 'start': {'dateTime': '2026-10-20T10:00:00', 'timeZone': 'UTC'},
                       'end': {'dateTime': '2026-10-20T11:00:00', 'timeZone': 'UTC'}, 'isAllDay': False, 'isReminderOn': True, 'reminderMinutesBeforeStart': 15}
        google_rows = None; graph_rows = None; extra_pages = False; unsafe_next = False; status = None; no_etag = False
    state = State()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def respond(self, status, body=None, headers=None):
            data = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
            self.send_response(status)
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def receive(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state.requests.append((self.command, self.path, dict(self.headers), body))
            return body
        def file(self, graph=False):
            return ({'id': 'remote-file', 'name': 'fc-mapping--notes.txt', 'eTag': '"graph-' + str(state.graph_version) + '"', 'size': len(state.content),
                     'file': {'mimeType': 'text/plain'}, 'parentReference': {'id': 'outside' if state.external_parent else 'selected-folder'},
                     '@microsoft.graph.downloadUrl': 'https://files.fixture.1drv.com/download/file?temporary=opaque'} if graph else
                    {'id': 'remote-file', 'name': 'notes.txt', 'mimeType': 'text/plain', 'version': str(state.google_version), 'size': str(len(state.content)),
                     'parents': ['outside' if state.external_parent else 'selected-folder'], 'appProperties': {'freecollabSyncKey': 'fc-mapping'}})
        def do_GET(self):
            self.receive(); parsed = urlsplit(self.path); query = parse_qs(parsed.query); path = parsed.path
            if state.status: return self.respond(state.status, {'private-provider-body': 'must-never-escape'}, {'Retry-After': '90'})
            if path == '/drive/v3/files':
                assert 'selected-folder' in query['q'][0]
                rows = state.google_rows if state.google_rows is not None else [self.file()]
                return self.respond(200, {'files': rows, **({'nextPageToken': 'next'} if state.extra_pages and 'pageToken' not in query else {})})
            if path == '/drive/v3/files/remote-file':
                if query.get('alt') == ['media']: return self.respond(200, state.content, {'Content-Type': 'text/plain'})
                return self.respond(200, self.file(), {} if state.no_etag else {'ETag': '"google-' + str(state.google_version) + '"'})
            if path == '/drive/v3/files/remote-file/export': return self.respond(200, b'%PDF fixture export')
            if path.endswith('/delta') or path == '/v1.0/delta/selected':
                rows = state.graph_rows if state.graph_rows is not None else [self.file(True)]
                result = {'value': rows, '@odata.deltaLink': 'https://graph.microsoft.com/v1.0/delta/selected?token=opaque'}
                if state.unsafe_next: result['@odata.nextLink'] = 'https://attacker.example.net/collect'
                return self.respond(200, result)
            if path == '/v1.0/drives/selected-drive/items/remote-file': return self.respond(200, self.file(True))
            if path == '/download/file':
                assert self.headers.get('Authorization') is None
                return self.respond(200, state.content)
            if path == '/calendar/v3/calendars/selected-calendar/events':
                return self.respond(200, {'items': state.google_rows if state.google_rows is not None else [state.google_event], 'nextSyncToken': 'token-next'})
            if path == '/calendar/v3/calendars/selected-calendar/events/event-one': return self.respond(200, state.google_event)
            if path == '/v1.0/me/calendars/selected-calendar/events':
                assert 'outlook.timezone="UTC"' in self.headers.get('Prefer', '')
                return self.respond(200, {'value': state.graph_rows if state.graph_rows is not None else [state.graph_event]})
            if path == '/v1.0/me/calendars/selected-calendar/events/event-one': return self.respond(200, state.graph_event)
            return self.respond(404)
        def do_POST(self):
            body = self.receive(); path = urlsplit(self.path).path
            if path.endswith('/token'):
                data = parse_qs(body.decode()); assert data['client_id'] == ['local-client']; assert data['client_secret'] == ['local-client-secret']
                assert data['grant_type'][0] in {'authorization_code', 'refresh_token'}
                return self.respond(200, {'access_token': 'new-local-access-token', 'refresh_token': 'new-local-refresh-token', 'expires_in': 3600, 'token_type': 'Bearer'})
            if path == '/calendar/v3/calendars/selected-calendar/events':
                data = json.loads(body); assert data['id'] == hashlib.sha256(b'fc-mapping').hexdigest()
                assert data['extendedProperties']['private']['freecollabSyncKey'] == 'fc-mapping'; data.update(etag='"created"')
                return self.respond(201, data)
            if path == '/v1.0/me/calendars/selected-calendar/events':
                data = json.loads(body); assert data['transactionId'] == 'fc-mapping'; data.update(id='created', **{'@odata.etag': '"created"'})
                return self.respond(201, data)
            if path == '/upload/drive/v3/files':
                assert b'freecollabSyncKey' in body and b'fc-new-mapping' in body
                return self.respond(201, {**self.file(), 'id': 'created-file'})
            return self.respond(404)
        def do_PATCH(self):
            body = self.receive(); path = urlsplit(self.path).path
            if path == '/drive/v3/files/remote-file':
                if self.headers.get('If-Match') != '"google-' + str(state.google_version) + '"': return self.respond(412)
                assert json.loads(body) == {'trashed': True}
                state.google_trashed = True
                return self.respond(200, {'id': 'remote-file', 'trashed': True})
            if path == '/upload/drive/v3/files/remote-file':
                if self.headers.get('If-Match') != '"google-' + str(state.google_version) + '"': return self.respond(412)
                assert b'Updated bytes' in body; state.google_version += 1
                return self.respond(200, self.file())
            if '/events/' in path:
                current = state.google_event if path.startswith('/calendar') else state.graph_event
                if self.headers.get('If-Match') != current.get('etag', current.get('@odata.etag')): return self.respond(412)
                current.update(json.loads(body)); return self.respond(200, current)
            return self.respond(404)
        def do_PUT(self):
            body = self.receive(); path = urlsplit(self.path).path
            if path.endswith('/content'):
                if '/remote-file/' in path and self.headers.get('If-Match') != '"graph-' + str(state.graph_version) + '"': return self.respond(412)
                if '/items/selected-folder:/' in path: assert self.headers.get('If-None-Match') == '*'
                state.content = body; state.graph_version += 1
                return self.respond(200, self.file(True))
            return self.respond(404)
        def do_DELETE(self):
            self.receive(); path = urlsplit(self.path).path
            if '/events/' in path:
                current = state.google_event if path.startswith('/calendar') else state.graph_event
                if self.headers.get('If-Match') != current.get('etag', current.get('@odata.etag')): return self.respond(412)
            elif path.startswith('/drive/'):
                if self.headers.get('If-Match') != '"google-' + str(state.google_version) + '"': return self.respond(412)
            elif self.headers.get('If-Match') != '"graph-' + str(state.graph_version) + '"': return self.respond(412)
            self.respond(204, b'')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def local(method, url, *, headers=None, body=None, max_bytes=transport.MAX_JSON_BYTES, timeout=30):
        parsed = urlsplit(url)
        assert parsed.scheme == 'https' and parsed.hostname in {'www.googleapis.com', 'graph.microsoft.com', 'oauth2.googleapis.com', 'login.microsoftonline.com', 'files.fixture.1drv.com'}
        req = Request('http://127.0.0.1:' + str(server.server_port) + parsed.path + ('?' + parsed.query if parsed.query else ''), data=body, headers=headers or {}, method=method)
        try: response = urlopen(req, timeout=timeout)
        except HTTPError as exc: response = exc
        with response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes: raise transport.ProviderError('response_too_large')
            return transport.Response(response.status, {key.lower(): value for key, value in response.headers.items()}, data)
    monkeypatch.setattr(transport, 'request', local)
    yield state
    server.shutdown(); server.server_close(); thread.join(3)


@pytest.mark.parametrize('provider,config', [('google', GOOGLE_FILES), ('microsoft', GRAPH_FILES)])
def test_real_file_catalog_read_conditional_update_and_delete(api, provider, config):
    snapshot = providers.snapshot(provider, 'files', config, CREDS)
    assert snapshot['complete'] and len(snapshot['items']) == 1
    item = snapshot['items'][0]
    assert item['remote_key'] == 'fc-mapping'
    read = providers.read_item(provider, 'files', config, CREDS, item)
    assert read['bytes'] == b'Original remote bytes' and read['etag'] == item['etag']
    updated = providers.write_item(provider, 'files', config, CREDS, {'name': 'notes.txt', 'mime_type': 'text/plain', 'bytes': b'Updated bytes'}, remote_id=item['id'], expected_etag=item['etag'])
    assert updated['etag'] != item['etag']
    with pytest.raises(transport.ProviderError) as conflict:
        providers.write_item(provider, 'files', config, CREDS, {'name': 'notes.txt', 'bytes': b'Updated bytes'}, remote_id=item['id'], expected_etag=item['etag'])
    assert conflict.value.code == 'conflict'
    providers.delete_item(provider, 'files', config, CREDS, updated['id'], updated['etag'])
    if provider == 'google':
        assert api.google_trashed is True
        assert all(row[0] != 'DELETE' for row in api.requests)


@pytest.mark.parametrize('provider,config', [('google', GOOGLE_CAL), ('microsoft', GRAPH_CAL)])
def test_real_calendar_catalog_read_create_update_and_delete(api, provider, config):
    snapshot = providers.snapshot(provider, 'calendar', config, CREDS)
    assert snapshot['complete'] and len(snapshot['items']) == 1
    item = snapshot['items'][0]
    read = providers.read_item(provider, 'calendar', config, CREDS, item)
    assert read['fields']['title'] == 'Review' and read['fields']['starts_at'] == FIELDS['starts_at']
    created = providers.write_item(provider, 'calendar', config, CREDS, {**FIELDS, 'remote_key': 'fc-mapping'})
    assert created['remote_key'] == 'fc-mapping'
    updated = providers.write_item(provider, 'calendar', config, CREDS, {**FIELDS, 'title': 'Updated review'}, item['id'], item['etag'])
    assert providers.read_item(provider, 'calendar', config, CREDS, updated)['fields']['title'] == 'Updated review'
    with pytest.raises(transport.ProviderError) as conflict:
        providers.write_item(provider, 'calendar', config, CREDS, FIELDS, item['id'], '"stale"')
    assert conflict.value.code == 'conflict'
    providers.delete_item(provider, 'calendar', config, CREDS, item['id'], item['etag'])


@pytest.mark.parametrize('provider,config', [('google', GOOGLE_FILES), ('microsoft', GRAPH_FILES)])
def test_oauth_pkce_scope_exchange_and_refresh(api, provider, config):
    normalized = providers.validate_config(provider, 'files', config)
    url = providers.authorization_url(provider, normalized, 'https://app.example.com/api/sync/callback', 's' * 32, 'c' * 43)
    query = parse_qs(urlsplit(url).query)
    assert query['code_challenge_method'] == ['S256'] and query['state'] == ['s' * 32]
    assert 'secret' not in url and query['client_id'] == ['local-client']
    assert providers.exchange_code(provider, config, 'https://app.example.com/api/sync/callback', 'local-code', 'v' * 43)['access_token'] == 'new-local-access-token'
    assert providers.refresh_tokens(provider, config, 'local-refresh-token')['refresh_token'] == 'new-local-refresh-token'


def test_google_file_create_properties_and_crash_reconciliation(api):
    result = providers.write_item('google', 'files', GOOGLE_FILES, CREDS, {'name': 'new.txt', 'mime_type': 'text/plain', 'bytes': b'New bytes', 'remote_key': 'fc-new-mapping'})
    assert result['id'] == 'created-file'
    before = len([row for row in api.requests if row[0] == 'POST'])
    existing = providers.write_item('google', 'files', GOOGLE_FILES, CREDS, {'name': 'notes.txt', 'bytes': b'Original remote bytes', 'remote_key': 'fc-mapping'})
    assert existing['id'] == 'remote-file'
    assert len([row for row in api.requests if row[0] == 'POST']) == before


def test_graph_file_create_deterministic_collection_filename(api):
    created = providers.write_item('microsoft', 'files', GRAPH_FILES, CREDS, {'name': 'new.txt', 'bytes': b'New bytes', 'remote_key': 'fc-new-mapping'})
    assert created['id'] == 'remote-file'
    write = [row for row in api.requests if row[0] == 'PUT'][0]
    assert 'fc-new-mapping--new.txt' in write[1] and write[2]['If-None-Match'] == '*'


def test_google_calendar_delta_tombstone_and_graph_file_delta(api):
    api.google_rows = [{'id': 'deleted-event', 'status': 'cancelled'}]
    result = providers.snapshot('google', 'calendar', GOOGLE_CAL, CREDS, 'last-cursor')
    assert not result['complete'] and result['items'][0]['deleted'] and result['cursor'] == 'token-next'
    api.graph_rows = [{'id': 'removed-file', 'deleted': {}}, {'id': 'moved-file', 'parentReference': {'id': 'outside'}}]
    result = providers.snapshot('microsoft', 'files', GRAPH_FILES, CREDS, 'https://graph.microsoft.com/v1.0/delta/selected?old=opaque')
    assert not result['complete'] and all(row['deleted'] for row in result['items'])
    assert {row['id'] for row in result['items']} == {'removed-file', 'moved-file'}


def test_google_docs_export_is_read_only_and_never_uploads_over_native_doc(api):
    original = providers._google_file
    def document(*args):
        raw, mapped, etag = original(*args)
        mapped.update(export=True, read_only=True, mime_type='application/pdf'); return raw, mapped, etag
    from unittest.mock import patch
    with patch.object(providers, '_google_file', document):
        assert providers.read_item('google', 'files', GOOGLE_FILES, CREDS, {'id': 'remote-file'})['bytes'].startswith(b'%PDF')
        with pytest.raises(transport.ProviderError) as error:
            providers.write_item('google', 'files', GOOGLE_FILES, CREDS, {'name': 'notes.txt', 'bytes': b'Updated bytes'}, 'remote-file', 'version:1')
        assert error.value.code == 'read_only_resource'


@pytest.mark.parametrize('provider,config', [('google', GOOGLE_FILES), ('microsoft', GRAPH_FILES)])
def test_selected_folder_boundary_cannot_be_bypassed_with_known_resource_id(api, provider, config):
    api.external_parent = True
    for operation in ('read', 'write', 'delete'):
        with pytest.raises(transport.ProviderError) as denied:
            if operation == 'read': providers.read_item(provider, 'files', config, CREDS, {'id': 'remote-file'})
            elif operation == 'write': providers.write_item(provider, 'files', config, CREDS, {'name': 'notes.txt', 'bytes': b'Updated bytes'}, 'remote-file', '"old"')
            else: providers.delete_item(provider, 'files', config, CREDS, 'remote-file', '"old"')
        assert denied.value.code == 'outside_collection'


def test_untrusted_graph_pagination_never_receives_token(api):
    api.unsafe_next = True
    with pytest.raises(transport.ProviderError) as error: providers.snapshot('microsoft', 'files', GRAPH_FILES, CREDS)
    assert error.value.code == 'unsafe_pagination'
    assert len(api.requests) == 1


@pytest.mark.parametrize('status,code', [(401, 'reauthorization_required'), (403, 'permission_denied'), (410, 'cursor_expired'), (429, 'rate_limited'), (503, 'provider_unavailable')])
def test_provider_error_bodies_do_not_escape_and_retry_is_bounded(api, status, code):
    api.status = status
    with pytest.raises(transport.ProviderError) as failure: providers.snapshot('google', 'calendar', GOOGLE_CAL, CREDS)
    assert failure.value.code == code and 'private-provider-body' not in str(failure.value)
    if status in (429, 503): assert failure.value.retry_after == 90


def test_snapshot_bounds_do_not_return_a_truncated_authoritative_catalog(api, monkeypatch):
    api.extra_pages = True
    monkeypatch.setattr(providers, 'MAX_PAGES', 1)
    with pytest.raises(transport.ProviderError) as failure: providers.snapshot('google', 'files', GOOGLE_FILES, CREDS)
    assert failure.value.code == 'snapshot_too_large'


def test_missing_actual_google_etag_prevents_unconditional_write(api):
    api.no_etag = True
    with pytest.raises(transport.ProviderError) as failure:
        providers.write_item('google', 'files', GOOGLE_FILES, CREDS, {'name': 'notes.txt', 'bytes': b'Updated bytes'}, 'remote-file', 'version:1')
    assert failure.value.code == 'precondition_required'
    assert all(row[0] == 'GET' for row in api.requests)


@pytest.mark.parametrize('provider,config,field', [('google', GOOGLE_CAL, 'recurrence'), ('microsoft', GRAPH_CAL, 'recurrence')])
def test_recurring_events_fail_explicitly_without_lossy_flattening(api, provider, config, field):
    event = api.google_event if provider == 'google' else api.graph_event
    event[field] = ['RRULE:FREQ=WEEKLY']
    with pytest.raises(transport.ProviderError) as failure: providers.snapshot(provider, 'calendar', config, CREDS)
    assert failure.value.code == 'unsupported_recurrence'


def test_graph_meetings_are_read_only_to_prevent_unrequested_scheduling_changes(api):
    api.graph_event['attendees'] = [{'emailAddress': {'address': 'fixture@example.com'}}]
    assert providers.snapshot('microsoft', 'calendar', GRAPH_CAL, CREDS)['items'][0]['read_only']
    with pytest.raises(transport.ProviderError) as failure:
        providers.write_item('microsoft', 'calendar', GRAPH_CAL, CREDS, FIELDS, 'event-one', api.graph_event['@odata.etag'])
    assert failure.value.code == 'read_only_resource'


@pytest.mark.parametrize('etag', ['*', 'bare-value', '"injected\r\n"', '', None])
def test_wildcard_or_malformed_etag_cannot_turn_update_into_unconditional_overwrite(api, etag):
    with pytest.raises(transport.ProviderError) as error:
        providers.write_item('google', 'calendar', GOOGLE_CAL, CREDS, FIELDS, 'event-one', etag)
    assert error.value.code == 'precondition_required'
    assert api.requests == []


def test_absent_provider_revision_cannot_hide_future_remote_file_changes(api):
    api.google_rows = [{'id': 'file', 'name': 'notes.txt', 'mimeType': 'text/plain'}]
    with pytest.raises(transport.ProviderError) as error: providers.snapshot('google', 'files', GOOGLE_FILES, CREDS)
    assert error.value.code == 'precondition_unavailable'


def test_malformed_provider_values_never_become_raw_worker_exceptions(api):
    api.google_rows = [{'id': 'file', 'name': 'notes.txt', 'mimeType': 'text/plain', 'version': '1', 'size': 'private-value'}]
    with pytest.raises(transport.ProviderError) as error: providers.snapshot('google', 'files', GOOGLE_FILES, CREDS)
    assert error.value.code == 'invalid_response' and 'private-value' not in str(error.value)


def test_revoked_refresh_token_requires_reconnection_not_unbounded_retry(monkeypatch):
    monkeypatch.setattr(transport, 'request', lambda *args, **kwargs: transport.Response(400, {}, b'{"error":"invalid_grant","error_description":"private-provider-details"}'))
    with pytest.raises(transport.ProviderError) as error: providers.refresh_tokens('google', GOOGLE_FILES, 'old-token')
    assert error.value.code == 'reauthorization_required' and 'private-provider-details' not in str(error.value)


def test_dav_facade_accepts_encrypted_app_password_field(monkeypatch):
    from app.services import remote_dav
    captured = []
    monkeypatch.setattr(remote_dav, 'snapshot', lambda kind, config, credentials, cursor: captured.append(credentials) or {'items': [], 'cursor': None, 'complete': True})
    assert providers.snapshot('nextcloud', 'files', {'base_url': 'https://dav.example.com/files/'}, {'username': 'member', 'app_password': 'local-app-password'})['complete']
    assert captured[0]['password'] == 'local-app-password'


def test_google_drive_removal_uses_conditional_trash_over_actual_verified_https(tmp_path, monkeypatch):
    import select
    import socket
    import socketserver
    import ssl
    import subprocess
    from pathlib import Path
    key, certificate = tmp_path / 'key.pem', tmp_path / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(certificate), '-days', '1', '-subj', '/CN=www.googleapis.com', '-addext', 'subjectAltName=DNS:www.googleapis.com'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observed = {'requests': [], 'revision': 1, 'trashed': False, 'race': False}
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, status, value, etag=None):
            body = json.dumps(value).encode(); self.send_response(status)
            self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body)))
            if etag: self.send_header('ETag', etag)
            self.end_headers(); self.wfile.write(body)
        def receive(self):
            assert self.headers['Host'] == 'www.googleapis.com'
            assert self.headers['Authorization'] == 'Bearer fixture-access-token'
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            observed['requests'].append((self.command, self.path, dict(self.headers), body)); return body
        def do_GET(self):
            self.receive(); revision = observed['revision']
            metadata = {'id': 'remote-file', 'name': 'notes.txt', 'mimeType': 'text/plain', 'version': str(revision), 'parents': ['selected-folder']}
            if observed['race']: observed['revision'] += 1
            self.reply(200, metadata, '"google-' + str(revision) + '"')
        def do_PATCH(self):
            body = self.receive()
            assert urlsplit(self.path).path == '/drive/v3/files/remote-file'
            assert json.loads(body) == {'trashed': True}
            assert self.headers['Content-Type'] == 'application/json'
            if self.headers.get('If-Match') != '"google-' + str(observed['revision']) + '"': return self.reply(412, {'error': 'changed'})
            observed['trashed'] = True; self.reply(200, {'id': 'remote-file', 'trashed': True})
        def do_DELETE(self):
            self.receive(); self.reply(500, {'error': 'Permanent delete must never be used'})
    https = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key)
    https.socket = context.wrap_socket(https.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3); header = b''
            while b'\r\n\r\n' not in header:
                data = self.request.recv(4096)
                if not data: return
                header += data
                if len(header) > 8192: return
            assert header.split(b'\r\n', 1)[0] == b'CONNECT 8.8.8.8:443 HTTP/1.1'
            with socket.create_connection(('127.0.0.1', https.server_port), timeout=3) as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    while True:
                        ready, _, _ = select.select([self.request, upstream], [], [], 3)
                        if not ready: return
                        for source in ready:
                            block = source.recv(16384)
                            if not block: return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Proxy); proxy.daemon_threads = True
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (https, proxy)]
    for thread in threads: thread.start()
    trust = tmp_path / 'combined.pem'; original = ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(original).read_bytes() if original else b'') + b'\n' + certificate.read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE', str(trust))
    monkeypatch.setattr(transport.pinned, 'getproxies', lambda: {'https': 'http://127.0.0.1:' + str(proxy.server_address[1])})
    monkeypatch.setattr(transport.pinned, 'public_addresses', lambda host, timeout=5: ['8.8.8.8'])
    try:
        with pytest.raises(transport.ProviderError) as stale:
            providers.delete_item('google', 'files', GOOGLE_FILES, CREDS, 'remote-file', 'version:0')
        assert stale.value.code == 'conflict' and not any(row[0] == 'PATCH' for row in observed['requests'])
        providers.delete_item('google', 'files', GOOGLE_FILES, CREDS, 'remote-file', 'version:1')
        assert observed['trashed'] is True
        patch = [row for row in observed['requests'] if row[0] == 'PATCH'][0]
        assert patch[2]['If-Match'] == '"google-1"' and json.loads(patch[3]) == {'trashed': True}
        observed['trashed'], observed['race'] = False, True
        with pytest.raises(transport.ProviderError) as raced:
            providers.delete_item('google', 'files', GOOGLE_FILES, CREDS, 'remote-file', 'version:1')
        assert raced.value.code == 'conflict' and raced.value.status == 412 and observed['trashed'] is False
        assert all(row[0] != 'DELETE' for row in observed['requests'])
    finally:
        for server in (proxy, https): server.shutdown(); server.server_close()
        for thread in threads: thread.join(3)


@pytest.fixture
def file_read_https(tmp_path, monkeypatch):
    """Actual pinned TLS requests to both API and preauthorized content hosts."""
    import select
    import socket
    import socketserver
    import ssl
    import subprocess
    from pathlib import Path
    hosts = {'www.googleapis.com', 'graph.microsoft.com', 'files.fixture.1drv.com'}
    key, certificate = tmp_path / 'read-key.pem', tmp_path / 'read-cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                    '-out', str(certificate), '-days', '1', '-subj', '/CN=www.googleapis.com',
                    '-addext', 'subjectAltName=' + ','.join('DNS:' + host for host in sorted(hosts))],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = {'mode': 'stable', 'revision': 1, 'parent': 'selected-folder', 'requests': [], 'connects': []}
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, status, value, etag=None):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status); self.send_header('Content-Length', str(len(body)))
            if etag: self.send_header('ETag', etag)
            self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            host = self.headers['Host']; parsed = urlsplit(self.path)
            state['requests'].append((host, self.path, dict(self.headers)))
            assert host in hosts
            if host == 'files.fixture.1drv.com':
                assert self.headers.get('Authorization') is None
                assert self.headers.get('If-Match') is None
                assert parsed.path == '/content/file'
                return self.content()
            assert self.headers.get('Authorization') == 'Bearer fixture-access-token'
            if host == 'www.googleapis.com' and parse_qs(parsed.query).get('alt') == ['media']:
                assert self.headers.get('If-Match') == '"google-1"'
                if state['mode'] == 'precondition': return self.reply(412, {'error': 'File changed'})
                return self.content()
            revision = state['revision']
            if host == 'www.googleapis.com':
                assert parsed.path == '/drive/v3/files/remote-file'
                row = {'id': 'remote-file', 'name': 'notes.txt', 'mimeType': 'text/plain',
                       'version': str(revision), 'parents': [state['parent']]}
                return self.reply(200, row, '"google-' + str(revision) + '"')
            assert parsed.path == '/v1.0/drives/selected-drive/items/remote-file'
            row = {'id': 'remote-file', 'name': 'notes.txt', 'eTag': '"graph-' + str(revision) + '"',
                   'file': {'mimeType': 'text/plain'}, 'parentReference': {'id': state['parent']},
                   '@microsoft.graph.downloadUrl': 'https://files.fixture.1drv.com/content/file?temporary=opaque'}
            self.reply(200, row)
        def content(self):
            if state['mode'] == 'edit': state['revision'] += 1
            elif state['mode'] == 'move': state['parent'] = 'outside-selected-folder'
            self.reply(200, b'content-version-' + str(state['revision']).encode(), '"content-' + str(state['revision']) + '"')
    https = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key)
    https.socket = context.wrap_socket(https.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3); header = b''
            while b'\r\n\r\n' not in header:
                data = self.request.recv(4096)
                if not data: return
                header += data
                if len(header) > 8192: return
            line = header.split(b'\r\n', 1)[0]; state['connects'].append(line)
            assert line == b'CONNECT 8.8.8.8:443 HTTP/1.1'
            with socket.create_connection(('127.0.0.1', https.server_port), timeout=3) as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    while True:
                        ready, _, _ = select.select([self.request, upstream], [], [], 3)
                        if not ready: return
                        for source in ready:
                            block = source.recv(16384)
                            if not block: return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Proxy); proxy.daemon_threads = True
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (https, proxy)]
    for thread in threads: thread.start()
    trust = tmp_path / 'read-trust.pem'; inherited = ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(inherited).read_bytes() if inherited else b'') + b'\n' + certificate.read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE', str(trust))
    monkeypatch.setattr(transport.pinned, 'getproxies', lambda: {'https': 'http://127.0.0.1:' + str(proxy.server_address[1])})
    def addresses(host, timeout=5):
        assert host in hosts
        return ['8.8.8.8']
    monkeypatch.setattr(transport.pinned, 'public_addresses', addresses)
    try: yield state
    finally:
        for server in (proxy, https): server.shutdown(); server.server_close()
        for thread in threads: thread.join(3)


@pytest.mark.parametrize('provider,config', [('google', GOOGLE_FILES), ('microsoft', GRAPH_FILES)])
def test_verified_https_file_read_rechecks_revision_and_collection_without_forwarding_graph_token(file_read_https, provider, config):
    state = file_read_https
    stable = providers.read_item(provider, 'files', config, CREDS, {'id': 'remote-file'})
    assert stable['bytes'] == b'content-version-1'
    assert stable['etag'] == ('version:1' if provider == 'google' else '"graph-1"')
    metadata_host = 'www.googleapis.com' if provider == 'google' else 'graph.microsoft.com'
    metadata = [row for row in state['requests'] if row[0] == metadata_host and parse_qs(urlsplit(row[1]).query).get('alt') != ['media']]
    assert len(metadata) == 2
    state['mode'] = 'edit'
    with pytest.raises(transport.ProviderError) as changed:
        providers.read_item(provider, 'files', config, CREDS, {'id': 'remote-file'})
    assert changed.value.code == 'conflict' and changed.value.status == 412
    state.update(mode='move', revision=1, parent='selected-folder')
    with pytest.raises(transport.ProviderError) as moved:
        providers.read_item(provider, 'files', config, CREDS, {'id': 'remote-file'})
    assert moved.value.code == 'outside_collection'
    assert state['connects'] and all(line == b'CONNECT 8.8.8.8:443 HTTP/1.1' for line in state['connects'])
    downloads = [headers for host, _, headers in state['requests'] if host == 'files.fixture.1drv.com']
    assert all('Authorization' not in headers for headers in downloads)


def test_verified_https_google_content_uses_real_metadata_etag_and_honors_precondition_failure(file_read_https):
    file_read_https['mode'] = 'precondition'
    with pytest.raises(transport.ProviderError) as changed:
        providers.read_item('google', 'files', GOOGLE_FILES, CREDS, {'id': 'remote-file'})
    assert changed.value.code == 'conflict' and changed.value.status == 412
    content = [row for row in file_read_https['requests'] if parse_qs(urlsplit(row[1]).query).get('alt') == ['media']]
    assert len(content) == 1 and content[0][2]['If-Match'] == '"google-1"'
    assert len(file_read_https['requests']) == 2
