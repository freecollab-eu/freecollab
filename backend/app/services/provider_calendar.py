"""Finite Google/Graph series, exact instance revisions and reviewed scheduling.

This module never chooses identities, credentials or recipient consent. The
calling durable worker owns those decisions and rechecks them around every call.
Ordinary sync suppresses scheduling and treats attendee-bearing events read-only.
"""
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import re
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .calendar_planning import _dates, instant, iso
from .integration_content import CalendarFields
from .remote_transport import ProviderError

MAX_OCCURRENCES = 366
MAX_EXCEPTIONS = 100
MAX_PAGES = 20
MAX_ITEMS = 1000
GRAPH_PREFER = {'Prefer': 'outlook.timezone="UTC", outlook.body-content-type="text"'}
_AUTHORITY_GUARD = ContextVar('calendar_provider_authority_guard', default=None)
# Graph accepts supported IANA zones on writes; externally-created Windows zones
# outside this explicit common mapping remain unsupported, never guessed.
WINDOWS_ZONES = {'UTC': 'UTC', 'W. Europe Standard Time': 'Europe/Berlin',
    'Central Europe Standard Time': 'Europe/Budapest', 'Romance Standard Time': 'Europe/Paris',
    'GMT Standard Time': 'Europe/London', 'FLE Standard Time': 'Europe/Helsinki',
    'Eastern Standard Time': 'America/New_York', 'Pacific Standard Time': 'America/Los_Angeles'}


def _providers():
    from . import remote_providers
    return remote_providers


@contextmanager
def authority_guard(callback, on_effect=None):
    """Bind the durable caller's fresh fence to each vendor HTTP request."""
    token = _AUTHORITY_GUARD.set((callback, on_effect))
    try:
        yield
    finally:
        _AUTHORITY_GUARD.reset(token)


def _json(*args, **kwargs):
    guard = _AUTHORITY_GUARD.get()
    if guard is not None:
        guard[0]()
    result = _providers()._json(*args, **kwargs)
    if guard is not None and guard[1] is not None and args[0] in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        guard[1]()
    return result


def _request(*args, **kwargs):
    guard = _AUTHORITY_GUARD.get()
    if guard is not None:
        guard[0]()
    response = _providers().transport.request(*args, **kwargs)
    if guard is not None and guard[1] is not None and args[0] in {'POST', 'PUT', 'PATCH', 'DELETE'} and 200 <= response.status < 300:
        guard[1]()
    return response


def _base(provider, config):
    p = _providers()
    return (p.GOOGLE_CALENDAR + '/calendars/' + p._id(config['calendar_id']) + '/events' if provider == 'google'
            else p.GRAPH + '/me/calendars/' + p._id(config['calendar_id']) + '/events')


def _zone(value):
    value = WINDOWS_ZONES.get(value, value)
    try:
        ZoneInfo(value)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ProviderError('unsupported_timezone') from None
    return value


def _recurrence(provider, raw):
    value = raw.get('recurrence')
    if not value:
        return None
    if provider == 'google':
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str) or not value[0].startswith('RRULE:'):
            raise ProviderError('unsupported_recurrence')
        pairs = [part.split('=') for part in value[0][6:].upper().split(';')]
        if any(len(part) != 2 for part in pairs) or len({part[0] for part in pairs}) != len(pairs):
            raise ProviderError('unsupported_recurrence')
        rule = dict(pairs)
        if set(rule) - {'FREQ', 'INTERVAL', 'COUNT'} or not re.fullmatch(r'[1-9][0-9]{0,2}', rule.get('COUNT', '')):
            raise ProviderError('unsupported_recurrence')
        return {'frequency': rule.get('FREQ', '').lower(), 'interval': int(rule.get('INTERVAL', '1')), 'count': int(rule['COUNT'])}
    if not isinstance(value, dict) or set(value) != {'pattern', 'range'}:
        raise ProviderError('unsupported_recurrence')
    pattern, span = value['pattern'], value['range']
    if not isinstance(pattern, dict) or not isinstance(span, dict) or span.get('type') != 'numbered':
        raise ProviderError('unsupported_recurrence')
    frequency = {'daily': 'daily', 'weekly': 'weekly', 'absoluteMonthly': 'monthly'}.get(pattern.get('type'))
    if not frequency:
        raise ProviderError('unsupported_recurrence')
    return {'frequency': frequency, 'interval': pattern.get('interval'), 'count': span.get('numberOfOccurrences')}


