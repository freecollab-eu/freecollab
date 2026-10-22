"""Provider series and scheduling through production transport + trusted TLS."""
from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit
import pytest
from app.services import provider_calendar as calendar
from app.services import remote_providers as providers
from app.services.remote_transport import ProviderError
from tests.provider_calendar_fixture_013 import CONFIG, CREDS, FIELDS, calendar_https

PROVIDERS = ['google', 'microsoft']
ORIGINALS = ['2026-10-23T07:00:00Z', '2026-10-30T08:00:00Z', '2026-11-06T08:00:00Z', '2026-11-13T08:00:00Z']


def create(state, provider, fields=None, *, attendee=False):
    # Production mapping checked separately; fixture expansion independently uses dateutil.
    body = calendar.master_body(provider, fields or FIELDS)
    if attendee:
        body['attendees'] = ([{'email': 'guest@example.com', 'displayName': 'Guest', 'responseStatus': 'needsAction'}] if provider == 'google' else
            [{'emailAddress': {'address': 'guest@example.com', 'name': 'Guest'}, 'type': 'required', 'status': {'response': 'none'}}])
    return state.create(provider, body, 'series-one')


def mutators(state):
    return [row for row in state.requests if row[0] != 'GET']


@pytest.mark.parametrize('provider', PROVIDERS)
def test_revocation_after_first_vendor_effect_blocks_later_instance_requests_and_guard_resets(calendar_https, provider):
    from fastapi import HTTPException
    state=calendar_https
    fields={**FIELDS,'exceptions':[{'original_start':ORIGINALS[1],'title':'Reviewed override'}]}
    checks=[]
    def authority():
        checks.append(len(state.requests))
        if mutators(state):
            raise HTTPException(401,'Original action authority was revoked after the first vendor response')
    with pytest.raises(HTTPException) as rejected:
        with calendar.authority_guard(authority):
            calendar.calendar_publish(provider,CONFIG[provider],CREDS,fields,remote_key='fc-revoked-mid-series',attendees=[{'email':'guest@example.net'}])
    assert rejected.value.status_code==401 and len(mutators(state))==1
    assert mutators(state)[0][0]=='POST' and not any(row[0] in {'PATCH','DELETE'} for row in state.requests)
    # An exception must not leave this caller's identity bound to the next job.
    before=len(checks)
    calendar.calendar_reconcile(provider,CONFIG[provider],CREDS,'fc-revoked-mid-series')
    assert len(checks)==before


