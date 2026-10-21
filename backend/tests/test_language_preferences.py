"""A language choice belongs to the authenticated account across sessions."""
import logging
import pytest
from sqlmodel import Session, select
from app.models.preference import UserLanguage
from app.models.user import User
from app.services.auth import create_token, revoke_token
from app.services.capability_logs import CapabilityLogFilter
from tests.test_preferences import appearance, bearer


def test_language_is_personal_persistent_and_browser_default_is_explicit(appearance):
    client, engine, _ = appearance
    initial = client.get('/preferences/language', headers=bearer())
    assert initial.status_code == 200 and initial.json() == {'locale': 'auto'}
    assert 'no-store' in initial.headers['cache-control']
    with Session(engine) as session:
        assert session.exec(select(UserLanguage)).all() == []
    for value in ('sv', 'en', 'auto', 'sv'):
        response = client.put('/preferences/language', headers=bearer(), json={'locale': value})
        assert response.status_code == 200 and response.json() == {'locale': value}
    # A new token/session and database session see the same account choice.
    assert client.get('/preferences/language', headers=bearer()).json() == {'locale': 'sv'}
    assert client.get('/preferences/language', headers=bearer('bob')).json() == {'locale': 'auto'}
    with Session(engine) as session:
        assert session.get(UserLanguage, 'alice').locale == 'sv'
        assert session.get(UserLanguage, 'bob') is None


@pytest.mark.parametrize('payload', [{}, {'locale': 'xx'}, {'locale': '<script>'}, {'locale': 'sv-SE'}, {'locale': 1}, {'locale': None}, {'locale': 'sv', 'user_id': 'bob'}])
def test_language_rejects_unregistered_locales_and_account_targeting(appearance, payload):
    client, engine, _ = appearance
    assert client.put('/preferences/language', headers=bearer(), json=payload).status_code == 422
    with Session(engine) as session:
        assert session.exec(select(UserLanguage)).all() == []


def test_language_rejects_missing_blocked_and_revoked_identity(appearance):
    client, engine, _ = appearance
    assert client.put('/preferences/language', json={'locale': 'sv'}).status_code in (401, 403)
    token = create_token('alice')
    with Session(engine) as session:
        revoke_token(token, session)
    assert client.put('/preferences/language', headers={'Authorization': 'Bearer ' + token}, json={'locale': 'sv'}).status_code == 401
    with Session(engine) as session:
        user = session.get(User, 'bob'); user.is_blocked = True; session.add(user); session.commit()
    assert client.put('/preferences/language', headers=bearer('bob'), json={'locale': 'sv'}).status_code == 403


def test_office_access_log_redacts_the_capability_and_filename():
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s "%s %s HTTP/%s" %d', ('127.0.0.1', 'GET', '/office-edit/session/SECRET/private.docx?download=1', '1.1', 200), None)
    CapabilityLogFilter().filter(record)
    assert 'SECRET' not in record.getMessage() and 'private.docx' not in record.getMessage()
    assert '/office-edit/[redacted]' in record.getMessage()