def fields_from_master(provider, raw):
    p = _providers()
    if raw.get('recurringEventId') or raw.get('type') in {'occurrence', 'exception'}:
        raise ProviderError('unsupported_instance')
    fields = p._plain_calendar_fields(raw, provider)
    recurrence = _recurrence(provider, raw)
    if provider == 'microsoft' and fields['all_day']:
        zone = _zone((raw.get('recurrence') or {}).get('range', {}).get('recurrenceTimeZone') or raw.get('originalStartTimeZone') or 'UTC')
        fields['timezone'] = zone
        for key, source in [('starts_at', 'start'), ('ends_at', 'end')]:
            value = raw[source]['dateTime']; source_zone = _zone(raw[source].get('timeZone', 'UTC'))
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo(source_zone))
            fields[key] = stamp.astimezone(ZoneInfo(zone)).date().isoformat()
    if recurrence:
        if provider == 'microsoft':
            span = raw['recurrence']['range']
            fields['timezone'] = _zone(span.get('recurrenceTimeZone') or raw.get('originalStartTimeZone') or 'UTC')
            pattern = raw['recurrence']['pattern']
            start = date.fromisoformat(fields['starts_at']) if fields['all_day'] else instant(fields['starts_at']).astimezone(ZoneInfo(fields['timezone'])).date()
            if span.get('startDate') != start.isoformat():
                raise ProviderError('unsupported_recurrence')
            if recurrence['frequency'] == 'weekly':
                day = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'][start.weekday()]
                if pattern.get('daysOfWeek') != [day]:
                    raise ProviderError('unsupported_recurrence')
            if recurrence['frequency'] == 'monthly' and pattern.get('dayOfMonth') != start.day:
                raise ProviderError('unsupported_recurrence')
        fields['recurrence'] = recurrence
    try:
        return CalendarFields.model_validate(fields).model_dump()
    except (ValueError, TypeError):
        raise ProviderError('unsupported_recurrence' if recurrence else 'unsupported_resource') from None


def _attendees(provider, raw):
    values = raw.get('attendees', [])
    if not isinstance(values, list) or len(values) > 50:
        raise ProviderError('unsupported_attendees')
    result = []
    for row in values:
        if not isinstance(row, dict):
            raise ProviderError('unsupported_attendees')
        source = row if provider == 'google' else row.get('emailAddress', {})
        email = source.get('email') if provider == 'google' else source.get('address')
        name = source.get('displayName', '') if provider == 'google' else source.get('name', '')
        response = row.get('responseStatus', 'needsAction') if provider == 'google' else row.get('status', {}).get('response', 'none')
        statuses = {'needsAction': 'needs-action', 'none': 'needs-action', 'notResponded': 'needs-action',
            'accepted': 'accepted', 'tentative': 'tentative', 'tentativelyAccepted': 'tentative', 'declined': 'declined', 'organizer': 'accepted'}
        if (not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', email)
                or not isinstance(name, str) or len(name) > 200 or response not in statuses):
            raise ProviderError('unsupported_attendees')
        result.append({'email': email, 'name': name, 'response': statuses[response],
            'self': bool(row.get('self')) if provider == 'google' else False,
            'organizer': bool(row.get('organizer')) if provider == 'google' else response == 'organizer',
            'optional': bool(row.get('optional')) if provider == 'google' else row.get('type') == 'optional'})
    return result


