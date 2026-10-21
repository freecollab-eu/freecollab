"""Installation readiness stays private, SELECT-only and honest about evidence."""
import base64
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app import main as application  # noqa: F401 -- register complete additive graph
from app.api import pilot_readiness as api
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.operations import WorkerHeartbeat
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import operational_security, pilot_readiness as service
from app.services.auth import create_token, token_digest

BASE = '/operations/pilot-readiness'


def key(byte): return SecretStr(base64.b64encode(bytes([byte]) * 32).decode())
def checks(value): return {row['id']: row for row in value['checks']}


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'test')
    monkeypatch.setattr(settings, 'jwt_secret', 'retained-private-operator-jwt-key-2026')
    monkeypatch.setattr(settings, 'account_security_key', key(71))
    monkeypatch.setattr(settings, 'message_encryption_keys', {'current': key(72)})
    monkeypatch.setattr(settings, 'message_encryption_active_key_id', 'current')
    monkeypatch.setattr(settings, 'public_app_url', 'https://selfhost.example')
    monkeypatch.setattr(settings, 'cors_origins', ['https://selfhost.example'])
    monkeypatch.setattr(settings, 'storage_path', str(tmp_path / 'private-uploads'))
    monkeypatch.setattr(settings, 'mail_delivery_mode', 'smtp')
    monkeypatch.setattr(settings, 'smtp_host', 'private-smtp-host.example')
    monkeypatch.setattr(settings, 'smtp_username', 'private-smtp-user')
    monkeypatch.setattr(settings, 'smtp_password', SecretStr('private-smtp-password'))
    monkeypatch.setattr(settings, 'smtp_from', 'Private Sender <private-sender@private.example>')
    monkeypatch.setattr(settings, 'smtp_starttls', True)
    monkeypatch.setattr(settings, 'smtp_implicit_tls', False)
    monkeypatch.setattr(settings, 'operations_metrics_token', SecretStr('private-metrics-token-' + 'm' * 40))
    for _, name, _ in service.FEATURES: monkeypatch.setattr(settings, name, False)
    monkeypatch.setattr(operational_security.shutil, 'disk_usage', lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3))
    engine = create_engine('sqlite:///' + str(tmp_path / 'readiness.db'), connect_args={'check_same_thread': False})
    with engine.connect() as connection: connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id='operator', name='Private Operator', email='private-operator@private.example', role='admin', password_hash='not-a-real-password'),
                         User(id='owner', name='Workspace Owner', email='owner@private.example', password_hash='not-a-real-password'),
                         User(id='moderator', name='Moderator', email='moderator@private.example', role='moderator', password_hash='not-a-real-password')])
        session.add(Workspace(id='workspace', name='Private Workspace')); session.commit()
        session.add(WorkspaceMember(workspace_id='workspace', user_id='owner', role='owner'))
        session.add(AccountSecurity(user_id='operator', email_verified_at=datetime.utcnow(), totp_secret='opaque-MFA-secret'))
        session.commit()
        tokens = {name: create_token(name, session, mfa_verified=True) for name in ('operator', 'owner', 'moderator')}
    app = FastAPI(); app.include_router(api.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, tokens=tokens, headers=lambda name='operator': {'Authorization': 'Bearer ' + tokens[name]})
    engine.dispose()


def fetch(installation, name='operator'):
    response = installation.client.get(BASE, headers=installation.headers(name))
    assert response.status_code == 200, response.text
    return response


def test_current_native_global_operator_only_workspace_roles_and_metrics_token_do_not_escalate(installation):
    c = installation.client
    assert c.get(BASE).status_code in {401, 403}
    for name in ('owner', 'moderator'): assert c.get(BASE, headers=installation.headers(name)).status_code == 403
    assert c.get(BASE, headers={'Authorization': 'Bearer ' + settings.operations_metrics_token.get_secret_value()}).status_code == 401
    with Session(installation.engine) as session:
        session.add(WorkspaceMember(workspace_id='workspace', user_id='operator', role='owner'))
        session.add(WorkspaceOIDCProvider(workspace_id='workspace', name='Customer SSO', issuer_url='https://idp.example', client_id='private-client', enabled=True, created_by='operator'))
        session.commit()
        scoped = create_token('operator', session, mfa_verified=True, auth_methods=['sso'], workspace_scope={'workspace_id': 'workspace', 'provider_revision': 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id='workspace', provider_revision=1)); session.commit()
    assert c.get(BASE, headers={'Authorization': 'Bearer ' + scoped}).status_code == 403
    response = fetch(installation)
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'


