"""Real local DAV protocol fixtures; production destination rules remain intact."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import pytest

from app.services import remote_dav as dav
from app.services.remote_transport import ProviderError

CREDS = {'username': 'fixture-member', 'password': 'fixture-app-password'}
CONFIG = {'base_url': 'https://dav.example.com/selected/', 'timezone': 'Europe/Stockholm'}
EVENT = {'title': 'A private review', 'description': 'Line one\nLine two, with; text\\', 'location': 'Studio',
         'starts_at': '2026-10-25T00:30:00Z', 'ends_at': '2026-10-25T01:00:00Z',
         'all_day': False, 'timezone': 'Europe/Stockholm', 'reminder_minutes': 15, 'external_uid': 'fixture-event@calendar'}


@pytest.fixture
def server(monkeypatch):
    class State:
        files = {}; revisions = 0; deleted = []; seen = []; no_put_etag = False
    state = State()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def send(self, status, data=b'', headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def receive(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state.seen.append((self.command, self.path, dict(self.headers), body))
            assert self.headers.get('Authorization', '').startswith('Basic ')
            return body
        def do_GET(self):
            self.receive()
            if self.path not in state.files: return self.send(404)
            data, etag, mime = state.files[self.path]
            if self.headers.get('If-Match') and self.headers['If-Match'] != etag: return self.send(412)
            self.send(200, data, {'ETag': etag, 'Content-Type': mime})
        def do_PUT(self):
            body = self.receive(); old = state.files.get(self.path)
            if self.headers.get('If-None-Match') == '*' and old: return self.send(412)
            if self.headers.get('If-Match') and (not old or old[1] != self.headers['If-Match']): return self.send(412)
            state.revisions += 1; etag = '"revision-' + str(state.revisions) + '"'
            state.files[self.path] = (body, etag, self.headers.get('Content-Type', 'application/octet-stream'))
            self.send(204 if old else 201, headers={} if state.no_put_etag else {'ETag': etag})
        def do_DELETE(self):
            self.receive(); old = state.files.get(self.path)
            if not old: return self.send(404)
            if self.headers.get('If-Match') != old[1]: return self.send(412)
            del state.files[self.path]; state.deleted.append(self.path); state.revisions += 1
            self.send(204)
        def do_PROPFIND(self): self.multistatus(False)
        def do_REPORT(self): self.multistatus(True)
        def multistatus(self, report):
            body = self.receive()
            if b'expired-token' in body: return self.send(409)
            doc = ET.Element(dav.DAV + 'multistatus')
            ET.SubElement(doc, dav.DAV + 'sync-token').text = 'urn:fixture:' + str(state.revisions)
            if not report:
                row = ET.SubElement(doc, dav.DAV + 'response'); ET.SubElement(row, dav.DAV + 'href').text = '/selected/'
                status = ET.SubElement(row, dav.DAV + 'propstat'); ET.SubElement(status, dav.DAV + 'status').text = 'HTTP/1.1 200 OK'
                prop = ET.SubElement(status, dav.DAV + 'prop'); resource = ET.SubElement(prop, dav.DAV + 'resourcetype'); ET.SubElement(resource, dav.DAV + 'collection')
            for path, (content, etag, mime) in state.files.items():
                row = ET.SubElement(doc, dav.DAV + 'response'); ET.SubElement(row, dav.DAV + 'href').text = path
                status = ET.SubElement(row, dav.DAV + 'propstat'); ET.SubElement(status, dav.DAV + 'status').text = 'HTTP/1.1 200 OK'
                prop = ET.SubElement(status, dav.DAV + 'prop')
                ET.SubElement(prop, dav.DAV + 'getetag').text = etag
                ET.SubElement(prop, dav.DAV + 'displayname').text = unquote(path.rsplit('/', 1)[-1])
                ET.SubElement(prop, dav.DAV + 'getcontenttype').text = mime
                ET.SubElement(prop, dav.DAV + 'getcontentlength').text = str(len(content))
                if report and mime.startswith('text/calendar'):
                    ET.SubElement(prop, dav.CAL + 'calendar-data').text = content.decode()
            if report and b'sync-token' in body:
                for path in state.deleted:
                    row = ET.SubElement(doc, dav.DAV + 'response'); ET.SubElement(row, dav.DAV + 'href').text = path
                    ET.SubElement(row, dav.DAV + 'status').text = 'HTTP/1.1 404 Not Found'
            self.send(207, ET.tostring(doc), {'Content-Type': 'application/xml'})
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    def local(method, url, headers=None, body=None, max_bytes=1024 * 1024, timeout=12):
        # Exact fixture-origin replacement; production has no HTTP/private bypass.
        assert urlsplit(url).netloc == 'dav.example.com'
        assert urlsplit(url).path.startswith('/selected/')
        req = Request('http://127.0.0.1:' + str(httpd.server_port) + urlsplit(url).path, data=body,
                      method=method, headers=headers or {})
        try: response = urlopen(req, timeout=timeout)
        except HTTPError as exc: response = exc
        with response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes: raise ProviderError('resource_limit')
            return SimpleNamespace(status=response.status, headers={k.lower(): v for k, v in response.headers.items()}, body=data)
    monkeypatch.setattr(dav, 'request', local)
    yield state
    httpd.shutdown(); httpd.server_close(); thread.join(timeout=3)


def test_real_webdav_create_snapshot_read_conditional_update_delete_and_incremental_tombstone(server):
    created = dav.write_item('files', CONFIG, CREDS, {'name': 'project notes.txt', 'bytes': b'first bytes', 'mime_type': 'text/plain'})
    assert created['id'] == 'project%20notes.txt'
    assert dav.snapshot('files', CONFIG, CREDS)['items'][0]['name'] == 'project notes.txt'
    assert dav.read_item('files', CONFIG, CREDS, created)['bytes'] == b'first bytes'
    updated = dav.write_item('files', CONFIG, CREDS, {'name': 'project notes.txt', 'bytes': b'second bytes'}, created['id'], created['etag'])
    assert updated['etag'] != created['etag']
    with pytest.raises(ProviderError) as failure:
        dav.write_item('files', CONFIG, CREDS, {'name': 'project notes.txt', 'bytes': b'overwrite'}, created['id'], created['etag'])
    assert failure.value.code == 'conflict'
    cursor = dav.snapshot('files', CONFIG, CREDS)['cursor']
    dav.delete_item('files', CONFIG, CREDS, created['id'], updated['etag'])
    changes = dav.snapshot('files', CONFIG, CREDS, cursor)
    assert changes['items'] == [{'id': created['id'], 'deleted': True, 'etag': None}]
    assert changes['complete'] is False
    assert any(method == 'PROPFIND' and headers['Depth'] == '1' for method, _, headers, _ in server.seen)
    assert any(method == 'REPORT' for method, *_ in server.seen)


def test_real_webdav_stale_cursor_falls_back_to_complete_non_destructive_scan(server):
    dav.write_item('files', CONFIG, CREDS, {'name': 'first.txt', 'bytes': b'body'})
    snap = dav.snapshot('files', CONFIG, CREDS, 'expired-token')
    assert snap['complete'] is True and len(snap['items']) == 1
    assert [request[0] for request in server.seen[-2:]] == ['REPORT', 'PROPFIND']


def test_real_caldav_roundtrip_uid_etag_reminder_and_privacy_metadata_preserved(server):
    created = dav.write_item('calendar', CONFIG, CREDS, {'name': 'review.ics', 'fields': EVENT})
    initial = dav.read_item('calendar', CONFIG, CREDS, created)
    assert initial['fields'] == EVENT
    assert initial['fields']['external_uid'] == EVENT['external_uid']
    assert initial['fields']['description'] == EVENT['description']
    assert initial['fields']['reminder_minutes'] == 15
    content, etag, mime = server.files['/selected/review.ics']
    server.files['/selected/review.ics'] = (content.replace(b'END:VEVENT', b'CLASS:PRIVATE\r\nTRANSP:TRANSPARENT\r\nSTATUS:CONFIRMED\r\nEND:VEVENT'), etag, mime)
    snap = dav.snapshot('calendar', CONFIG, CREDS)
    assert snap['items'][0]['fields']['title'] == EVENT['title']
    assert snap['items'][0]['read_only'] is False
    updated = dav.write_item('calendar', CONFIG, CREDS, {'fields': {**EVENT, 'title': 'Changed', 'external_uid': 'attempted-new-uid'}}, created['id'], created['etag'])
    after = dav.read_item('calendar', CONFIG, CREDS, updated)['fields']
    assert after['title'] == 'Changed' and after['external_uid'] == EVENT['external_uid']
    assert b'CLASS:PRIVATE' in server.files['/selected/review.ics'][0]
    assert b'TRANSP:TRANSPARENT' in server.files['/selected/review.ics'][0]
    dav.delete_item('calendar', CONFIG, CREDS, updated['id'], updated['etag'])
    assert not server.files


def test_real_caldav_unsupported_recurrence_is_read_only_and_never_rewritten(server):
    recurring = dav.serialize_calendar(EVENT).replace(b'END:VEVENT', b'RRULE:FREQ=WEEKLY\r\nEND:VEVENT')
    server.files['/selected/recurring.ics'] = (recurring, '"one"', 'text/calendar')
    snapshot = dav.snapshot('calendar', CONFIG, CREDS)
    assert snapshot['items'][0]['read_only'] is True
    assert snapshot['items'][0]['unsupported_reason'] == 'unsupported_event'
    with pytest.raises(ProviderError):
        dav.write_item('calendar', CONFIG, CREDS, {'fields': EVENT}, 'recurring.ics', '"one"')
    assert server.files['/selected/recurring.ics'][0] == recurring


def test_real_webdav_missing_put_etag_gets_exact_written_body_before_acceptance(server):
    server.no_put_etag = True
    created = dav.write_item('files', CONFIG, CREDS, {'name': 'bytes.txt', 'bytes': b'unchanged'})
    assert created['etag']
    assert [r[0] for r in server.seen] == ['PUT', 'GET']


@pytest.mark.parametrize('remote_id', ['../outside', 'https://evil.example/x', '%2e%2e', '%252e%252e', 'nested/file', 'name%2Ffile', 'name?access_token=secret', '\\outside', '\x00bad'])
def test_remote_ids_cannot_escape_selected_collection(remote_id):
    with pytest.raises(ProviderError): dav._item_url(CONFIG, remote_id)


@pytest.mark.parametrize('href', ['https://evil.example/item', 'https://dav.example.com/other/item', '/selected/../other/item', '/selected/%2e%2e/item', '/selected/name?token=secret'])
def test_remote_hrefs_cannot_change_destination_or_collection(href):
    with pytest.raises(ProviderError): dav._href_id(CONFIG, href)


@pytest.mark.parametrize('base', ['http://dav.example.com/x/', 'https://127.0.0.1/x/', 'https://user:secret@dav.example.com/x/', 'https://dav.example.com/x/?token=secret', 'https://dav.example.com/x/../other/', 'https://dav.example.com:8443/x/'])
def test_invalid_config_rejected_before_credentials_are_sent(base):
    with pytest.raises(ProviderError): dav.validate_dav_config('files', {'base_url': base})


@pytest.mark.parametrize('body', [b'<!DOCTYPE x [<!ENTITY y "secret">]><multistatus/>', b'<!ENTITY remote SYSTEM "file:///etc/passwd"><multistatus/>', b'<wrong/>', b'<unclosed>'])
def test_xml_entities_and_malformed_documents_rejected(body):
    with pytest.raises(ProviderError): dav._xml(body)


def test_unsafe_multistatus_href_never_reaches_download(monkeypatch):
    body = b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>https://private.example/file</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:getetag>etag</d:getetag></d:prop></d:propstat></d:response></d:multistatus>'
    calls = []
    monkeypatch.setattr(dav, 'request', lambda *args, **kwargs: calls.append(args[0]) or SimpleNamespace(status=207, headers={}, body=body))
    with pytest.raises(ProviderError): dav.snapshot('files', CONFIG, CREDS)
    assert calls == ['PROPFIND']


def test_calendar_all_day_unicode_folding_escape_and_dst():
    all_day = {**EVENT, 'title': 'Å' * 120, 'all_day': True, 'starts_at': '2026-10-25', 'ends_at': '2026-10-27'}
    encoded = dav.serialize_calendar(all_day)
    assert all(len(line) <= 75 for line in encoded.split(b'\r\n'))
    parsed = dav.parse_calendar(encoded, 'Europe/Stockholm')
    assert parsed == all_day
    wall = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:dst\r\nSUMMARY:Meeting\r\nDTSTART;TZID=Europe/Stockholm:20261025T023000\r\nDTEND;TZID=Europe/Stockholm:20261025T024500\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
    parsed = dav.parse_calendar(wall)
    assert parsed['starts_at'] == '2026-10-25T00:30:00Z'
    assert parsed['timezone'] == 'Europe/Stockholm'
    with pytest.raises(ProviderError): dav.parse_calendar(wall.replace(b'20261025T02', b'20260329T02'))


@pytest.mark.parametrize('extra', ['RRULE:FREQ=DAILY', 'RDATE:20260101T000000Z', 'EXDATE:20260101T000000Z', 'RECURRENCE-ID:20260101T000000Z', 'ATTENDEE:mailto:person@example.com', 'ORGANIZER:mailto:person@example.com', 'URL:https://calendar.example.com', 'STATUS:CANCELLED'])
def test_calendar_constructs_without_lossless_native_representation_are_explicitly_rejected(extra):
    encoded = dav.serialize_calendar(EVENT).replace(b'END:VEVENT', extra.encode() + b'\r\nEND:VEVENT')
    with pytest.raises(ProviderError) as error: dav.parse_calendar(encoded)
    assert error.value.code == 'unsupported_event'


def test_calendar_active_alarm_and_duplicate_properties_are_rejected():
    encoded = dav.serialize_calendar(EVENT)
    for bad in [encoded.replace(b'ACTION:DISPLAY', b'ACTION:EMAIL'), encoded.replace(b'END:VEVENT', b'SUMMARY:duplicate\r\nEND:VEVENT'), encoded.replace(b'TRIGGER:-PT15M', b'TRIGGER;RELATED=END:-PT15M')]:
        with pytest.raises(ProviderError): dav.parse_calendar(bad)


def test_delete_and_update_require_etag_before_any_request(monkeypatch):
    monkeypatch.setattr(dav, 'request', lambda *args, **kwargs: pytest.fail('Missing condition reached network'))
    with pytest.raises(ProviderError): dav.delete_item('files', CONFIG, CREDS, 'notes.txt')
    with pytest.raises(ProviderError): dav.write_item('files', CONFIG, CREDS, {'name': 'notes.txt', 'bytes': b'body'}, 'notes.txt')


def test_deterministic_provider_key_recovers_file_create_without_duplicate(server):
    key = 'fc-deadbeef0123456789abcdef01234567'
    resource = {'name': 'shared.txt', 'bytes': b'bytes', 'remote_key': key}
    created = dav.write_item('files', CONFIG, CREDS, resource)
    assert created['name'] == 'shared.txt' and created['remote_key'] == key
    snapshot = dav.snapshot('files', CONFIG, CREDS)
    assert snapshot['items'][0]['remote_key'] == key
    assert snapshot['items'][0]['id'] == created['id']
    with pytest.raises(ProviderError) as exc:
        dav.write_item('files', CONFIG, CREDS, resource)
    assert exc.value.code == 'conflict' and len(server.files) == 1


def test_deterministic_calendar_filename_and_uid_recover_pending_create(server):
    key = 'fc-0123456789abcdef0123456789abcdef'
    created = dav.write_item('calendar', CONFIG, CREDS, {'name': 'review.ics', 'fields': {**EVENT, 'external_uid': None}, 'remote_key': key})
    item = dav.snapshot('calendar', CONFIG, CREDS)['items'][0]
    assert item['remote_key'] == key
    assert item['fields']['external_uid'] == key + '@freecollab'
    with pytest.raises(ProviderError):
        dav.write_item('calendar', CONFIG, CREDS, {'name': 'review.ics', 'fields': EVENT, 'remote_key': key})
    assert len(server.files) == 1


@pytest.mark.parametrize('etag', ['*', 'W/"weak"', 'unquoted', '"one"\r\nInjected:yes'])
def test_wildcard_weak_or_unquoted_write_etag_never_reaches_network(monkeypatch, etag):
    monkeypatch.setattr(dav, 'request', lambda *args, **kwargs: pytest.fail('Unsafe condition reached network'))
    with pytest.raises(ProviderError): dav.delete_item('files', CONFIG, CREDS, 'notes.txt', etag)


def test_utf16_entity_declaration_is_rejected_before_xml_parser():
    body = '<!DOCTYPE x [<!ENTITY y "private">]><multistatus/>'.encode('utf-16')
    with pytest.raises(ProviderError): dav._xml(body)


def test_calendar_without_filename_uses_deterministic_key_filename(server):
    key = 'fc-abcdef0123456789abcdef0123456789'
    resource = {'fields': {**EVENT, 'external_uid': None}, 'remote_key': key}
    created = dav.write_item('calendar', CONFIG, CREDS, resource)
    assert created['id'] == key + '--event.ics'
    with pytest.raises(ProviderError): dav.write_item('calendar', CONFIG, CREDS, resource)
    assert len(server.files) == 1


def test_real_worker_flat_calendar_resource_retains_transport_identity_without_schema_leak(server):
    from app.services import remote_providers
    key = 'fc-worker-calendar-mapping'
    credentials = {'username': CREDS['username'], 'app_password': CREDS['password']}
    resource = {**EVENT, 'external_uid': None, 'remote_key': key}
    created = remote_providers.write_item('caldav', 'calendar', CONFIG, credentials, resource)
    assert created['remote_key'] == key
    snapshot = remote_providers.snapshot('caldav', 'calendar', CONFIG, credentials)
    assert snapshot['items'][0]['remote_key'] == key
    read = remote_providers.read_item('caldav', 'calendar', CONFIG, credentials, created)
    assert read['fields']['title'] == EVENT['title']
    assert read['fields']['external_uid'] == key + '@freecollab'
    updated = remote_providers.write_item('caldav', 'calendar', CONFIG, credentials,
        {**resource, 'title': 'Updated through the real worker contract'}, created['id'], created['etag'])
    assert updated['id'] == created['id']
    after = remote_providers.read_item('caldav', 'calendar', CONFIG, credentials, updated)
    assert after['fields']['title'] == 'Updated through the real worker contract'
    assert after['fields']['external_uid'] == key + '@freecollab'
    payload = server.files['/selected/' + created['id']][0]
    assert b'remote_key' not in payload and b'Updated through the real worker contract' in payload


def test_flat_calendar_unknown_fields_still_fail_closed(server):
    with pytest.raises(ProviderError) as error:
        dav.write_item('calendar', CONFIG, CREDS, {**EVENT, 'remote_key': 'fc-calendar', 'unsupported_payload': 'value'})
    assert error.value.code == 'invalid_event'
    assert not server.files