def item_from_master(provider, raw):
    p = _providers()
    identity = raw.get('id'); p._id(identity)
    deleted = raw.get('status') == 'cancelled' or '@removed' in raw or raw.get('isCancelled') is True
    etag = raw.get('etag') if provider == 'google' else raw.get('@odata.etag')
    result = {'id': identity, 'etag': etag, 'name': raw.get('summary', raw.get('subject', 'Calendar event')),
        'mime_type': 'text/calendar', 'deleted': deleted, 'read_only': False}
    if deleted:
        return result
    if not p._valid_etag(etag):
        raise ProviderError('precondition_unavailable')
    attendees = _attendees(provider, raw)
    organizer = raw.get('organizer', {}).get('self') is True if provider == 'google' else raw.get('isOrganizer') is True
    can_respond = (not organizer and any(row['self'] and not row['organizer'] for row in attendees)) if provider == 'google' else raw.get('isOrganizer') is False
    result.update(fields=fields_from_master(provider, raw), attendees=attendees, organizer_self=organizer,
        can_respond=can_respond, read_only=bool(attendees), remote_key=(raw.get('extendedProperties', {}).get('private', {}).get('freecollabSyncKey') if provider == 'google' else raw.get('transactionId')))
    return result


def _get(provider, config, credentials, identity, *, expanded=False):
    p = _providers(); url = _base(provider, config) + '/' + p._id(identity)
    if provider == 'microsoft' and expanded:
        selected = 'id,subject,body,location,start,end,isAllDay,isReminderOn,reminderMinutesBeforeStart,iCalUId,recurrence,originalStartTimeZone,attendees,organizer,isOrganizer,responseStatus,transactionId,cancelledOccurrences,exceptionOccurrences,originalStart,seriesMasterId,type,occurrenceId'
        url += '?' + urlencode({'$select': selected, '$expand': 'exceptionOccurrences'})
    raw, _ = _json('GET', url, credentials, headers=GRAPH_PREFER if provider == 'microsoft' else None)
    if raw.get('id') != identity:
        raise ProviderError('outside_collection')
    return raw


def _original(provider, raw, all_day, zone="UTC"):
    value = raw.get('originalStartTime', {}) if provider == 'google' else raw.get('originalStart')
    value = value.get('date' if all_day else 'dateTime') if provider == 'google' and isinstance(value, dict) else value
    if not isinstance(value, str):
        raise ProviderError('incomplete_series')
    return (value if provider == 'google' else instant(value).astimezone(ZoneInfo(zone)).date().isoformat()) if all_day else iso(instant(value))


def _next_link(value, base):
    """Continuation must stay on the exact selected collection/instance path."""
    target, source = urlsplit(value), urlsplit(base)
    if (target.scheme, target.netloc, target.path) != (source.scheme, source.netloc, source.path) or target.fragment or target.username or target.password or len(value) > 16384:
        raise ProviderError('unsafe_pagination')
    return value


def _instance_rows(provider, config, credentials, identity, fields):
    p = _providers(); url = _base(provider, config) + '/' + p._id(identity) + '/instances'
    if provider == 'google':
        query = {'showDeleted': 'true', 'maxResults': '250'}
    else:
        source = CalendarFields.model_validate({**fields, 'exceptions': []})
        dates = list(_dates(source)); start, end = dates[0][1], dates[-1][2]
        if source.all_day:
            start = datetime.combine(start, datetime.min.time(), ZoneInfo(source.timezone)); end = datetime.combine(end, datetime.min.time(), ZoneInfo(source.timezone))
        query = {'startDateTime': iso(start - timedelta(days=1)), 'endDateTime': iso(end + timedelta(days=1)), '$top': '250'}
    first = url; url += '?' + urlencode(query); rows = []; seen = set()
    for _ in range(MAX_PAGES):
        if not url:
            return rows
        if url in seen:
            raise ProviderError('snapshot_too_large')
        seen.add(url)
        raw, _ = _json('GET', url, credentials, headers=GRAPH_PREFER if provider == 'microsoft' else None)
        page = raw.get('items' if provider == 'google' else 'value')
        if not isinstance(page, list) or len(rows) + len(page) > MAX_OCCURRENCES:
            raise ProviderError('snapshot_too_large')
        rows.extend(page)
        if provider == 'google':
            token = p._cursor(raw.get('nextPageToken')); url = first + '?' + urlencode({**query, 'pageToken': token}) if token else None
        else:
            url = _next_link(raw['@odata.nextLink'], first) if raw.get('@odata.nextLink') else None
    raise ProviderError('snapshot_too_large')