@pytest.mark.parametrize('provider', PROVIDERS)
def test_https_series_pagination_retains_wall_clock_across_dst(calendar_https, provider):
    state = calendar_https; create(state, provider); state.paginate = True
    result = calendar.calendar_snapshot(provider, CONFIG[provider], CREDS)
    assert result['complete'] is True and len(result['items']) == 1
    item = result['items'][0]
    assert item['fields']['recurrence'] == FIELDS['recurrence'] and item['fields']['timezone'] == 'Europe/Stockholm'
    assert 'exceptions' not in item['fields']
    assert list(state.instances[provider]['series-one']) == ORIGINALS
    assert item['etag'].startswith('"freecollab-calendar-')
    assert item['master_etag'] == state.events[provider]['series-one']['etag' if provider == 'google' else '@odata.etag']
    assert len([row for row in state.requests if '/instances?' in row[1]]) == 2 and not mutators(state)
    assert set(state.sni) == {'www.googleapis.com' if provider == 'google' else 'graph.microsoft.com'}
    assert state.connects and all(row == b'CONNECT 8.8.8.8:443 HTTP/1.1' for row in state.connects)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_moved_outside_window_and_cancelled_reconstructed(calendar_https, provider):
    state = calendar_https; create(state, provider)
    old = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one')
    rows = state.instances[provider]['series-one']; moved = rows[ORIGINALS[1]]
    if provider == 'google':
        moved.update(summary='Moved review', start={'dateTime': '2027-03-10T09:00:00Z', 'timeZone': 'Europe/Stockholm'}, end={'dateTime': '2027-03-10T09:30:00Z', 'timeZone': 'Europe/Stockholm'})
        rows[ORIGINALS[2]]['status'] = 'cancelled'; rows[ORIGINALS[2]].pop('etag')
    else:
        moved.update(type='exception', subject='Moved review', start={'dateTime': '2027-03-10T09:00:00', 'timeZone': 'UTC'}, end={'dateTime': '2027-03-10T09:30:00', 'timeZone': 'UTC'})
        rows.pop(ORIGINALS[2]); state.cancelled[provider]['series-one'][ORIGINALS[2]] = 'unparsed-opaque-id'
    state.versioned(provider, moved)
    new = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one')
    assert new['etag'] != old['etag'] and new['master_etag'] == old['master_etag']
    exceptions = {row['original_start']: row for row in new['fields']['exceptions']}
    assert exceptions[ORIGINALS[1]]['starts_at'] == '2027-03-10T09:00:00Z' and exceptions[ORIGINALS[1]]['title'] == 'Moved review'
    assert exceptions[ORIGINALS[2]]['cancelled'] is True and not mutators(state)
    if provider == 'microsoft': assert any('/series-one-instance-1' in row[1] and '/instances' not in row[1] for row in state.requests)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_create_exception_edit_exact_versions_and_readonly_reconcile(calendar_https, provider):
    state = calendar_https
    fields = {**FIELDS, 'exceptions': [{'original_start': ORIGINALS[1], 'starts_at': '2026-10-30T10:00:00Z', 'ends_at': '2026-10-30T10:30:00Z'}, {'original_start': ORIGINALS[2], 'cancelled': True}]}
    created = providers.write_item(provider, 'calendar', CONFIG[provider], CREDS, {**fields, 'remote_key': 'fc-owned-publication'})
    assert len(created['fields']['exceptions']) == 2 and created['remote_key'] == 'fc-owned-publication'
    request = next(row for row in state.requests if row[0] == 'POST'); body = json.loads(request[3])
    assert request[2]['If-None-Match'] == '*'
    if provider == 'google':
        assert body['recurrence'] == ['RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=4']
        assert parse_qs(urlsplit(request[1]).query)['sendUpdates'] == ['none']
    else:
        assert body['recurrence']['pattern'] == {'type': 'weekly', 'interval': 1, 'daysOfWeek': ['friday'], 'firstDayOfWeek': 'monday'}
        assert body['start'] == {'dateTime': '2026-10-23T09:00:00', 'timeZone': 'Europe/Stockholm'}
    assert all(row[2].get('If-Match', '').startswith('"fixture-') for row in mutators(state) if row[0] in {'PATCH', 'DELETE'})
    before = len(mutators(state)); recovered = calendar.calendar_reconcile(provider, CONFIG[provider], CREDS, 'fc-owned-publication')
    assert recovered['id'] == created['id'] and len(mutators(state)) == before
    changed = deepcopy(created['fields']); changed['exceptions'][0]['title'] = 'Reviewed occurrence'
    result = providers.write_item(provider, 'calendar', CONFIG[provider], CREDS, {**changed, 'remote_key': 'fc-owned-publication'}, remote_id=created['id'], expected_etag=created['etag'])
    writes = mutators(state)[before:]
    assert len(writes) == 1 and writes[0][0] == 'PATCH' and '-instance-' in writes[0][1]
    assert result['fields']['exceptions'][0]['title'] == 'Reviewed occurrence' and len(state.events[provider]) == 1


@pytest.mark.parametrize('provider', PROVIDERS)
def test_concurrent_instance_change_is_conditional_conflict(calendar_https, provider):
    state = calendar_https; create(state, provider)
    current = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one'); state.concurrent_instance = True
    fields = {**current['fields'], 'exceptions': [{'original_start': ORIGINALS[1], 'title': 'My change'}]}
    with pytest.raises(ProviderError) as error:
        calendar.calendar_write(provider, CONFIG[provider], CREDS, fields, remote_id='series-one', expected_etag=current['etag'])
    assert error.value.status == 412
    row = state.instances[provider]['series-one'][ORIGINALS[1]]
    assert row.get('summary', row.get('subject')) == FIELDS['title'] and len(mutators(state)) == 1


