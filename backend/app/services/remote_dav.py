"""Bounded WebDAV/CalDAV adapters for an explicitly selected customer collection.

IDs are collection-relative, conditional writes prevent lost remote changes, and
unsupported calendar constructs fail closed instead of flattening meetings or
recurrences. HTTP destination validation/pinning lives in remote_transport.
"""
import base64
from datetime import datetime, timezone
import re
import secrets
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from . import oidc_transport
from .integration_content import CalendarFields
from .remote_transport import ProviderError, check_response, request

DAV = '{DAV:}'
CAL = '{urn:ietf:params:xml:ns:caldav}'
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_CALENDAR_BYTES = 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ITEMS = 1000


def validate_dav_config(kind, config):
    if kind not in {'files', 'calendar'} or not isinstance(config, dict):
        raise ProviderError('invalid_config')
    try:
        value = config['base_url']
        parsed, hostname = oidc_transport.normalize_url(value)
        if parsed.query or not parsed.path.startswith('/'):
            raise ValueError()
        decoded = unquote(parsed.path, errors='strict')
        if any(part in {'.', '..'} for part in decoded.split('/')) or '\\' in decoded or '%' in decoded:
            raise ValueError()
        zone = config.get('timezone', 'UTC')
        ZoneInfo(zone)
        authority = '[' + hostname + ']' if ':' in hostname else hostname
        base = urlunsplit((parsed.scheme, authority, parsed.path.rstrip('/') + '/', '', ''))
        return {**config, 'base_url': base, 'timezone': zone}
    except (KeyError, ValueError, TypeError, oidc_transport.TransportError):
        raise ProviderError('invalid_config') from None


# Parent facade also imports this conventional name.
validate_config = validate_dav_config


def _headers(credentials, extra=None):
    if (not isinstance(credentials, dict) or not isinstance(credentials.get('username'), str)
            or not isinstance(credentials.get('password'), str) or not credentials['username']
            or not credentials['password'] or len(credentials['username']) > 512 or len(credentials['password']) > 4096 or ':' in credentials['username']
            or any(ord(ch) < 32 for ch in credentials['username'] + credentials['password'])):
        raise ProviderError('credentials_required')
    auth = base64.b64encode((credentials['username'] + ':' + credentials['password']).encode()).decode()
    return {'Authorization': 'Basic ' + auth, **(extra or {})}


def _item_url(config, item_id):
    if not isinstance(item_id, str) or not item_id or len(item_id) > 2048:
        raise ProviderError('invalid_resource')
    try:
        decoded = unquote(item_id, errors='strict')
    except UnicodeError:
        raise ProviderError('invalid_resource') from None
    # Only direct children are synchronized. Nested collections require their own
    # explicit channel grant; neither remote hrefs nor user IDs can escape it.
    if (decoded in {'.', '..'} or any(ch in decoded for ch in '/\\%?#')
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)):
        raise ProviderError('invalid_resource')
    return config['base_url'] + quote(decoded, safe='-._~!$&\'()+,;=@')


def _href_id(config, href):
    if not isinstance(href, str) or any(ord(ch) < 33 or ord(ch) == 127 for ch in href):
        raise ProviderError('invalid_response')
    try:
        base = urlsplit(config['base_url'])
        target = urlsplit(urljoin(config['base_url'], href))
        same_origin = (target.scheme, target.hostname, target.port or 443) == (base.scheme, base.hostname, base.port or 443)
    except ValueError:
        raise ProviderError('invalid_response') from None
    if (not same_origin or target.username is not None or target.password is not None or target.query or target.fragment
            or not target.path.startswith(base.path)):
        raise ProviderError('invalid_response')
    relative = target.path[len(base.path):]
    if not relative:
        return None
    # Collections are skipped by the caller before this point.
    _item_url(config, relative)
    return quote(unquote(relative), safe='-._~!$&\'()+,;=@')