def _hydrate(provider, config, credentials, raw):
    item = item_from_master(provider, raw)
    if item['deleted'] or not item['fields'].get('recurrence'):
        return item
    p = _providers()
    if provider == 'microsoft':
        raw = _get(provider, config, credentials, item['id'], expanded=True)
        item = item_from_master(provider, raw)
    fields = item['fields']; expected = {identity: (start, end) for identity, start, end in _dates(CalendarFields.model_validate(fields))}
    rows = _instance_rows(provider, config, credentials, item['id'], fields)
    if provider == 'microsoft':
        expanded = raw.get('exceptionOccurrences', [])
        if not isinstance(expanded, list) or len(expanded) > MAX_EXCEPTIONS or raw.get('exceptionOccurrences@odata.nextLink'):
            raise ProviderError('snapshot_too_large')
        for extra in expanded:
            if not isinstance(extra, dict) or not isinstance(extra.get('id'), str):
                raise ProviderError('incomplete_series')
            if not any(row.get('id') == extra['id'] for row in rows):
                # Expanded projections do not always carry etags/originalStart.
                rows.append(_get(provider, config, credentials, extra['id']))
    actual = {}; exceptions = []; versions = []
    for row in rows:
        if not isinstance(row, dict) or row.get('recurringEventId', row.get('seriesMasterId')) != item['id']:
            raise ProviderError('outside_collection')
        original = _original(provider, row, fields['all_day'], fields['timezone'])
        if original not in expected or original in actual:
            raise ProviderError('incomplete_series')
        actual[original] = row
        etag = row.get('etag') if provider == 'google' else row.get('@odata.etag')
        cancelled = row.get('status') == 'cancelled' or row.get('isCancelled') is True
        if not p._valid_etag(etag) and not cancelled:
            raise ProviderError('precondition_unavailable')
        versions.append([original, row['id'], etag or 'cancelled'])
        if cancelled:
            exceptions.append({'original_start': original, 'cancelled': True})
            continue
        base_start, base_end = expected[original]
        base = {**fields, 'starts_at': base_start.isoformat() if fields['all_day'] else iso(base_start),
            'ends_at': base_end.isoformat() if fields['all_day'] else iso(base_end)}
        if _attendees(provider, row) != item['attendees']:
            raise ProviderError('unsupported_instance_attendees')
        value = p._plain_calendar_fields(row, provider)
        if provider == 'microsoft' and fields['all_day']:
            for key, source in [('starts_at', 'start'), ('ends_at', 'end')]:
                point = row[source]; parsed = datetime.fromisoformat(point['dateTime'].replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo(_zone(point.get('timeZone', 'UTC'))))
                value[key] = parsed.astimezone(ZoneInfo(fields['timezone'])).date().isoformat()
        override = {key: value[key] for key in ['starts_at', 'ends_at', 'title', 'location', 'description'] if value[key] != base[key]}
        # Paired date overrides are required by the local immutable schema.
        if 'starts_at' in override or 'ends_at' in override:
            override.update(starts_at=value['starts_at'], ends_at=value['ends_at'])
        if override:
            exceptions.append({'original_start': original, 'cancelled': False, **override})
    missing = set(expected) - set(actual)
    if provider == 'microsoft':
        cancelled = raw.get('cancelledOccurrences', [])
        if not isinstance(cancelled, list) or len(set(cancelled)) != len(cancelled) or len(cancelled) != len(missing):
            raise ProviderError('incomplete_series')
        exceptions.extend({'original_start': identity, 'cancelled': True} for identity in sorted(missing))
        versions.extend(['cancelled', value] for value in sorted(cancelled))
    elif missing:
        raise ProviderError('incomplete_series')
    if len(exceptions) > MAX_EXCEPTIONS:
        raise ProviderError('snapshot_too_large')
    item['fields'] = CalendarFields.model_validate({**fields, 'exceptions': exceptions}).model_dump()
    item['master_etag'] = item['etag']
    item['etag'] = '"freecollab-calendar-' + sha256(json.dumps([item['etag'], sorted(versions)], sort_keys=True).encode()).hexdigest() + '"'
    item['_instances'] = actual
    item['_master'] = raw
    return item


def _read(provider, config, credentials, remote_id):
    return _hydrate(provider, config, credentials, _get(provider, config, credentials, remote_id))