@pytest.mark.parametrize('provider', PROVIDERS)
def test_reviewed_invites_background_readonly_and_organizer_cancel(calendar_https, provider):
    state = calendar_https; fields = {key: value for key, value in FIELDS.items() if key != 'recurrence'}
    item = calendar.calendar_publish(provider, CONFIG[provider], CREDS, fields, remote_key='fc-reviewed-invite', attendees=[{'email': 'guest@example.com', 'name': 'Guest'}])
    assert item['read_only'] is True and item['attendees'][0]['email'] == 'guest@example.com'
    request = mutators(state)[0]; assert len(json.loads(request[3])['attendees']) == 1
    if provider == 'google': assert parse_qs(urlsplit(request[1]).query)['sendUpdates'] == ['all']
    before = len(mutators(state))
    with pytest.raises(ProviderError, match='read_only_resource'):
        calendar.calendar_write(provider, CONFIG[provider], CREDS, {**fields, 'title': 'Unreviewed'}, remote_id=item['id'], expected_etag=item['etag'])
    with pytest.raises(ProviderError, match='read_only_resource'):
        calendar.calendar_delete(provider, CONFIG[provider], CREDS, item['id'], item['etag'])
    assert len(mutators(state)) == before
    assert calendar.calendar_cancel(provider, CONFIG[provider], CREDS, item['id'], expected_etag=item['etag'])['deleted']
    assert len(mutators(state)) == before + 1
    assert 'sendUpdates=all' in mutators(state)[-1][1] if provider == 'google' else mutators(state)[-1][1].endswith('/cancel')


@pytest.mark.parametrize('provider', PROVIDERS)
def test_rsvp_only_delegated_self_not_arbitrary_attendee(calendar_https, provider):
    state = calendar_https; create(state, provider, {key: value for key, value in FIELDS.items() if key != 'recurrence'}, attendee=True)
    event = state.events[provider]['series-one']
    if provider == 'google':
        event['organizer']['self'] = False; event['attendees'] += [{'email': 'me@example.com', 'responseStatus': 'needsAction', 'self': True}]
    else: event['isOrganizer'] = False
    current = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one'); assert current['can_respond']
    calendar.calendar_respond(provider, CONFIG[provider], CREDS, 'series-one', 'accepted', expected_etag=current['etag'])
    request = mutators(state)[0]; body = json.loads(request[3])
    if provider == 'google':
        assert body == {'attendeesOmitted': True, 'attendees': [{'email': 'me@example.com', 'responseStatus': 'accepted'}]}
        assert event['attendees'][0]['responseStatus'] == 'needsAction'
    else: assert request[1].endswith('/accept') and body == {'sendResponse': True}
    before = len(mutators(state))
    if provider == 'google': event['organizer']['self'] = True
    else: event['isOrganizer'] = True
    state.versioned(provider, event); organizer = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one')
    with pytest.raises(ProviderError, match='delegated_attendee_required'):
        calendar.calendar_respond(provider, CONFIG[provider], CREDS, 'series-one', 'declined', expected_etag=organizer['etag'])
    assert len(mutators(state)) == before


def test_google_incremental_instance_only_fetches_master(calendar_https):
    state = calendar_https; create(state, 'google'); state.incremental_instance = deepcopy(state.instances['google']['series-one'][ORIGINALS[1]])
    result = calendar.calendar_snapshot('google', CONFIG['google'], CREDS, 'previous-cursor')
    assert result['complete'] is False and result['cursor'] == 'next-cursor' and result['items'][0]['id'] == 'series-one'
    assert any(row[1].endswith('/events/series-one') for row in state.requests)


