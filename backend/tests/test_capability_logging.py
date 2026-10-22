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