def calendar_read(provider, config, credentials, remote_id):
    p = _providers(); config = p.validate_config(provider, 'calendar', config)
    if provider not in {'google', 'microsoft'}:
        raise ProviderError('unsupported_provider')
    return {key: value for key, value in _read(provider, config, credentials, remote_id).items() if not key.startswith('_')}


def calendar_snapshot(provider, config, credentials, cursor=None):
    p = _providers(); cursor = p._cursor(cursor); config = p.validate_config(provider, 'calendar', config); base = _base(provider, config)
    if provider not in {'google', 'microsoft'}:
        raise ProviderError('unsupported_provider')
    if provider == 'google':
        query = {'maxResults': '100', 'showDeleted': 'true', 'singleEvents': 'false'}
        if cursor:
            query['syncToken'] = cursor
        url = base + '?' + urlencode(query)
    else:
        query = {}; url = base + '?$top=100'
    rows = []; seen = set(); next_cursor = None
    for _ in range(MAX_PAGES):
        if not url:
            break
        if url in seen:
            raise ProviderError('snapshot_too_large')
        seen.add(url)
        data, _ = _json('GET', url, credentials, headers=GRAPH_PREFER if provider == 'microsoft' else None)
        page = data.get('items' if provider == 'google' else 'value')
        if not isinstance(page, list) or len(rows) + len(page) > MAX_ITEMS:
            raise ProviderError('snapshot_too_large')
        rows.extend(page)
        if provider == 'google':
            token = p._cursor(data.get('nextPageToken')); next_cursor = p._cursor(data.get('nextSyncToken'))
            url = base + '?' + urlencode({**query, 'pageToken': token}) if token else None
        else:
            url = _next_link(data['@odata.nextLink'], base) if data.get('@odata.nextLink') else None
    if url:
        raise ProviderError('snapshot_too_large')
    masters = {}; hydrate = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError('invalid_response')
        parent = row.get('recurringEventId', row.get('seriesMasterId'))
        if parent:
            p._id(parent); hydrate.add(parent); continue
        identity = row.get('id'); p._id(identity)
        if identity in masters and not (provider == 'google' and cursor):
            raise ProviderError('invalid_response')
        masters[identity] = row
    for identity in hydrate:
        if identity not in masters:
            masters[identity] = _get(provider, config, credentials, identity)
    values = []; scanned = 0
    for raw in masters.values():
        value = _hydrate(provider, config, credentials, raw)
        scanned += 1 + len(value.get('_instances', {}))
        if scanned > MAX_ITEMS:
            raise ProviderError('snapshot_too_large')
        # Runtime-only internal fields are neither retained in sync snapshots nor
        # exposed through a public API. Their bounded transport metadata is read
        # fresh before an actual conditional mutation.
        values.append({key: item for key, item in value.items() if not key.startswith('_')})
    return {'items': values, 'cursor': next_cursor, 'complete': not bool(provider == 'google' and cursor)}


def master_body(provider, value):
    p = _providers(); fields = CalendarFields.model_validate({key: value[key] for key in CalendarFields.model_fields if key in value}); body = p._plain_calendar_write(fields.model_dump(), provider)
    if fields.all_day:
        for key, value in [('start', fields.starts_at), ('end', fields.ends_at)]:
            body[key] = {'date': value, 'timeZone': fields.timezone} if provider == 'google' else {'dateTime': value + 'T00:00:00', 'timeZone': fields.timezone}
    if not fields.recurrence:
        return body
    rule = fields.recurrence
    if provider == 'google':
        body['recurrence'] = ['RRULE:FREQ=' + rule.frequency.upper() + ';INTERVAL=' + str(rule.interval) + ';COUNT=' + str(rule.count)]
    else:
        start = date.fromisoformat(fields.starts_at) if fields.all_day else instant(fields.starts_at).astimezone(ZoneInfo(fields.timezone)).date()
        pattern = {'type': {'daily': 'daily', 'weekly': 'weekly', 'monthly': 'absoluteMonthly'}[rule.frequency], 'interval': rule.interval}
        if rule.frequency == 'weekly':
            pattern['daysOfWeek'] = [['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'][start.weekday()]]
            pattern['firstDayOfWeek'] = 'monday'
        if rule.frequency == 'monthly':
            pattern['dayOfMonth'] = start.day
        body['recurrence'] = {'pattern': pattern, 'range': {'type': 'numbered', 'startDate': start.isoformat(), 'numberOfOccurrences': rule.count, 'recurrenceTimeZone': fields.timezone}}
        for key, instant_value in [('start', fields.starts_at), ('end', fields.ends_at)]:
            body[key] = {'dateTime': instant_value + 'T00:00:00' if fields.all_day else instant(instant_value).astimezone(ZoneInfo(fields.timezone)).replace(tzinfo=None).isoformat(), 'timeZone': fields.timezone}
    return body


