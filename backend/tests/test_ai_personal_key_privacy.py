"""A real key INSERT must remain serialized with actual account closure."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import event
from sqlmodel import Session

from app.models.ai_runtime import AIPersonalKey
from app.models.user import User
from tests.test_data_controls import controls, headers, PASSWORD
from tests.test_copilot import ai
from tests.test_ai_runtime import profile, PREFIX


def test_private_key_final_insert_cannot_resurrect_credentials_after_actual_account_closure(ai, monkeypatch):
    client, engine, _, _ = ai
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member', lambda *_: None)
    revision = profile(ai, key_source='personal', api_key=None)
    preview = client.get('/account/data-closure', headers=headers(ai)).json()
    assert preview['can_erase_identity']
    held, release, closure_attempt, closure_wrote = Event(), Event(), Event(), Event()
    def before(connection, cursor, statement, parameters, context, many):
        sql = statement.lower()
        if sql.startswith('insert into aipersonalkey'):
            held.set(); assert release.wait(5), 'The private key insert was not released'
        elif held.is_set() and sql.startswith('delete from accountratebucket'):
            closure_attempt.set()
    def after(connection, cursor, statement, parameters, context, many):
        if held.is_set() and statement.lower().startswith('delete from accountratebucket'):
            closure_wrote.set()
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            saving = pool.submit(client.put, PREFIX+'/personal-keys/fast', headers=headers(ai), json={
                'password': PASSWORD, 'expected_profile_revision': revision, 'api_key': 'synthetic-private-key-to-erase'})
            assert held.wait(5)
            closing = pool.submit(client.post, '/account/data-closure', headers=headers(ai), json={
                'password': PASSWORD, 'confirmation': 'ERASE MY ACCOUNT', 'preview_fingerprint': preview['fingerprint'],
                'acknowledge_retained_shared_authorship_and_backups': True})
            assert closure_attempt.wait(5)
            assert not closure_wrote.wait(.2), 'Closure passed the private key writer fence before its final INSERT'
            release.set(); saved = saving.result(timeout=10); closed = closing.result(timeout=10)
        assert saved.status_code == 200, saved.text
        assert closed.status_code == 200, closed.text
        with Session(engine) as session:
            assert session.get(User, 'owner').is_blocked
            assert session.get(AIPersonalKey, ('a', 'fast', 'owner')) is None
        denied = client.put(PREFIX+'/personal-keys/fast', headers=headers(ai), json={
            'password': PASSWORD, 'expected_profile_revision': revision, 'api_key': 'another-private-key'})
        assert denied.status_code == 401
    finally:
        release.set()
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)