@pytest.mark.parametrize('destination', ['https://attacker.example.org/token', 'https://graph.microsoft.com/v1.0/me/calendars/other/events?$skiptoken=x', 'https://graph.microsoft.com/v1.0/me/messages?$skiptoken=x'])
def test_graph_pagination_cannot_escape_selected_calendar(calendar_https, destination):
    state = calendar_https; create(state, 'microsoft'); state.unsafe_next = destination
    with pytest.raises(ProviderError, match='unsafe_pagination'): calendar.calendar_snapshot('microsoft', CONFIG['microsoft'], CREDS)
    assert len(state.requests) == 1 and not mutators(state)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_combined_edits_fail_before_any_write(calendar_https, provider):
    state = calendar_https; create(state, provider); current = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one')
    fields = {**current['fields'], 'title': 'New baseline', 'exceptions': [{'original_start': ORIGINALS[1], 'cancelled': True}]}
    with pytest.raises(ProviderError, match='combined_series_edit_requires_separate_review'):
        calendar.calendar_write(provider, CONFIG[provider], CREDS, fields, remote_id='series-one', expected_etag=current['etag'])
    assert not mutators(state)


def test_graph_cancellation_cannot_be_silently_restored(calendar_https):
    state = calendar_https; create(state, 'microsoft'); state.instances['microsoft']['series-one'].pop(ORIGINALS[1]); state.cancelled['microsoft']['series-one'][ORIGINALS[1]] = 'opaque'
    current = calendar.calendar_read('microsoft', CONFIG['microsoft'], CREDS, 'series-one')
    with pytest.raises(ProviderError, match='cancelled_occurrence_requires_new_series'):
        calendar.calendar_write('microsoft', CONFIG['microsoft'], CREDS, {**current['fields'], 'exceptions': []}, remote_id='series-one', expected_etag=current['etag'])
    assert not mutators(state)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_per_instance_attendees_never_silently_flattened(calendar_https, provider):
    state = calendar_https; create(state, provider, attendee=True); row = state.instances[provider]['series-one'][ORIGINALS[1]]
    if provider == 'google': row['attendees'][0]['responseStatus'] = 'declined'
    else: row['attendees'][0]['status']['response'] = 'declined'
    with pytest.raises(ProviderError, match='unsupported_instance_attendees'): calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one')
    assert not mutators(state)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_reviewed_publish_can_add_attendees_to_existing_local_series(calendar_https, provider):
    state = calendar_https; create(state, provider)
    current = calendar.calendar_read(provider, CONFIG[provider], CREDS, 'series-one')
    result = calendar.calendar_publish(provider, CONFIG[provider], CREDS, current['fields'], remote_id='series-one', expected_etag=current['etag'], remote_key='fc-reviewed-existing', attendees=[{'email': 'guest@example.com', 'name': 'Guest'}])
    assert result['read_only'] is True
    writes = mutators(state)
    assert len(writes) == 1 and writes[0][0] == 'PATCH' and '-instance-' not in writes[0][1]


@pytest.mark.parametrize('provider', PROVIDERS)
def test_all_day_series_local_dates_and_exception_across_dst(calendar_https, provider):
    state = calendar_https
    fields = {**FIELDS, 'all_day': True, 'starts_at': '2026-10-23', 'ends_at': '2026-10-24', 'exceptions': [{'original_start': '2026-10-30', 'starts_at': '2026-10-31', 'ends_at': '2026-11-01'}]}
    item = calendar.calendar_write(provider, CONFIG[provider], CREDS, {**fields, 'remote_key': 'fc-all-day'})
    assert item['fields']['starts_at'] == '2026-10-23' and item['fields']['timezone'] == 'Europe/Stockholm'
    exception = item['fields']['exceptions'][0]
    assert exception['original_start'] == '2026-10-30' and exception['starts_at'] == '2026-10-31' and exception['ends_at'] == '2026-11-01'