def _condition(item, expected_etag):
    p = _providers(); p._precondition(expected_etag)
    if item.get('etag') != expected_etag:
        raise ProviderError('conflict', status=412)
    return p._precondition(item.get('master_etag', item['etag']))


def _publish_attendees(provider, attendees):
    if not isinstance(attendees, list) or not 1 <= len(attendees) <= 50:
        raise ProviderError('invalid_attendees')
    normalized = []
    for row in attendees:
        if not isinstance(row, dict) or set(row) - {'email', 'name', 'optional'}:
            raise ProviderError('invalid_attendees')
        email, name = row.get('email'), row.get('name', '')
        if (not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', email)
                or not isinstance(name, str) or len(name) > 200 or any(ord(char) < 32 for char in name)):
            raise ProviderError('invalid_attendees')
        normalized.append({'email': email, 'displayName': name, 'optional': bool(row.get('optional'))} if provider == 'google' else
            {'emailAddress': {'address': email, 'name': name}, 'type': 'optional' if row.get('optional') else 'required'})
    if len({row.get('email', row.get('emailAddress', {}).get('address')).casefold() for row in normalized}) != len(normalized):
        raise ProviderError('invalid_attendees')
    return normalized


def _validate_restore(provider, current, fields):
    if not current:
        return
    desired = {row.original_start: row for row in fields.exceptions}
    for previous in current['fields'].get('exceptions', []):
        if previous['cancelled'] and (previous['original_start'] not in desired or not desired[previous['original_start']].cancelled):
            if provider == 'microsoft':
                raise ProviderError('cancelled_occurrence_requires_new_series')
            row = current.get('_instances', {}).get(previous['original_start'], {})
            if not _providers()._valid_etag(row.get('etag')):
                raise ProviderError('precondition_unavailable')