def _xml(body):
    if not isinstance(body, bytes) or len(body) > MAX_XML_BYTES:
        raise ProviderError('invalid_response')
    # ElementTree does not expand external entities, but internal entities and
    # oversized nested documents are rejected before parsing too.
    if b'\x00' in body or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', body, re.I):
        raise ProviderError('invalid_response')
    try:
        root = ET.fromstring(body)
        if root.tag != DAV + 'multistatus':
            raise ValueError()
        stack = [(root, 0)]
        count = 0
        while stack:
            node, depth = stack.pop(); count += 1
            if depth > 20 or count > 20000:
                raise ValueError()
            stack.extend((child, depth + 1) for child in node)
        return root
    except (ET.ParseError, ValueError, RecursionError):
        raise ProviderError('invalid_response') from None


def _status(node):
    text = node.findtext(DAV + 'status') or ''
    match = re.fullmatch(r'HTTP/\d(?:\.\d)? (\d{3})(?: .*)?', text)
    return int(match[1]) if match else None


def _props(response):
    result = {}
    for status in response.findall(DAV + 'propstat'):
        if _status(status) == 200:
            for prop in status.findall(DAV + 'prop/*'):
                result[prop.tag] = prop
    return result


def _value(props, name, default=''):
    node = props.get(name)
    return node.text if node is not None and node.text is not None else default


def _valid_etag(value, strong=False):
    return bool(isinstance(value, str) and len(value) <= 1024
                and re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"', value)
                and (not strong or not value.startswith('W/')))


def _metadata(item_id, etag, name, mime_type='application/octet-stream', size=None):
    result = {'id': item_id, 'etag': etag, 'name': name, 'mime_type': mime_type,
              'size': size, 'deleted': False, 'read_only': bool(etag and etag.startswith('W/'))}
    match = re.fullmatch(r'(fc-[A-Za-z0-9_-]{1,128}?)--(.+)', unquote(item_id))
    if match:
        result['remote_key'] = match[1]
        if name.startswith(match[1] + '--'): result['name'] = name[len(match[1]) + 2:]
    if result['read_only']: result['unsupported_reason'] = 'weak_etag'
    return result