def test_readiness_reuses_real_checks_but_never_claims_public_tls_mail_delivery_or_backup_success(installation, monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'production')
    now = datetime.utcnow()
    with Session(installation.engine) as session:
        session.add(WorkerHeartbeat(name='mail', last_seen_at=now)); session.commit()
    data = fetch(installation).json(); items = checks(data)
    assert data['scope'] == 'local_configuration' and data['public_origin'] == 'https://selfhost.example'
    for identifier in ('production_environment', 'public_https_origin', 'cors_https_origins', 'server_keys', 'operator_mfa', 'mail', 'mail_worker', 'storage', 'metrics'):
        assert items[identifier]['status'] == 'ready'
    assert data['database'] == {'engine': 'sqlite', 'reachable': True} and items['database']['status'] == 'attention'
    assert items['proxy_boundary']['status'] == items['host_boundary']['status'] == 'unknown'
    assert data['workers']['mail']['enabled'] and data['workers']['mail']['status'] == 'ready'
    assert data['workers']['integrations']['enabled'] is False
    assert data['backup'] == {'status': 'unknown', 'evidence_source': 'not_recorded', 'last_backup_at': None, 'last_offsite_verified_at': None, 'last_restore_verified_at': None, 'freshness': 'unknown'}
    assert 'does not verify public TLS' in data['summary']
    assert data['status'] == 'attention'


def test_get_is_select_only_redacted_and_never_decrypts_opens_keyfiles_or_contacts_providers(installation, monkeypatch):
    from app.services import account_security, crypto, mail, remote_transport
    def forbidden(*args, **kwargs): raise AssertionError('Readiness may not open files, decrypt or contact providers')
    for module, name in ((account_security, 'unseal_private'), (crypto, 'decrypt_message'), (mail, 'enqueue_mail'), (remote_transport, 'request')): monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many): statements.append(statement)
    event.listen(installation.engine, 'before_cursor_execute', capture)
    try: response = fetch(installation)
    finally: event.remove(installation.engine, 'before_cursor_execute', capture)
    assert statements and all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    for private in ('private-smtp-host', 'private-smtp-user', 'private-smtp-password', 'private-sender', 'private-operator', 'Private Operator', 'Private Workspace', 'private-metrics-token', 'opaque-MFA-secret', str(installation.engine.url), settings.storage_path): assert private not in response.text
    for value in (settings.account_security_key, *settings.message_encryption_keys.values()): assert value.get_secret_value() not in response.text
    assert settings.jwt_secret not in response.text


@pytest.mark.parametrize('age,expected', [(None, 'unknown'), (0, 'ready'), (179, 'ready'), (181, 'attention'), (-120, 'attention')])
def test_worker_freshness_uses_retained_heartbeats_and_rejects_future_clock_skew(installation, monkeypatch, age, expected):
    monkeypatch.setattr(settings, 'workflows_enabled', True)
    now = datetime.utcnow()
    if age is not None:
        with Session(installation.engine) as session:
            session.add_all([WorkerHeartbeat(name=name, last_seen_at=now - timedelta(seconds=age)) for name in ('mail', 'integrations')]); session.commit()
    with Session(installation.engine) as session: data = service.snapshot(session, 'operator', now=now)
    for name in ('mail', 'integrations'):
        assert data['workers'][name]['status'] == expected and data['workers'][name]['enabled']
        assert data['workers'][name]['age_seconds'] == max(0, age) if age is not None else data['workers'][name]['age_seconds'] is None
    assert checks(data)['integration_worker']['status'] == expected


def test_file_mail_sink_and_on_demand_features_do_not_claim_production_smtp_or_background_worker_need(installation, monkeypatch):
    monkeypatch.setattr(settings, 'mail_delivery_mode', 'file')
    for flag in ('cloud_files_enabled', 'copilot_enabled', 'external_meetings_enabled'): monkeypatch.setattr(settings, flag, True)
    data = fetch(installation).json()
    assert checks(data)['mail']['status'] == 'unconfigured' and data['mail']['mode'] == 'file'
    assert not data['workers']['integrations']['enabled'] and data['workers']['integrations']['status'] == 'ready'
    assert all(not row['worker_required'] for row in data['features'] if row['id'] in {'copilot', 'cloud_files', 'external_meetings'})


@pytest.mark.parametrize('revoke', ['role', 'block', 'auth_version', 'session'])
def test_operator_authority_is_rechecked_after_diagnostic_queries(installation, revoke):
    touched = []
    def after_query(_connection, _cursor, statement, _parameters, _context, _many):
        if touched or 'FROM mailoutbox' not in statement: return
        touched.append(True)
        with Session(installation.engine) as session:
            if revoke in {'role', 'block'}:
                row = session.get(User, 'operator')
                if revoke == 'role': row.role = 'user'
                else: row.is_blocked = True
            elif revoke == 'auth_version': row = session.get(AccountSecurity, 'operator'); row.auth_version += 1
            else:
                row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(installation.tokens['operator']))).one(); row.revoked_at = datetime.utcnow()
            session.add(row); session.commit()
    event.listen(installation.engine, 'after_cursor_execute', after_query)
    try: response = installation.client.get(BASE, headers=installation.headers())
    finally: event.remove(installation.engine, 'after_cursor_execute', after_query)
    assert touched and response.status_code in {401, 403} and 'checks' not in response.json()


def test_untracked_operator_token_is_not_bootstrapped_by_a_read(installation):
    digest = token_digest(installation.tokens['operator'])
    with Session(installation.engine) as session:
        session.delete(session.exec(select(ActiveSession).where(ActiveSession.token_hash == digest)).one()); session.commit()
    assert installation.client.get(BASE, headers=installation.headers()).status_code == 401
    with Session(installation.engine) as session:
        assert session.exec(select(ActiveSession).where(ActiveSession.token_hash == digest)).first() is None