def _write(provider, config, credentials, value, *, remote_id=None, expected_etag=None, remote_key=None, attendees=None):
    p = _providers(); fields = CalendarFields.model_validate({key: value[key] for key in CalendarFields.model_fields if key in value}); current = None
    if remote_id:
        p._precondition(expected_etag)
        current = _read(provider, config, credentials, remote_id)
        headers = _condition(current, expected_etag)
        if current['deleted']:
            raise ProviderError('not_found', status=404)
        if attendees is None and current.get('attendees'):
            raise ProviderError('read_only_resource')
        if attendees is not None and current.get('organizer_self') is not True:
            raise ProviderError('organizer_required')
    else:
        headers = {'If-None-Match': '*'}
        p._remote_key({'remote_key': remote_key})
    _validate_restore(provider, current, fields)
    current_model = CalendarFields.model_validate(current['fields']) if current else None
    baseline_changed = current_model is None or current_model.model_dump(exclude={'exceptions', 'external_uid'}) != fields.model_dump(exclude={'exceptions', 'external_uid'})
    exceptions_changed = current_model is None or current_model.model_dump().get('exceptions', []) != fields.model_dump().get('exceptions', [])
    if current and fields.recurrence and baseline_changed and exceptions_changed:
        raise ProviderError('combined_series_edit_requires_separate_review')
    if current and fields.recurrence and not baseline_changed:
        if attendees is not None:
            # The reviewed attendee roster is itself a master-level scheduling
            # effect, so it cannot silently share an exception-only operation.
            desired_roster = _publish_attendees(provider, attendees)
            existing_roster = _publish_attendees(provider, [{'email': row['email'], 'name': row['name'], 'optional': row['optional']} for row in current['attendees']]) if current['attendees'] else []
            if desired_roster != existing_roster:
                if exceptions_changed:
                    raise ProviderError('combined_series_edit_requires_separate_review')
            else:
                _apply_exceptions(provider, config, credentials, current, fields, invitations=True)
                return calendar_read(provider, config, credentials, remote_id)
        else:
            _apply_exceptions(provider, config, credentials, current, fields, invitations=False)
            return calendar_read(provider, config, credentials, remote_id)
    body = master_body(provider, fields.model_dump())
    if attendees is not None:
        body['attendees'] = _publish_attendees(provider, attendees)
    if not remote_id:
        if provider == 'google':
            body.update(id=sha256(remote_key.encode()).hexdigest(), extendedProperties={'private': {'freecollabSyncKey': remote_key}})
        else:
            body['transactionId'] = remote_key
    # Clearing a local rule is an explicit conversion to a standalone event.
    elif current['fields'].get('recurrence') and not fields.recurrence:
        body['recurrence'] = [] if provider == 'google' else None
    url = _base(provider, config) + ('/' + p._id(remote_id) if remote_id else '')
    if provider == 'google':
        url += '?' + urlencode({'sendUpdates': 'all' if attendees is not None else 'none'})
    raw, _ = _json('PATCH' if remote_id else 'POST', url, credentials, body=body, headers={**(GRAPH_PREFER if provider == 'microsoft' else {}), **headers})
    identity = raw.get('id'); p._id(identity)
    if fields.recurrence:
        actual = _hydrate(provider, config, credentials, raw)
        if not current:
            _apply_exceptions(provider, config, credentials, actual, fields, invitations=attendees is not None)
        return calendar_read(provider, config, credentials, identity)
    return item_from_master(provider, raw)


def _apply_exceptions(provider, config, credentials, current, fields, *, invitations):
    p = _providers(); desired = {row.original_start: row for row in fields.exceptions}
    originals = {identity: (start, end) for identity, start, end in _dates(fields)}
    previous = {row['original_start']: row for row in current['fields'].get('exceptions', [])}
    for identity in sorted(set(desired) | set(previous)):
        target, before = desired.get(identity), previous.get(identity)
        if target and target.model_dump() == before:
            continue
        if target and target.cancelled and before and before.get('cancelled'):
            continue
        row = current['_instances'].get(identity)
        if row is None:
            raise ProviderError('cancelled_occurrence_requires_new_series')
        start, end = originals[identity]
        base = fields.model_dump(exclude={'recurrence', 'exceptions'})
        base.update(starts_at=start.isoformat() if fields.all_day else iso(start), ends_at=end.isoformat() if fields.all_day else iso(end))
        if target and not target.cancelled:
            base.update(target.model_dump(exclude={'original_start', 'cancelled'}, exclude_none=True))
        url = _base(provider, config) + '/' + p._id(row['id'])
        headers = p._precondition(row.get('etag') if provider == 'google' else row.get('@odata.etag'))
        cancel = bool(target and target.cancelled)
        if provider == 'google':
            url += '?' + urlencode({'sendUpdates': 'all' if invitations else 'none'})
        if cancel:
            response = _request('DELETE', url, headers={**p._headers(credentials), **headers})
            p.transport.check_response(response, (200, 204))
        else:
            body = master_body(provider, base)
            if provider == 'google':
                body['status'] = 'confirmed'
            _json('PATCH', url, credentials, body=body, headers={**(GRAPH_PREFER if provider == 'microsoft' else {}), **headers})


def calendar_write(provider, config, credentials, resource, remote_id=None, expected_etag=None):
    # Ordinary background synchronization never opts into provider invitations.
    return _write(provider, config, credentials, resource, remote_id=remote_id, expected_etag=expected_etag, remote_key=resource.get('remote_key'))


def calendar_publish(provider, config, credentials, fields, *, remote_id=None, expected_etag=None, remote_key, attendees):
    p = _providers(); config = p.validate_config(provider, 'calendar', config)
    if provider not in {'google', 'microsoft'}:
        raise ProviderError('unsupported_provider')
    return _write(provider, config, credentials, fields, remote_id=remote_id, expected_etag=expected_etag, remote_key=remote_key, attendees=attendees)


