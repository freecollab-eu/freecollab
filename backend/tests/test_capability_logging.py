"""Bearer URLs must stay private in real Uvicorn formatted access/error records."""
import logging

import pytest
from uvicorn.logging import AccessFormatter

from app.services.capability_logs import CapabilityLogFilter


@pytest.mark.parametrize('path', [
    '/calendar/subscriptions/feed-id/unguessable-feed-secret.ics?window=month',
    '/api/calendar/subscriptions/feed-id/unguessable-feed-secret.ics',
    '/%63alendar/subscriptions/feed-id/unguessable-feed-secret.ics',
    '/api/calendar%2fsubscriptions/feed-id/unguessable-feed-secret.ics',
    '/office-edit/unguessable-office-secret/file.docx',
    '/api/%6fffice-edit/unguessable-office-secret/file.docx',
    '/personal-oauth/callback?state=unguessable-oauth-secret&code=one-use-code',
    '/api/personal-oauth/%63allback?state=unguessable-oauth-secret&code=one-use-code',
])
def test_access_formatter_retains_status_without_capability_even_for_encoded_routing_prefix(path):
    record = logging.LogRecord('uvicorn.access',logging.INFO,__file__,1,'%s - "%s %s HTTP/%s" %d',
        ('127.0.0.1:12345','GET',path,'1.1',200),None)
    assert CapabilityLogFilter().filter(record)
    result = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',use_colors=False).format(record)
    assert 'GET ' in result and 'HTTP/1.1' in result and '200 OK' in result
    assert '[redacted]' in result
    assert 'unguessable-' not in result and 'one-use-code' not in result
    assert len(record.args) == 5


def test_named_error_arguments_and_literal_urls_keep_multiple_capabilities_private():
    value = '/api/calendar/subscriptions/feed/unguessable-feed-secret.ics'
    record = logging.LogRecord('uvicorn.error',logging.ERROR,__file__,1,
        'Failed %(url)s; /personal-oauth/callback?code=one-use-code',({'url':value},),None)
    CapabilityLogFilter().filter(record)
    result = logging.Formatter('%(message)s').format(record)
    assert '[redacted]' in result
    assert 'unguessable-' not in result and 'one-use-code' not in result


def test_ordinary_encoded_paths_and_format_arguments_are_preserved():
    path = '/documents/quarter%20one?search=planning'
    record = logging.LogRecord('uvicorn.access',logging.INFO,__file__,1,'%s %s',('GET',path),None)
    CapabilityLogFilter().filter(record)
    assert record.getMessage() == 'GET ' + path


@pytest.mark.parametrize('callback', [
    'auth/oidc/callback',
    'cloud-files/oauth/callback',
    'external-meetings/oauth/callback',
    'sync/oauth/callback',
    'personal-oauth/callback',
])
@pytest.mark.parametrize('prefix,suffix', [
    ('/', ''), ('/api/', ''), ('/%61pi/', ''),
    ('/api/', '/'), ('/api/', '%2f'),
])
def test_every_registered_oauth_callback_hides_codes_and_state(callback, prefix, suffix):
    # Include both native routing and encoded upstream representations, plus
    # FastAPI's trailing-slash redirect path before the callback is consumed.
    path = prefix + callback.replace('callback', '%63allback') + suffix
    secret_query = '?code=fixture-private-code&state=fixture-private-state&error_description=fixture-private-description'
    record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ('127.0.0.1:12345', 'GET', path + secret_query, '1.1', 307), None)
    assert CapabilityLogFilter().filter(record)
    result = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert 'GET ' + path + '?[redacted] HTTP/1.1' in result
    assert '307 Temporary Redirect' in result
    assert 'fixture-private-' not in result
    assert len(record.args) == 5


def test_oauth_error_records_hide_multiple_callbacks_without_losing_metadata():
    record = logging.LogRecord('uvicorn.error', logging.ERROR, __file__, 1,
        'OIDC %(url)s failed; /api/sync/oauth/callback?code=fixture-private-second',
        ({'url': '/auth/oidc/callback?state=fixture-private-state&error_description=fixture-private-description'},), None)
    CapabilityLogFilter().filter(record)
    result = record.getMessage()
    assert result == 'OIDC /auth/oidc/callback?[redacted] failed; /api/sync/oauth/callback?[redacted]'


@pytest.mark.parametrize('path', [
    '/auth/oidc/providers?search=planning',
    '/sync/oauth/callback-history?code=search-term',
    '/external-meetings/oauth/callback-notes?state=calendar',
])
def test_non_callback_routes_keep_their_ordinary_query_metadata(path):
    record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1, '%s %s', ('GET', path), None)
    CapabilityLogFilter().filter(record)
    assert record.getMessage() == 'GET ' + path