def snapshot(kind, config, credentials, cursor=None):
    config = validate_dav_config(kind, config)
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4096 or any(ord(ch) < 32 for ch in cursor)):
        raise ProviderError('invalid_cursor')
    if cursor:
        root = ET.Element(DAV + 'sync-collection')
        ET.SubElement(root, DAV + 'sync-token').text = cursor
        ET.SubElement(root, DAV + 'sync-level').text = '1'
        prop = ET.SubElement(root, DAV + 'prop')
        for name in ('getetag', 'displayname', 'getcontenttype', 'getcontentlength', 'resourcetype'):
            ET.SubElement(prop, DAV + name)
        if kind == 'calendar': ET.SubElement(prop, CAL + 'calendar-data')
        method = 'REPORT'
    elif kind == 'calendar':
        root = ET.Element(CAL + 'calendar-query')
        prop = ET.SubElement(root, DAV + 'prop')
        ET.SubElement(prop, DAV + 'getetag'); ET.SubElement(prop, DAV + 'displayname')
        ET.SubElement(prop, CAL + 'calendar-data')
        filt = ET.SubElement(root, CAL + 'filter')
        calendar = ET.SubElement(filt, CAL + 'comp-filter', {'name': 'VCALENDAR'})
        ET.SubElement(calendar, CAL + 'comp-filter', {'name': 'VEVENT'})
        method = 'REPORT'
    else:
        root = ET.Element(DAV + 'propfind'); prop = ET.SubElement(root, DAV + 'prop')
        for name in ('getetag', 'displayname', 'getcontenttype', 'getcontentlength', 'resourcetype', 'sync-token'):
            ET.SubElement(prop, DAV + name)
        method = 'PROPFIND'
    response = request(method, config['base_url'], headers=_headers(credentials, {'Depth': '1', 'Content-Type': 'application/xml; charset=utf-8'}),
                       body=ET.tostring(root, encoding='utf-8', xml_declaration=True), max_bytes=MAX_XML_BYTES)
    # A stale RFC6578 token is a recoverable full-resync signal. Unsupported sync
    # reports likewise fall back to complete collection snapshots, never deletion.
    if cursor and response.status in {403, 409, 410, 501}:
        return snapshot(kind, config, credentials, None)
    check_response(response, allowed=(207,))
    document = _xml(response.body)
    rows = document.findall(DAV + 'response')
    if len(rows) > MAX_ITEMS:
        raise ProviderError('resource_limit')
    items, seen = [], set()
    token = document.findtext(DAV + 'sync-token')
    for row in rows:
        props = _props(row)
        resourcetype = props.get(DAV + 'resourcetype')
        is_collection = resourcetype is not None and resourcetype.find(DAV + 'collection') is not None
        if is_collection:
            token = token or _value(props, DAV + 'sync-token') or None
            continue
        item_id = _href_id(config, row.findtext(DAV + 'href'))
        if item_id is None: continue
        if item_id in seen: raise ProviderError('invalid_response')
        seen.add(item_id)
        status = _status(row)
        if status in {404, 410}:
            items.append({'id': item_id, 'deleted': True, 'etag': None})
            continue
        if status is not None and status != 200:
            raise ProviderError('invalid_response')
        etag = _value(props, DAV + 'getetag')
        if not _valid_etag(etag):
            raise ProviderError('invalid_response')
        length = _value(props, DAV + 'getcontentlength')
        try:
            size = int(length) if length else None
            if size is not None and (size < 0 or size > MAX_FILE_BYTES): raise ValueError()
        except ValueError:
            raise ProviderError('resource_limit') from None
        item = _metadata(item_id, etag, _value(props, DAV + 'displayname') or unquote(item_id),
                         (_value(props, DAV + 'getcontenttype') or ('text/calendar' if kind == 'calendar' else 'application/octet-stream')).split(';', 1)[0].strip(), size)
        if kind == 'calendar':
            content = _value(props, CAL + 'calendar-data')
            if content:
                try: item['fields'] = parse_calendar(content.encode(), default_timezone=config['timezone'])
                except ProviderError as exc:
                    item['read_only'] = True; item['unsupported_reason'] = exc.code
        items.append(item)
    if token and (len(token) > 4096 or any(ord(ch) < 32 for ch in token)):
        raise ProviderError('invalid_response')
    return {'items': items, 'cursor': token or None, 'complete': not bool(cursor)}


def read_item(kind, config, credentials, item):
    config = validate_dav_config(kind, config)
    remote_id = item.get('id') if isinstance(item, dict) else item
    response = request('GET', _item_url(config, remote_id), headers=_headers(credentials),
                       max_bytes=MAX_CALENDAR_BYTES if kind == 'calendar' else MAX_FILE_BYTES)
    check_response(response, allowed=(200,))
    result = {'etag': response.headers.get('etag')}
    if not _valid_etag(result['etag']):
        raise ProviderError('invalid_response')
    if kind == 'calendar': result['fields'] = parse_calendar(response.body, default_timezone=config['timezone'])
    else: result['bytes'] = response.body
    return result


def _condition(expected_etag, creating=False):
    if creating: return {'If-None-Match': '*'}
    if not _valid_etag(expected_etag, strong=True):
        raise ProviderError('etag_required')
    return {'If-Match': expected_etag}