def calendar_cancel(provider, config, credentials, remote_id, *, expected_etag):
    p = _providers(); current = calendar_read(provider, config, credentials, remote_id)
    headers = _condition(current, expected_etag)
    if current.get('organizer_self') is not True:
        raise ProviderError('organizer_required')
    if provider == 'google':
        response = _request('DELETE', _base(provider, config) + '/' + p._id(remote_id) + '?sendUpdates=all', headers={**p._headers(credentials), **headers})
    else:
        response = _request('POST', _base(provider, config) + '/' + p._id(remote_id) + '/cancel', headers={**p._headers(credentials), **headers, 'Content-Type': 'application/json'}, body=b'{}')
    p.transport.check_response(response, (200, 202, 204))
    return {'id': remote_id, 'etag': current['etag'], 'deleted': True}


def calendar_respond(provider, config, credentials, remote_id, response, *, expected_etag):
    p = _providers(); current = calendar_read(provider, config, credentials, remote_id)
    headers = _condition(current, expected_etag)
    if response not in {'accepted', 'tentative', 'declined'} or not current.get('can_respond'):
        raise ProviderError('delegated_attendee_required')
    url = _base(provider, config) + '/' + p._id(remote_id)
    if provider == 'google':
        own = [row for row in current['attendees'] if row['self'] and not row['organizer']]
        if len(own) != 1:
            raise ProviderError('delegated_attendee_required')
        _json('PATCH', url + '?sendUpdates=all', credentials, body={'attendeesOmitted': True, 'attendees': [{'email': own[0]['email'], 'responseStatus': response}]}, headers=headers)
    else:
        action = {'accepted': 'accept', 'tentative': 'tentativelyAccept', 'declined': 'decline'}[response]
        result = _request('POST', url + '/' + action, headers={**p._headers(credentials), **headers, 'Content-Type': 'application/json'}, body=b'{"sendResponse":true}')
        p.transport.check_response(result, (200, 202, 204))
    # Graph decline can remove the delegated calendar copy. Acknowledged action
    # remains successful, with no fabricated post-action resource state.
    try:
        return calendar_read(provider, config, credentials, remote_id)
    except ProviderError as error:
        if error.status != 404:
            raise
        return {'id': remote_id, 'etag': current['etag'], 'deleted': True, 'response': response}


def calendar_reconcile(provider, config, credentials, remote_key):
    p = _providers(); p._remote_key({'remote_key': remote_key})
    if provider == 'google':
        try:
            item = calendar_read(provider, config, credentials, sha256(remote_key.encode()).hexdigest())
        except ProviderError as error:
            if error.status == 404:
                return None
            raise
        if item.get('remote_key') != remote_key:
            raise ProviderError('ambiguous_create')
        return item
    found = [row for row in calendar_snapshot(provider, config, credentials)['items'] if row.get('remote_key') == remote_key]
    if len(found) > 1:
        raise ProviderError('ambiguous_create')
    return found[0] if found else None


def calendar_delete(provider, config, credentials, remote_id, expected_etag):
    p = _providers(); current = calendar_read(provider, config, credentials, remote_id)
    if current.get('attendees'):
        raise ProviderError('read_only_resource')
    headers = _condition(current, expected_etag)
    url = _base(provider, config) + '/' + p._id(remote_id) + ('?sendUpdates=none' if provider == 'google' else '')
    response = _request('DELETE', url, headers={**p._headers(credentials), **headers})
    p.transport.check_response(response, (200, 204))


def _bounded(operation):
    def run(*args, **kwargs):
        p = _providers(); budget = p.transport.begin_budget(45)
        try:
            return operation(*args, **kwargs)
        except ProviderError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError):
            raise ProviderError('invalid_response') from None
        finally:
            p.transport.end_budget(budget)
    return run


for _name in ('calendar_read', 'calendar_snapshot', 'calendar_write', 'calendar_publish', 'calendar_cancel', 'calendar_respond', 'calendar_reconcile', 'calendar_delete'):
    globals()[_name] = _bounded(globals()[_name])
