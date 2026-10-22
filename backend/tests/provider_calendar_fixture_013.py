"""Owned Google/Graph HTTPS protocol fixture, reached through a real CONNECT proxy.

The provider transport retains public-host validation, TLS hostname checking,
original Host/SNI, body limits, conditional requests and redirect refusal.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import socketserver
import ssl
import subprocess
import threading
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY
import pytest

from app.services import remote_transport as transport

UTC = timezone.utc
CLIENT = {'client_id': 'fixture-client', 'client_secret': 'fixture-secret', 'calendar_id': 'selected-calendar'}
CONFIG = {'google': CLIENT, 'microsoft': {**CLIENT, 'tenant_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}}
CREDS = {'access_token': 'owned-calendar-access-token'}
FIELDS = {'title': 'Studio planning', 'description': 'Selected channel agenda', 'location': 'North studio',
    'starts_at': '2026-10-23T07:00:00Z', 'ends_at': '2026-10-23T07:30:00Z', 'all_day': False,
    'timezone': 'Europe/Stockholm', 'reminder_minutes': 15, 'external_uid': None,
    'recurrence': {'frequency': 'weekly', 'interval': 1, 'count': 4}}


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


class CalendarState:
    def __init__(self):
        self.requests, self.connects, self.sni = [], [], []
        self.events = {'google': {}, 'microsoft': {}}
        self.instances = {'google': {}, 'microsoft': {}}
        self.cancelled = {'google': {}, 'microsoft': {}}
        self.version = 0
        self.paginate = False
        self.incremental_instance = None
        self.unsafe_next = None
        self.concurrent_instance = False
        self.partial_failure = False

    def versioned(self, provider, value):
        self.version += 1
        value['etag' if provider == 'google' else '@odata.etag'] = '"fixture-' + str(self.version) + '"'
        return value

    def normalize(self, provider, value):
        value = deepcopy(value)
        if provider == 'microsoft':
            for key in ['start', 'end']:
                point = value[key]
                parsed = datetime.fromisoformat(point['dateTime'].replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo(point['timeZone']))
                value.setdefault('originalStartTimeZone', point['timeZone'])
                value[key] = {'dateTime': parsed.astimezone(UTC).replace(tzinfo=None).isoformat(), 'timeZone': 'UTC'}
            value['body'] = {'contentType': 'text', 'content': value.get('body', {}).get('content', '')}
        return value

    def create(self, provider, value, identity=None):
        value = self.normalize(provider, value)
        value['id'] = identity or value.get('id') or 'graph-event-' + str(self.version + 1)
        value.setdefault('organizer', {'email': 'owner@example.com', 'self': True} if provider == 'google' else {'emailAddress': {'address': 'owner@example.com'}})
        if provider == 'microsoft':
            value.setdefault('isOrganizer', True)
            value['type'] = 'seriesMaster' if value.get('recurrence') else 'singleInstance'
        self.versioned(provider, value)
        self.events[provider][value['id']] = value
        self.instances[provider][value['id']] = {}
        self.cancelled[provider][value['id']] = {}
        if value.get('recurrence'):
            self.generate(provider, value)
        return deepcopy(value)

    def generate(self, provider, event):
        if provider == 'google':
            parts = dict(part.split('=') for part in event['recurrence'][0][6:].split(';'))
            frequency = parts['FREQ']; interval = int(parts.get('INTERVAL', 1)); count = int(parts['COUNT'])
            zone = ZoneInfo(event['start'].get('timeZone', 'UTC'))
            all_day = 'date' in event['start']
            if all_day:
                start = datetime.fromisoformat(event['start']['date']).replace(tzinfo=zone)
                duration = datetime.fromisoformat(event['end']['date']) - datetime.fromisoformat(event['start']['date'])
            else:
                start = stamp(event['start']['dateTime']).astimezone(zone)
                duration = stamp(event['end']['dateTime']) - stamp(event['start']['dateTime'])
        else:
            rule = event['recurrence']; pattern, span = rule['pattern'], rule['range']
            frequency = {'daily': 'DAILY', 'weekly': 'WEEKLY', 'absoluteMonthly': 'MONTHLY'}[pattern['type']]
            interval, count = pattern['interval'], span['numberOfOccurrences']
            zone = ZoneInfo(span['recurrenceTimeZone']); all_day = bool(event.get('isAllDay'))
            start = stamp(event['start']['dateTime'] + 'Z').astimezone(zone)
            duration = stamp(event['end']['dateTime'] + 'Z') - stamp(event['start']['dateTime'] + 'Z')
        for index, occurrence in enumerate(rrule({'DAILY': DAILY, 'WEEKLY': WEEKLY, 'MONTHLY': MONTHLY}[frequency], dtstart=start, interval=interval, count=count)):
            value = deepcopy(event); value.pop('recurrence', None)
            value['id'] = event['id'] + '-instance-' + str(index)
            if provider == 'google':
                value.update(recurringEventId=event['id'], originalStartTime={'dateTime': iso(occurrence)})
                value['start'] = {'dateTime': iso(occurrence), 'timeZone': str(zone)}
                value['end'] = {'dateTime': iso(occurrence + duration), 'timeZone': str(zone)}
            else:
                value.update(type='occurrence', seriesMasterId=event['id'], originalStart=iso(occurrence), occurrenceId='opaque-occurrence-' + str(index))
                value['start'] = {'dateTime': occurrence.astimezone(UTC).replace(tzinfo=None).isoformat(), 'timeZone': 'UTC'}
                value['end'] = {'dateTime': (occurrence + duration).astimezone(UTC).replace(tzinfo=None).isoformat(), 'timeZone': 'UTC'}
            if provider == 'google' and all_day:
                value['originalStartTime'] = {'date': occurrence.date().isoformat()}
                value['start'] = {'date': occurrence.date().isoformat(), 'timeZone': str(zone)}
                value['end'] = {'date': (occurrence + duration).date().isoformat(), 'timeZone': str(zone)}
            self.versioned(provider, value)
            self.instances[provider][event['id']][occurrence.date().isoformat() if all_day else iso(occurrence)] = value

    def find(self, provider, identity):
        if identity in self.events[provider]:
            return self.events[provider][identity], None
        for parent, rows in self.instances[provider].items():
            for original, value in rows.items():
                if value['id'] == identity:
                    return value, (parent, original)
        return None, None


@pytest.fixture
def calendar_https(tmp_path, monkeypatch):
    key, certificate = tmp_path / 'calendar-key.pem', tmp_path / 'calendar-cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(certificate), '-days', '1', '-subj', '/CN=www.googleapis.com', '-addext', 'subjectAltName=DNS:www.googleapis.com,DNS:graph.microsoft.com'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = CalendarState()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def receive(self):
            assert self.headers['Host'] in {'www.googleapis.com', 'graph.microsoft.com'}
            assert self.headers['Authorization'] == 'Bearer owned-calendar-access-token'
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state.requests.append((self.command, self.path, dict(self.headers), raw))
            path = urlsplit(self.path)
            provider = 'google' if self.headers['Host'] == 'www.googleapis.com' else 'microsoft'
            prefix = '/calendar/v3/calendars/selected-calendar/events' if provider == 'google' else '/v1.0/me/calendars/selected-calendar/events'
            assert path.path == prefix or path.path.startswith(prefix + '/'), 'An operation escaped its selected calendar'
            identity = unquote(path.path[len(prefix):].lstrip('/'))
            return provider, identity, parse_qs(path.query), json.loads(raw) if raw else None
        def reply(self, status, body=None):
            raw = b'' if body is None else json.dumps(body).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def condition(self, provider, value):
            if self.headers.get('If-Match') != value.get('etag' if provider == 'google' else '@odata.etag'):
                self.reply(412, {'error': 'Changed conditional resource'}); return False
            return True
        def do_GET(self):
            provider, identity, query, _ = self.receive()
            if not identity:
                values = list(state.events[provider].values())
                if provider == 'google' and 'syncToken' in query and state.incremental_instance:
                    values = [state.incremental_instance]
                payload = {'items' if provider == 'google' else 'value': deepcopy(values)}
                if provider == 'google': payload['nextSyncToken'] = 'next-cursor'
                if state.unsafe_next: payload['@odata.nextLink'] = state.unsafe_next
                return self.reply(200, payload)
            if identity.endswith('/instances'):
                parent = identity[:-10]; values = list(state.instances[provider][parent].values())
                if provider == 'microsoft':
                    low, high = stamp(query['startDateTime'][0]), stamp(query['endDateTime'][0])
                    values = [value for value in values if low <= stamp(value['start']['dateTime'] + 'Z') < high]
                if state.paginate and 'pageToken' not in query and '$skiptoken' not in query and len(values) > 1:
                    values = values[:1]
                    next_page = True
                else:
                    if state.paginate and ('pageToken' in query or '$skiptoken' in query): values = values[1:]
                    next_page = False
                payload = {'items' if provider == 'google' else 'value': deepcopy(values)}
                if next_page:
                    if provider == 'google': payload['nextPageToken'] = 'page2'
                    else:
                        payload['@odata.nextLink'] = 'https://graph.microsoft.com' + urlsplit(self.path).path + '?' + urlencode({**{key: value[0] for key, value in query.items()}, '$skiptoken': 'page2'})
                return self.reply(200, payload)
            value, parent = state.find(provider, identity)
            if value is None: return self.reply(404, {'error': 'Missing fixture event'})
            value = deepcopy(value)
            if provider == 'microsoft' and '$expand' in query:
                value['cancelledOccurrences'] = list(state.cancelled[provider][identity].values())
                value['exceptionOccurrences'] = [deepcopy(row) for row in state.instances[provider][identity].values() if row.get('type') == 'exception']
            return self.reply(200, value)
        def do_POST(self):
            provider, identity, query, body = self.receive()
            if not identity:
                if provider == 'google' and body.get('id') in state.events[provider]: return self.reply(409, {'error': 'Existing deterministic event'})
                if provider == 'microsoft':
                    existing = next((row for row in state.events[provider].values() if row.get('transactionId') == body.get('transactionId')), None)
                    if existing: return self.reply(200, deepcopy(existing))
                assert self.headers.get('If-None-Match') == '*'
                return self.reply(201, state.create(provider, body))
            identity, action = identity.rsplit('/', 1)
            value, _ = state.find(provider, identity)
            if value is None: return self.reply(404, {})
            if not self.condition(provider, value): return
            if action == 'cancel':
                assert value.get('isOrganizer') is True
                state.events[provider].pop(identity); return self.reply(202)
            assert action in {'accept', 'decline', 'tentativelyAccept'} and value.get('isOrganizer') is False and body == {'sendResponse': True}
            value['responseStatus'] = {'response': {'accept': 'accepted', 'decline': 'declined', 'tentativelyAccept': 'tentativelyAccepted'}[action]}
            state.versioned(provider, value); return self.reply(202)
        def do_PATCH(self):
            provider, identity, query, body = self.receive(); value, parent = state.find(provider, identity)
            if value is None: return self.reply(404, {})
            if parent and state.concurrent_instance:
                state.concurrent_instance = False; state.versioned(provider, value)
            if not self.condition(provider, value): return
            if state.partial_failure and parent: return self.reply(503, {'error': 'Owned fixture interruption'})
            if body.get('attendeesOmitted'):
                assert provider == 'google' and set(body) == {'attendeesOmitted', 'attendees'} and len(body['attendees']) == 1
                own = next(row for row in value['attendees'] if row.get('self'))
                assert body['attendees'][0]['email'] == own['email']
                own['responseStatus'] = body['attendees'][0]['responseStatus']
            else:
                if provider == 'microsoft' and 'start' in body:
                    body = state.normalize(provider, body)
                if not parent:
                    for instance in state.instances[provider].get(identity, {}).values():
                        for field in ['attendees', 'summary', 'subject', 'description', 'body', 'location', 'reminders', 'isReminderOn', 'reminderMinutesBeforeStart']:
                            if field in body and instance.get(field) == value.get(field):
                                instance[field] = deepcopy(body[field])
                        state.versioned(provider, instance)
                value.update(body)
                if parent and provider == 'microsoft': value['type'] = 'exception'
            state.versioned(provider, value)
            return self.reply(200, deepcopy(value))
        def do_DELETE(self):
            provider, identity, query, _ = self.receive(); value, parent = state.find(provider, identity)
            if value is None: return self.reply(404, {})
            if not self.condition(provider, value): return
            if parent:
                if provider == 'google': value['status'] = 'cancelled'; state.versioned(provider, value)
                else:
                    master, original = parent; state.instances[provider][master].pop(original); state.cancelled[provider][master][original] = 'opaque-cancelled-' + str(state.version)
            else: state.events[provider].pop(identity)
            return self.reply(204)
    https = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key)
    context.set_servername_callback(lambda _sock, name, _ctx: state.sni.append(name))
    https.socket = context.wrap_socket(https.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5); headers = b''
            while b'\r\n\r\n' not in headers:
                block = self.request.recv(4096)
                if not block: return
                headers += block
                if len(headers) > 8192: return
            first = headers.split(b'\r\n', 1)[0]; state.connects.append(first)
            assert first == b'CONNECT 8.8.8.8:443 HTTP/1.1'
            with socket.create_connection(('127.0.0.1', https.server_port), timeout=5) as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    while True:
                        readable, _, _ = select.select([self.request, upstream], [], [], 5)
                        if not readable: return
                        for source in readable:
                            block = source.recv(65536)
                            if not block: return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Proxy); proxy.daemon_threads = True
    servers = [https, proxy]; threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads: thread.start()
    trust = tmp_path / 'combined-ca.pem'; original = ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(original).read_bytes() if original else b'') + b'\n' + certificate.read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE', str(trust))
    monkeypatch.setattr(transport.pinned, 'getproxies', lambda: {'https': 'http://127.0.0.1:' + str(proxy.server_address[1])})
    def addresses(host, timeout=5):
        assert host in {'www.googleapis.com', 'graph.microsoft.com'}
        return ['8.8.8.8']
    monkeypatch.setattr(transport.pinned, 'public_addresses', addresses)
    try: yield state
    finally:
        for server in reversed(servers): server.shutdown(); server.server_close()
        for thread in threads: thread.join(3)