def write_item(kind, config, credentials, resource, remote_id=None, expected_etag=None):
    config = validate_dav_config(kind, config)
    if not isinstance(resource, dict): raise ProviderError('invalid_resource')
    name = resource.get('name')
    if name is not None and (not isinstance(name, str) or not name or len(name) > 255 or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
        raise ProviderError('invalid_resource')
    remote_key = resource.get('remote_key')
    if remote_key is not None and (not isinstance(remote_key, str) or not re.fullmatch(r'fc-[A-Za-z0-9_-]{1,128}', remote_key) or '--' in remote_key):
        raise ProviderError('invalid_resource')
    if kind == 'calendar':
        # The worker sends flat CalendarFields alongside transport metadata.
        # Keep that metadata for stable remote identity, but never feed it into
        # the strict calendar schema. Unknown calendar fields still fail closed.
        fields = resource['fields'] if 'fields' in resource else {
            key: value for key, value in resource.items() if key not in {'remote_key', 'name'}
        }
        if not isinstance(fields, dict): raise ProviderError('invalid_event')
        if remote_key and not fields.get('external_uid'):
            fields = {**fields, 'external_uid': remote_key + '@freecollab'}
        content = serialize_calendar(fields)
        name = name or ('event.ics' if remote_key else secrets.token_hex(16) + '.ics')
        mime = 'text/calendar; charset=utf-8'
    else:
        content = resource.get('bytes', resource.get('content'))
        if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
            raise ProviderError('resource_limit')
        if not isinstance(name, str) or not name: raise ProviderError('invalid_resource')
        mime = resource.get('mime_type', 'application/octet-stream')
        if not isinstance(mime, str) or any(ord(ch) < 32 for ch in mime): raise ProviderError('invalid_resource')
    if remote_id is None and remote_key: name = remote_key + '--' + name
    item_id = remote_id if remote_id is not None else quote(name, safe='-._~!$&\'()+,;=@')
    url = _item_url(config, item_id)
    headers = _headers(credentials, {'Content-Type': mime, **_condition(expected_etag, creating=remote_id is None)})
    if remote_id is not None and kind == 'calendar':
        # Refuse lossy rewriting of unsupported remote recurrence, scheduling or
        # custom constructs even when the local caller supplies simple fields.
        old = request('GET', url, headers=_headers(credentials, {'If-Match': expected_etag}), max_bytes=MAX_CALENDAR_BYTES)
        check_response(old, allowed=(200,))
        if old.headers.get('etag') != expected_etag: raise ProviderError('conflict', status=412)
        existing = parse_calendar(old.body, default_timezone=config['timezone'])
        # UID is stable across edits; a caller cannot change the provider identity.
        fields = {**fields, 'external_uid': existing['external_uid']}
        content = serialize_calendar(fields)
        metadata = _preserved_metadata(old.body)
        if metadata:
            content = content.replace(b'END:VEVENT\r\n', metadata + b'END:VEVENT\r\n')
    response = request('PUT', url, headers=headers, body=content, max_bytes=64 * 1024)
    check_response(response, allowed=(200, 201, 204))
    etag = response.headers.get('etag')
    if not etag:
        fresh = request('GET', url, headers=_headers(credentials), max_bytes=MAX_CALENDAR_BYTES if kind == 'calendar' else MAX_FILE_BYTES)
        check_response(fresh, allowed=(200,)); etag = fresh.headers.get('etag')
        if kind == 'files' and fresh.body != content:
            raise ProviderError('conflict', status=412)
        if kind == 'calendar' and parse_calendar(fresh.body, config['timezone']) != parse_calendar(content, config['timezone']):
            raise ProviderError('conflict', status=412)
    if not _valid_etag(etag): raise ProviderError('invalid_response')
    return _metadata(item_id, etag, unquote(item_id), mime, len(content))


def delete_item(kind, config, credentials, remote_id, expected_etag=None):
    config = validate_dav_config(kind, config)
    response = request('DELETE', _item_url(config, remote_id), headers=_headers(credentials, _condition(expected_etag)), max_bytes=64 * 1024)
    check_response(response, allowed=(200, 204, 404, 410))


def _text(value):
    return value.replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _untext(value):
    if re.search(r'\\[^nN\\,;]', value): raise ProviderError('unsupported_event')
    return re.sub(r'\\([nN\\,;])', lambda m: '\n' if m[1] in {'n', 'N'} else m[1], value)


def _ical_time(params, value, default_zone):
    value_kind = params.get('VALUE', 'DATE-TIME')
    if value_kind == 'DATE':
        if set(params) - {'VALUE'} or not re.fullmatch(r'\d{8}', value): raise ProviderError('unsupported_event')
        return datetime.strptime(value, '%Y%m%d').date().isoformat(), True, default_zone
    if value_kind != 'DATE-TIME' or set(params) - {'VALUE', 'TZID'}:
        raise ProviderError('unsupported_event')
    utc = value.endswith('Z')
    if utc and 'TZID' in params: raise ProviderError('unsupported_event')
    raw = value[:-1] if utc else value
    if not re.fullmatch(r'\d{8}T\d{6}', raw): raise ProviderError('unsupported_event')
    zone_name = 'UTC' if utc else params.get('TZID', default_zone).strip('"')
    zone = ZoneInfo(zone_name)
    wall = datetime.strptime(raw, '%Y%m%dT%H%M%S')
    instant = wall.replace(tzinfo=zone, fold=0)
    if instant.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
        raise ProviderError('unsupported_event')
    return instant.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), False, zone_name


def parse_calendar(content, default_timezone='UTC'):
    """A single nonrecurring VEVENT; unsupported remote content is read-only."""
    try:
        if not isinstance(content, bytes) or len(content) > MAX_CALENDAR_BYTES: raise ValueError()
        text = content.decode('utf-8-sig')
        if '\x00' in text: raise ValueError()
        lines = []
        for line in text.replace('\r\n', '\n').split('\n'):
            if not line: continue
            if line[0] in ' \t':
                if not lines: raise ValueError()
                lines[-1] += line[1:]
            else: lines.append(line)
            if len(lines[-1]) > 20000: raise ValueError()
        stack, event, alarm, seen_event = [], {}, {}, False
        calendar_allowed = {'VERSION', 'PRODID', 'CALSCALE'}
        event_allowed = {'UID', 'DTSTAMP', 'CREATED', 'LAST-MODIFIED', 'SEQUENCE', 'SUMMARY', 'DESCRIPTION', 'LOCATION', 'DTSTART', 'DTEND', 'CLASS', 'TRANSP', 'STATUS', 'X-FREECOLLAB-TIMEZONE'}
        for line in lines:
            head, separator, value = line.partition(':')
            if not separator: raise ValueError()
            name, *raw_params = head.split(';'); name = name.upper()
            if name in {'BEGIN', 'END'}:
                value = value.upper()
                if name == 'BEGIN':
                    if not stack and value == 'VCALENDAR': stack.append(value)
                    elif stack == ['VCALENDAR'] and value == 'VEVENT' and not seen_event:
                        seen_event = True; stack.append(value)
                    elif stack == ['VCALENDAR', 'VEVENT'] and value == 'VALARM' and not alarm:
                        stack.append(value)
                    else: raise ValueError()
                else:
                    if not stack or stack[-1] != value: raise ValueError()
                    stack.pop()
                continue
            if stack == ['VCALENDAR']:
                if name not in calendar_allowed or raw_params: raise ValueError()
                if name == 'VERSION' and value != '2.0': raise ValueError()
                continue
            target = event if stack == ['VCALENDAR', 'VEVENT'] else alarm if stack == ['VCALENDAR', 'VEVENT', 'VALARM'] else None
            if target is None or name in target: raise ValueError()
            if target is event and name not in event_allowed: raise ValueError()
            if target is alarm and name not in {'ACTION', 'DESCRIPTION', 'TRIGGER'}: raise ValueError()
            params = {}
            for raw in raw_params:
                key, equal, data = raw.partition('=')
                key = key.upper()
                if not equal or key in params: raise ValueError()
                params[key] = data
            target[name] = (params, value)
        if stack or not seen_event: raise ValueError()
        for prop, allowed in [('CLASS', {'PUBLIC', 'PRIVATE', 'CONFIDENTIAL'}), ('TRANSP', {'OPAQUE', 'TRANSPARENT'}), ('STATUS', {'CONFIRMED'})]:
            if prop in event and (event[prop][0] or event[prop][1] not in allowed): raise ValueError()
        uid = event['UID'][1]
        if event['UID'][0] or not uid: raise ValueError()
        start, all_day, zone = _ical_time(*event['DTSTART'], default_timezone)
        end, end_day, _ = _ical_time(*event['DTEND'], zone)
        if all_day != end_day: raise ValueError()
        display_zone = event.get('X-FREECOLLAB-TIMEZONE')
        if display_zone:
            if display_zone[0]: raise ValueError()
            ZoneInfo(display_zone[1]); zone = display_zone[1]
        elif 'TZID' not in event['DTSTART'][0]:
            ZoneInfo(default_timezone); zone = default_timezone
        for key in ('SUMMARY', 'DESCRIPTION', 'LOCATION'):
            if key in event and event[key][0]: raise ValueError()
        reminder = None
        if alarm:
            if alarm.get('ACTION') != ({}, 'DISPLAY'): raise ValueError()
            if 'DESCRIPTION' in alarm and alarm['DESCRIPTION'][0]: raise ValueError()
            params, trigger = alarm['TRIGGER']
            if params: raise ValueError()
            match = re.fullmatch(r'-PT(?:(\d+)H)?(?:(\d+)M)?', trigger)
            if not match or not any(match.groups()): raise ValueError()
            reminder = int(match[1] or 0) * 60 + int(match[2] or 0)
        fields = {'title': _untext(event.get('SUMMARY', ({}, 'Untitled event'))[1]),
                  'description': _untext(event.get('DESCRIPTION', ({}, ''))[1]),
                  'location': _untext(event.get('LOCATION', ({}, ''))[1]),
                  'starts_at': start, 'ends_at': end, 'all_day': all_day, 'timezone': zone,
                  'reminder_minutes': reminder, 'external_uid': uid}
        return CalendarFields.model_validate(fields).model_dump()
    except (KeyError, ValueError, TypeError, UnicodeError, OverflowError):
        raise ProviderError('unsupported_event') from None


def _preserved_metadata(content):
    # Retain privacy/transparency metadata that has no editable native field.
    # parse_calendar has already rejected unsupported scheduling/recurrence.
    text = content.decode('utf-8-sig').replace('\r\n', '\n')
    lines = re.sub(r'\n[ \t]', '', text).split('\n')
    names = {'CLASS', 'TRANSP', 'STATUS', 'CREATED'}
    retained = [line for line in lines if line.partition(':')[0].upper() in names]
    return ('\r\n'.join(retained) + '\r\n').encode() if retained else b''


def serialize_calendar(fields):
    try:
        fields = CalendarFields.model_validate(fields).model_dump()
        if fields.get('recurrence') or fields.get('exceptions'):
            raise ProviderError('unsupported_recurrence')
        uid = fields.get('external_uid') or secrets.token_urlsafe(24) + '@freecollab'
        lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//FreeCollab//Calendar Sync//EN', 'CALSCALE:GREGORIAN',
                 'BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                 'X-FREECOLLAB-TIMEZONE:' + fields['timezone']]
        for key, property_name in (('starts_at', 'DTSTART'), ('ends_at', 'DTEND')):
            if fields['all_day']: lines.append(property_name + ';VALUE=DATE:' + fields[key].replace('-', ''))
            else: lines.append(property_name + ':' + datetime.fromisoformat(fields[key].replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        lines.extend(['SUMMARY:' + _text(fields['title']), 'DESCRIPTION:' + _text(fields['description']), 'LOCATION:' + _text(fields['location'])])
        reminder = fields.get('reminder_minutes')
        if reminder is not None:
            lines.extend(['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:Reminder', f'TRIGGER:-PT{reminder}M', 'END:VALARM'])
        lines.extend(['END:VEVENT', 'END:VCALENDAR'])
        # Fold by UTF-8 octets without splitting a codepoint (RFC5545).
        folded = []
        for line in lines:
            current = ''
            for char in line:
                if len((current + char).encode()) > 74:
                    folded.append(current); current = ' ' + char
                else: current += char
            folded.append(current)
        return ('\r\n'.join(folded) + '\r\n').encode()
    except (KeyError, ValueError, TypeError):
        raise ProviderError('invalid_event') from None
