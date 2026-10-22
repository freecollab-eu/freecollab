"""Two-way native content, durable conflicts, revocation and tenant-bound OAuth."""
import base64
import copy
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import auth, files, integrations_content, remote_sync
from app.config import settings
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.file_version import FileDocument, FileVersion
from app.models.integration_content import IntegrationContentRevision
from app.models.remote_sync import RemoteSyncConnection, RemoteSyncConflict, RemoteSyncItem
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workflow import WorkflowDefinition  # Register event-hook tables before isolated fixture create_all.
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import remote_providers as providers
from app.services import remote_sync as sync
from app.services.auth import create_token, hash_password, token_digest
from app.services.storage import StorageService

PASSWORD = "local-sync-owner-password"
EVENT = {"title": "Planning", "description": "Agenda", "location": "Studio",
    "starts_at": "2026-10-20T10:00:00Z", "ends_at": "2026-10-20T11:00:00Z",
    "all_day": False, "timezone": "Europe/Stockholm", "reminder_minutes": 15, "external_uid": "event@example.invalid"}


class Remote:
    def __init__(self):
        self.items, self.values, self.writes, self.deletes = {}, {}, [], []
        self.calls, self.counter, self.before_read = 0, 0, None

    def add(self, identity="remote-file", value=b"remote bytes", fields=None, read_only=False):
        self.counter += 1
        self.items[identity] = {"id": identity, "etag": f'"remote-{self.counter}"', "name": "notes.txt",
            "mime_type": "text/plain", "deleted": False, "read_only": read_only}
        self.values[identity] = {"fields": fields} if fields else {"bytes": value, "mime_type": "text/plain"}
        return self.items[identity]

    def snapshot(self, *args, **kwargs):
        self.calls += 1
        return {"items": copy.deepcopy(list(self.items.values())), "complete": True, "cursor": None}

    def read(self, provider, kind, config, credentials, item):
        if self.before_read:
            self.before_read()
        return {**copy.deepcopy(self.values[item["id"]]), "etag": self.items[item["id"]]["etag"]}

    def write(self, provider, kind, config, credentials, resource, remote_id=None, expected_etag=None):
        self.writes.append((remote_id, expected_etag))
        if remote_id:
            if remote_id not in self.items or self.items[remote_id]["etag"] != expected_etag:
                raise providers.ProviderError("conflict", status=412)
        remote_id = remote_id or "created-" + str(len(self.writes))
        result = self.add(remote_id, resource.get("bytes", b""),
                          {key: value for key, value in resource.items() if key != "remote_key"} if kind == "calendar" else None)
        result["remote_key"] = resource["remote_key"]
        if kind == "files":
            result.update(name=resource["name"], mime_type=resource["mime_type"])
        return copy.deepcopy(result)

    def delete(self, provider, kind, config, credentials, remote_id, expected_etag=None):
        assert self.items[remote_id]["etag"] == expected_etag
        self.deletes.append(remote_id)
        self.items.pop(remote_id)


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sync_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "isolated-remote-sync-fixture-signing-secret")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "public_app_url", "http://localhost")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"a" * 32).decode()))
    monkeypatch.setattr(settings, "message_encryption_keys", {"sync-test": SecretStr(base64.b64encode(b"b" * 32).decode())})
    monkeypatch.setattr(settings, "message_encryption_active_key_id", "sync-test")
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "files"))
    storage = StorageService()
    monkeypatch.setattr(files, "storage_service", storage)
    monkeypatch.setattr(sync, "storage_service", storage)
    engine = create_engine(f"sqlite:///{tmp_path / 'sync.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        password_hash = hash_password(PASSWORD)
        for name in ("owner", "member", "outsider", "private-admin"):
            session.add(User(id=name, name=name, email=name + "@example.com", password_hash=password_hash))
        session.add_all([Workspace(id="a", name="A"), Workspace(id="b", name="B")])
        session.flush()
        for workspace, user, role in (("a", "owner", "owner"), ("a", "member", "member"),
                                      ("a", "private-admin", "admin"), ("b", "outsider", "owner")):
            session.add(WorkspaceMember(workspace_id=workspace, user_id=user, role=role))
        for channel, workspace in (("room", "a"), ("private", "a"), ("other", "b")):
            session.add(Channel(id=channel, name=channel))
            session.add(WorkspaceChannel(channel_id=channel, workspace_id=workspace))
        session.add_all([ChannelMember(channel_id="room", user_id="owner", role="admin"),
            ChannelMember(channel_id="room", user_id="member"),
            ChannelMember(channel_id="private", user_id="private-admin", role="admin"),
            ChannelMember(channel_id="other", user_id="outsider", role="admin")])
        session.commit()
        tokens = {name: create_token(name, session) for name in ("owner", "member", "outsider", "private-admin")}
    app = FastAPI()
    for module in (auth, files, integrations_content, remote_sync):
        app.include_router(module.router)
    app.include_router(remote_sync.oauth_router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    remote = Remote()
    monkeypatch.setattr(providers, "snapshot", remote.snapshot)
    monkeypatch.setattr(providers, "read_item", remote.read)
    monkeypatch.setattr(providers, "write_item", remote.write)
    monkeypatch.setattr(providers, "delete_item", remote.delete)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, remote=remote, storage=storage,
            headers={name: {"Authorization": "Bearer " + token} for name, token in tokens.items()}, tokens=tokens)
    engine.dispose()


def draft(kind="files", **overrides):
    return {"name": "Our cloud", "provider": "nextcloud", "kind": kind, "channel_id": "room",
        "config": {"base_url": "https://cloud.example.com/collection/"},
        "credentials": {"username": "customer", "app_password": "private-app-password"},
        "password": PASSWORD, "consent": True, **overrides}


def create(team, kind="files", **overrides):
    response = team.client.post("/workspaces/a/sync", headers=team.headers["owner"], json=draft(kind, **overrides))
    assert response.status_code == 200, response.text
    return response.json()


def action(team, connection, name, **fields):
    return team.client.post(f'/workspaces/a/sync/{connection["id"]}/{name}', headers=team.headers["owner"],
                            json={"password": PASSWORD, **fields})


def activate(team, connection):
    assert action(team, connection, "test").status_code == 200
    response = action(team, connection, "enable")
    assert response.status_code == 200, response.text


def cycle(team, connection):
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection["id"])
        row.next_run_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row)
        session.commit()
        result = sync.process_pending(session)
    return result


def upload(team, value=b"local bytes", version=None):
    path = f'/files/{version}/versions' if version else '/files/channels/room'
    response = team.client.post(path, files={"file": ("local.txt", value, "text/plain")}, headers=team.headers["owner"])
    assert response.status_code == 200, response.text
    return response.json()


def test_write_only_workspace_secrets_explicit_consent_and_member_disclosure(team):
    assert team.client.post('/workspaces/a/sync', headers=team.headers['owner'], json=draft(consent=False)).status_code == 422
    connection = create(team)
    listed = team.client.get('/workspaces/a/sync', headers=team.headers['owner'])
    assert 'private-app-password' not in listed.text and 'customer' not in listed.text
    assert connection['has_credentials'] is True and connection['enabled'] is False
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert 'private-app-password' not in row.encrypted_credentials
        assert sync.credentials(row)['app_password'] == 'private-app-password'
    assert team.client.get('/workspaces/a/sync', headers=team.headers['member']).status_code == 403
    assert team.client.get('/workspaces/a/sync', headers=team.headers['outsider']).status_code == 403
    disclosure = team.client.get('/channels/room/sync-status', headers=team.headers['member'])
    assert disclosure.status_code == 200 and disclosure.json()['connections'][0]['provider'] == 'nextcloud'
    assert 'config' not in disclosure.text and 'credential' not in disclosure.text
    assert team.client.get('/channels/room/sync-status', headers=team.headers['outsider']).status_code == 403
    assert team.client.get('/workspaces/a/sync', headers=team.headers['private-admin']).json()['connections'] == []
    path = f'/workspaces/a/sync/{connection["id"]}/disconnect'
    assert team.client.post(path, json={'password': PASSWORD}, headers=team.headers['private-admin']).status_code == 403


def test_wrong_password_private_channels_secure_channels_and_disabled_feature_fail_closed(team, monkeypatch):
    assert team.client.post('/workspaces/a/sync', headers=team.headers['owner'], json=draft(password='wrong')).status_code == 403
    assert team.client.post('/workspaces/a/sync', headers=team.headers['owner'], json=draft(channel_id='other')).status_code == 403
    with Session(team.engine) as session:
        session.add(SecureChannel(channel_id='room', matrix_room_id='!room:local.test', status='preparing', created_by='owner'))
        session.commit()
    response = team.client.post('/workspaces/a/sync', headers=team.headers['owner'], json=draft())
    assert response.status_code == 409 and response.json()['detail']['code'] == 'unsupported_secure_channel'
    with Session(team.engine) as session:
        session.delete(session.get(SecureChannel, 'room')); session.commit()
    connection = create(team)
    assert action(team, connection, 'enable').status_code == 409
    monkeypatch.setattr(settings, 'sync_enabled', False)
    assert action(team, connection, 'test').status_code == 503
    with Session(team.engine) as session:
        assert sync.process_pending(session) == 0
    assert team.remote.calls == 0


def test_two_way_file_bytes_versions_and_soft_deletion_preserve_history(team):
    local = upload(team)
    team.remote.add()
    connection = create(team); activate(team, connection)
    assert cycle(team, connection) == 1
    records = team.client.get('/files/channels/room', headers=team.headers['owner']).json()
    imported = next(row for row in records if row['document_id'] != local['document_id'])
    assert team.client.get('/files/' + imported['id'], headers=team.headers['member']).content == b'remote bytes'
    assert team.remote.values['created-1']['bytes'] == b'local bytes'
    team.remote.add(value=b'updated remote')
    assert cycle(team, connection) == 1
    versions = team.client.get('/files/' + imported['id'] + '/versions', headers=team.headers['owner']).json()
    assert len(versions) == 2
    assert team.client.get('/files/' + versions[0]['id'], headers=team.headers['owner']).content == b'updated remote'
    assert team.client.get('/files/' + imported['id'], headers=team.headers['owner']).content == b'remote bytes'
    team.remote.items.pop('remote-file')
    assert cycle(team, connection) == 1
    assert len(team.client.get('/files/channels/room', headers=team.headers['owner']).json()) == 1
    assert team.client.get('/files/' + imported['id'], headers=team.headers['owner']).content == b'remote bytes'
    response = team.client.delete('/files/' + local['id'], headers=team.headers['owner'])
    assert response.json()['retained_versions'] is True
    assert cycle(team, connection) == 1 and 'created-1' in team.remote.deletes
    assert team.client.get('/files/' + local['id'], headers=team.headers['member']).content == b'local bytes'
    assert team.client.get('/files/channels/room', headers=team.headers['owner']).json() == []


def test_simultaneous_changes_wait_for_explicit_resolution_with_etag(team):
    team.remote.add()
    connection = create(team); activate(team, connection); assert cycle(team, connection) == 1
    imported = team.client.get('/files/channels/room', headers=team.headers['owner']).json()[0]
    upload(team, b'local competing edit', imported['id'])
    team.remote.add(value=b'remote competing edit')
    assert cycle(team, connection) == 1
    assert not team.remote.writes
    conflicts = team.client.get(f'/workspaces/a/sync/{connection["id"]}/conflicts', headers=team.headers['owner']).json()['conflicts']
    assert len(conflicts) == 1 and conflicts[0]['reason'] == 'both_changed'
    response = action(team, connection, f'conflicts/{conflicts[0]["id"]}/resolve', choice='local')
    assert response.status_code == 200
    assert cycle(team, connection) == 1
    assert team.remote.values['remote-file']['bytes'] == b'local competing edit'
    assert team.remote.writes[0][1] is not None
    assert team.client.get(f'/workspaces/a/sync/{connection["id"]}/conflicts', headers=team.headers['owner']).json()['conflicts'] == []


def test_calendar_two_way_revisions_and_remote_tombstones_are_encrypted(team):
    team.remote.add(fields=EVENT)
    connection = create(team, 'calendar'); activate(team, connection); assert cycle(team, connection) == 1
    items = team.client.get('/channels/room/integrations/calendar', headers=team.headers['member']).json()
    assert len(items) == 1 and items[0]['fields']['title'] == 'Planning'
    with Session(team.engine) as session:
        row = session.exec(select(IntegrationContentRevision)).one()
        assert 'Planning' not in row.encrypted_payload
    item = items[0]
    fields = {**item['fields'], 'title': 'Local calendar edit'}
    edited = team.client.post('/channels/room/integrations/calendar', headers=team.headers['owner'],
        json={'id': item['id'], 'expected_revision': item['revision'], 'fields': fields})
    assert edited.status_code == 200
    assert cycle(team, connection) == 1
    assert team.remote.values['remote-file']['fields']['title'] == 'Local calendar edit'
    team.remote.items.pop('remote-file')
    assert cycle(team, connection) == 1
    assert team.client.get('/channels/room/integrations/calendar', headers=team.headers['member']).json() == []
    history = team.client.get(f'/channels/room/integrations/calendar/{item["id"]}/revisions', headers=team.headers['member']).json()
    assert len(history) == 3 and history[0]['deleted'] is True


def test_calendar_provider_rsvp_metadata_preserves_immutable_native_revision(team):
    team.remote.add(fields=EVENT, read_only=True)
    connection = create(team, 'calendar'); activate(team, connection); assert cycle(team, connection) == 1
    before = team.client.get('/channels/room/integrations/calendar', headers=team.headers['member']).json()[0]
    team.remote.counter += 1
    team.remote.items['remote-file'].update(etag=f'"remote-{team.remote.counter}"',
        attendees=[{'email': 'guest@example.com', 'response': 'accepted'}])
    assert cycle(team, connection) == 1
    after = team.client.get('/channels/room/integrations/calendar', headers=team.headers['member']).json()[0]
    assert after == before
    with Session(team.engine) as session:
        assert len(session.exec(select(IntegrationContentRevision)).all()) == 1
        item = session.exec(select(RemoteSyncItem)).one()
        assert item.base_remote_etag == team.remote.items['remote-file']['etag']
        assert sync._remote_snapshot(item)['attendees'][0]['response'] == 'accepted'
    assert not team.remote.writes


@pytest.mark.parametrize('revocation', ['blocked', 'workspace', 'channel', 'secure'])
def test_worker_rechecks_grant_and_never_writes_after_access_revocation(team, revocation):
    upload(team)
    connection = create(team); activate(team, connection)
    with Session(team.engine) as session:
        if revocation == 'blocked':
            row = session.get(User, 'owner'); row.is_blocked = True; session.add(row)
        elif revocation == 'workspace':
            session.delete(session.get(WorkspaceMember, ('a', 'owner')))
        elif revocation == 'channel':
            row = session.get(ChannelMember, ('room', 'owner')); row.role = 'member'; session.add(row)
        else:
            session.add(SecureChannel(channel_id='room', matrix_room_id='!room:local.test', created_by='owner', status='preparing'))
        session.commit()
    before = team.remote.calls
    assert cycle(team, connection) == 0
    assert team.remote.calls == before and not team.remote.writes


def test_revocation_during_remote_download_discards_response(team):
    team.remote.add()
    connection = create(team); activate(team, connection)
    def revoke():
        with Session(team.engine) as session:
            session.delete(session.get(ChannelMember, ('room', 'owner'))); session.commit()
    team.remote.before_read = revoke
    assert cycle(team, connection) == 0
    with Session(team.engine) as session:
        assert session.exec(select(File)).all() == []


def test_scoped_sso_token_cannot_configure_connections_but_sees_safe_channel_notice(team):
    create(team)
    with Session(team.engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id='a', name='IdP', issuer_url='https://idp.example.com', client_id='client', created_by='owner', enabled=True))
        session.commit()
        token = create_token('owner', session, auth_methods=['sso'], workspace_scope={'workspace_id': 'a', 'provider_revision': 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id='a', provider_revision=1)); session.commit()
    headers = {'Authorization': 'Bearer ' + token}
    assert team.client.get('/workspaces/a/sync', headers=headers).status_code == 403
    assert team.client.get('/channels/room/sync-status', headers=headers).status_code == 200
    assert team.client.get('/channels/other/sync-status', headers=headers).status_code == 403


def test_oauth_browser_state_pkce_replay_revision_and_secret_storage(team, monkeypatch):
    monkeypatch.setattr(settings, 'public_app_url', 'http://127.0.0.1')
    # TestClient cookie domain follows testserver; callback localhost policy is
    # selected through a local test origin without weakening provider transport.
    monkeypatch.setattr(settings, 'public_app_url', 'http://localhost')
    connection = create(team, provider='google', config={'client_id': 'our-customer-client', 'folder_id': 'selected-folder'},
                        credentials={'client_secret': 'customer-oauth-secret'})
    started = action(team, connection, 'oauth/start')
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()['authorization_url']).query)
    assert query['client_id'] == ['our-customer-client'] and query['redirect_uri'] == ['http://localhost/api/sync/oauth/callback']
    def exchange(provider, config, redirect, code, verifier):
        assert config['client_secret'] == 'customer-oauth-secret'
        assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode() == query['code_challenge'][0]
        return {'access_token': 'private-provider-access', 'refresh_token': 'private-provider-refresh'}
    monkeypatch.setattr(providers, 'exchange_code', exchange)
    callback = team.client.get('/sync/oauth/callback', params={'state': query['state'][0], 'code': 'one-use-code'}, follow_redirects=False)
    assert callback.status_code == 303 and 'sync_connected=' in callback.headers['location']
    assert 'private-provider' not in callback.headers['location']
    replay = team.client.get('/sync/oauth/callback', params={'state': query['state'][0], 'code': 'one-use-code'}, follow_redirects=False)
    assert 'sync_error=' in replay.headers['location']
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert 'private-provider' not in row.encrypted_credentials
        assert sync.credentials(row)['refresh_token'] == 'private-provider-refresh'
    assert 'private-provider' not in team.client.get('/workspaces/a/sync', headers=team.headers['owner']).text


def test_oauth_does_not_finish_after_native_logout(team, monkeypatch):
    monkeypatch.setattr(settings, 'public_app_url', 'http://localhost')
    connection = create(team, provider='google', config={'client_id': 'client', 'folder_id': 'folder'}, credentials={})
    started = action(team, connection, 'oauth/start').json()
    state = parse_qs(urlsplit(started['authorization_url']).query)['state'][0]
    with Session(team.engine) as session:
        tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(team.tokens['owner']))).one()
        tracked.revoked_at = datetime.utcnow(); session.add(tracked); session.commit()
    def unexpected(*args):
        raise AssertionError('Revoked native session exchanged a provider code')
    monkeypatch.setattr(providers, 'exchange_code', unexpected)
    response = team.client.get('/sync/oauth/callback', params={'state': state, 'code': 'code'}, follow_redirects=False)
    assert 'sync_error=' in response.headers['location']


def test_disabled_connection_and_disconnect_preserve_content_and_stop_dispatch(team):
    team.remote.add()
    connection = create(team); activate(team, connection); assert cycle(team, connection) == 1
    assert action(team, connection, 'disconnect').status_code == 200
    count = team.remote.calls
    assert cycle(team, connection) == 0 and team.remote.calls == count
    assert len(team.client.get('/files/channels/room', headers=team.headers['owner']).json()) == 1
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.encrypted_credentials is None and row.enabled is False


def test_create_timeout_is_not_blindly_retried_and_deduplicates_remote_key(team, monkeypatch):
    upload(team)
    connection = create(team); activate(team, connection)
    actual = team.remote.write
    def ambiguous(*args, **kwargs):
        actual(*args, **kwargs)
        raise providers.ProviderError('provider_timeout')
    monkeypatch.setattr(providers, 'write_item', ambiguous)
    assert cycle(team, connection) == 0
    assert len(team.remote.writes) == 1
    monkeypatch.setattr(providers, 'write_item', actual)
    assert cycle(team, connection) == 1
    assert len(team.remote.writes) == 1
    with Session(team.engine) as session:
        mapping = session.exec(select(RemoteSyncItem)).one()
        assert mapping.remote_id == 'created-1' and mapping.pending_operation is None


def test_incremental_snapshot_absence_does_not_delete_an_existing_remote_item(team, monkeypatch):
    team.remote.add()
    connection = create(team); activate(team, connection); assert cycle(team, connection) == 1
    def delta(*args, **kwargs):
        return {'items': [], 'complete': False, 'cursor': 'opaque-next-delta'}
    monkeypatch.setattr(providers, 'snapshot', delta)
    assert cycle(team, connection) == 1
    assert len(team.client.get('/files/channels/room', headers=team.headers['member']).json()) == 1
    assert not team.remote.deletes


def test_bounded_catalog_scan_continues_without_losing_or_duplicating_items(team):
    for index in range(25):
        team.remote.add(identity='remote-' + str(index), value=('payload' + str(index)).encode())
    connection = create(team); activate(team, connection)
    assert cycle(team, connection) == 1
    assert len(team.client.get('/files/channels/room', headers=team.headers['member']).json()) == 20
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.scan_after_id and row.status == 'queued'
    assert cycle(team, connection) == 1
    assert len(team.client.get('/files/channels/room', headers=team.headers['member']).json()) == 25
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.scan_after_id is None and row.status == 'connected'
        assert len(session.exec(select(RemoteSyncItem)).all()) == 25


def test_refresh_rotation_is_persisted_even_if_next_provider_request_fails(team, monkeypatch):
    connection = create(team, provider='google', config={'client_id': 'client', 'folder_id': 'folder'}, credentials={})
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        sync.save_credentials(row, {'access_token': 'expired', 'refresh_token': 'original-refresh'})
        row.enabled = True
        session.add(row); session.commit()
    calls = []
    def snapshot(*args, **kwargs):
        calls.append(True)
        raise providers.ProviderError('authorization_required' if len(calls) == 1 else 'provider_timeout', status=401 if len(calls) == 1 else 503)
    monkeypatch.setattr(providers, 'snapshot', snapshot)
    monkeypatch.setattr(providers, 'refresh_tokens', lambda *args: {'access_token': 'new-access', 'refresh_token': 'rotated-refresh'})
    assert cycle(team, connection) == 0
    assert len(calls) == 2
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert sync.credentials(row)['refresh_token'] == 'rotated-refresh'
        assert row.last_error == 'provider_timeout'


def test_read_only_export_local_delete_hides_copy_without_deleting_or_resurrecting_source(team):
    team.remote.add(read_only=True)
    connection = create(team); activate(team, connection); assert cycle(team, connection) == 1
    imported = team.client.get('/files/channels/room', headers=team.headers['member']).json()[0]
    response = team.client.delete('/files/' + imported['id'], headers=team.headers['owner'])
    assert response.json()['retained_versions'] is True
    assert cycle(team, connection) == 1
    team.remote.add(value=b'updated readonly source', read_only=True)
    assert cycle(team, connection) == 1
    assert not team.remote.deletes and not team.remote.writes
    assert team.client.get('/files/channels/room', headers=team.headers['member']).json() == []
    assert team.client.get('/files/' + imported['id'], headers=team.headers['member']).content == b'remote bytes'


def test_synced_file_delete_before_first_worker_pass_retains_original_versions(team):
    connection = create(team)
    local = upload(team)
    response = team.client.delete('/files/' + local['id'], headers=team.headers['owner'])
    assert response.json()['retained_versions'] is True
    activate(team, connection); assert cycle(team, connection) == 1
    assert not team.remote.writes
    assert team.client.get('/files/' + local['id'], headers=team.headers['member']).content == b'local bytes'


def test_duplicate_collection_cannot_create_an_import_echo_and_secret_rotation_retains_connection(team):
    connection = create(team)
    duplicate = team.client.post('/workspaces/a/sync', headers=team.headers['owner'], json=draft(name='Another name'))
    assert duplicate.status_code == 409
    changed = draft(credentials={'app_password': 'rotated-app-password'})
    rotated = team.client.put('/workspaces/a/sync/' + connection['id'], headers=team.headers['owner'], json=changed)
    assert rotated.status_code == 200 and rotated.json()['id'] == connection['id']
    with Session(team.engine) as session:
        assert sync.credentials(session.get(RemoteSyncConnection, connection['id']))['app_password'] == 'rotated-app-password'


def test_revision_race_cannot_lazily_switch_network_request_to_a_new_collection(team, monkeypatch):
    connection = create(team); activate(team, connection)
    actual = sync._provider_call
    observed = []
    def snapshot(provider, kind, config, credentials, cursor=None):
        observed.append(config['base_url'])
        return {'items': [], 'complete': True, 'cursor': None}
    monkeypatch.setattr(providers, 'snapshot', snapshot)
    changed = []
    def race(session, row, secret, function, *args, **kwargs):
        if not changed:
            with Session(team.engine) as other:
                current = other.get(RemoteSyncConnection, connection['id'])
                current.revision += 1
                current.config_json = json.dumps({'base_url': 'https://different.example.com/private/', 'kind': 'files', 'timezone': 'UTC'})
                other.add(current); other.commit()
            changed.append(True)
        return actual(session, row, secret, function, *args, **kwargs)
    monkeypatch.setattr(sync, '_provider_call', race)
    assert cycle(team, connection) == 0
    assert observed == ['https://cloud.example.com/collection/']


def second_admin(team):
    with Session(team.engine) as session:
        session.add(ChannelMember(channel_id='room', user_id='private-admin', role='admin'))
        session.commit()
    return team.headers['private-admin']


@pytest.mark.parametrize('destination', ['https://different.example.com/collection/', 'https://cloud.example.com/private/'])
def test_dav_retarget_before_first_sync_erases_original_private_credentials(team, destination):
    connection = create(team)
    headers = second_admin(team)
    changed = draft(config={'base_url': destination}, credentials={})
    response = team.client.put('/workspaces/a/sync/' + connection['id'], headers=headers, json=changed)
    assert response.status_code == 200 and response.json()['has_credentials'] is False
    before = team.remote.calls
    tested = team.client.post(f'/workspaces/a/sync/{connection["id"]}/test', headers=headers, json={'password': PASSWORD})
    assert tested.status_code == 409 and team.remote.calls == before
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert sync.credentials(row) == {} and row.enabled is False


@pytest.mark.parametrize('provider,config,new_config', [
    ('google', {'client_id': 'client', 'folder_id': 'selected'}, {'client_id': 'client', 'folder_id': 'private'}),
    ('microsoft', {'client_id': 'client', 'tenant_id': '00000000-0000-0000-0000-000000000001', 'drive_id': 'drive', 'folder_id': 'selected'},
        {'client_id': 'client', 'tenant_id': '00000000-0000-0000-0000-000000000001', 'drive_id': 'drive', 'folder_id': 'private'}),
])
def test_cloud_collection_retarget_requires_new_oauth_account_consent(team, provider, config, new_config):
    connection = create(team, provider=provider, config=config, credentials={'client_secret': 'fixed-app-secret'})
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        sync.save_credentials(row, {'client_secret': 'fixed-app-secret', 'access_token': 'original-owner-access', 'refresh_token': 'original-owner-refresh'})
        session.add(row); session.commit()
    headers = second_admin(team)
    response = team.client.put('/workspaces/a/sync/' + connection['id'], headers=headers,
        json=draft(provider=provider, config=new_config, credentials={}))
    assert response.status_code == 200 and response.json()['status'] == 'oauth_required'
    assert team.client.post(f'/workspaces/a/sync/{connection["id"]}/test', headers=headers,
                            json={'password': PASSWORD}).status_code == 409
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert sync.credentials(row) == {'client_secret': 'fixed-app-secret'}
    assert team.remote.calls == 0


def test_changing_oauth_application_does_not_reuse_another_app_client_secret(team):
    connection = create(team, provider='google', config={'client_id': 'old-client', 'folder_id': 'folder'},
                        credentials={'client_secret': 'original-app-private-secret'})
    headers = second_admin(team)
    response = team.client.put('/workspaces/a/sync/' + connection['id'], headers=headers,
        json=draft(provider='google', config={'client_id': 'new-client', 'folder_id': 'folder'}, credentials={}))
    assert response.status_code == 200 and response.json()['has_credentials'] is False
    with Session(team.engine) as session:
        assert sync.credentials(session.get(RemoteSyncConnection, connection['id'])) == {}


def test_metadata_edits_and_enable_do_not_reassign_retained_account_grant(team):
    connection = create(team)
    headers = second_admin(team)
    response = team.client.put('/workspaces/a/sync/' + connection['id'], headers=headers,
                              json=draft(name='Renamed connection', credentials={}))
    assert response.status_code == 200 and response.json()['has_credentials'] is True
    for name in ('test', 'enable'):
        response = team.client.post(f'/workspaces/a/sync/{connection["id"]}/{name}', headers=headers, json={'password': PASSWORD})
        assert response.status_code == 200, response.text
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.grant_user_id == 'owner'
        owner = session.get(ChannelMember, ('room', 'owner')); owner.role = 'member'; session.add(owner); session.commit()
    before = team.remote.calls
    assert cycle(team, connection) == 0 and team.remote.calls == before


def test_cancelled_replacement_oauth_cannot_transfer_an_old_account_grant(team):
    connection = create(team, provider='google', config={'client_id': 'client', 'folder_id': 'folder'},
                        credentials={'client_secret': 'app-secret'})
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        sync.save_credentials(row, {'client_secret': 'app-secret', 'access_token': 'old-access', 'refresh_token': 'old-refresh'})
        session.add(row); session.commit()
    headers = second_admin(team)
    response = team.client.post(f'/workspaces/a/sync/{connection["id"]}/oauth/start', headers=headers, json={'password': PASSWORD})
    assert response.status_code == 200
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.grant_user_id == 'owner' and row.enabled is False
        assert sync.credentials(row) == {'client_secret': 'app-secret'}
    assert team.client.post(f'/workspaces/a/sync/{connection["id"]}/test', headers=headers,
                            json={'password': PASSWORD}).status_code == 409


def test_dav_username_change_cannot_reuse_another_provider_accounts_password(team):
    connection = create(team)
    headers = second_admin(team)
    response = team.client.put('/workspaces/a/sync/' + connection['id'], headers=headers,
                              json=draft(credentials={'username': 'different-provider-account'}))
    assert response.status_code == 200
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert sync.credentials(row) == {'username': 'different-provider-account'}
    assert team.client.post(f'/workspaces/a/sync/{connection["id"]}/test', headers=headers,
                            json={'password': PASSWORD}).status_code == 409
    assert team.remote.calls == 0


@pytest.mark.parametrize('phase,transition', [
    (1, 'revision'), (2, 'revision'), (2, 'session'), (2, 'auth_version'),
])
def test_oauth_rechecks_consent_and_native_session_after_grant_lock_wait(team, monkeypatch, phase, transition):
    from app.models.account_security import AccountSecurity
    connection = create(team, provider='google',
        config={'client_id': 'customer-app', 'folder_id': 'selected-folder'},
        credentials={'client_secret': 'customer-app-secret'})
    started = action(team, connection, 'oauth/start').json()
    state = parse_qs(urlsplit(started['authorization_url']).query)['state'][0]
    exchanged = []
    def exchange(*args):
        exchanged.append(True)
        return {'access_token': 'fixture-provider-access', 'refresh_token': 'fixture-provider-refresh'}
    monkeypatch.setattr(providers, 'exchange_code', exchange)
    original = sync.lock_workspace
    waits = []
    def transition_after_wait(session, workspace_id):
        original(session, workspace_id)
        waits.append(True)
        if len(waits) != phase:
            return
        # SQLite serializes writers more broadly than PostgreSQL. Change the
        # committed-state view at the lock boundary to exercise the same stale
        # pre-lock ORM snapshot without relying on a timing-sensitive thread.
        if transition == 'revision':
            row = session.get(RemoteSyncConnection, connection['id'])
            config = json.loads(row.config_json)
            config['folder_id'] = 'different-selected-folder'
            row.config_json, row.revision = json.dumps(config), row.revision + 1
            row.encrypted_credentials, row.status = None, 'oauth_required'
        elif transition == 'session':
            row = session.exec(select(ActiveSession).where(
                ActiveSession.token_hash == token_digest(team.tokens['owner']))).one()
            row.revoked_at = datetime.utcnow()
        else:
            row = session.get(AccountSecurity, 'owner')
            row.auth_version += 1
        session.add(row)
        session.flush()
    monkeypatch.setattr(sync, 'lock_workspace', transition_after_wait)
    response = team.client.get('/sync/oauth/callback', params={'state': state, 'code': 'fixture-code'},
                               follow_redirects=False)
    assert response.status_code == 303 and 'sync_error=' in response.headers['location']
    assert len(exchanged) == (0 if phase == 1 else 1)
    with Session(team.engine) as session:
        assert not sync.credentials(session.get(RemoteSyncConnection, connection['id'])).get('access_token')


def test_provider_test_cannot_restore_old_credentials_after_revision_changes_at_lock(team, monkeypatch):
    connection = create(team)
    assert action(team, connection, 'test').status_code == 200
    original = sync.lock_workspace
    waits = []
    def retarget_after_wait(session, workspace_id):
        original(session, workspace_id)
        waits.append(True)
        if len(waits) == 2:
            row = session.get(RemoteSyncConnection, connection['id'])
            row.config_json = json.dumps({'base_url': 'https://new.example.com/collection/', 'kind': 'files', 'timezone': 'UTC'})
            row.revision += 1
            row.encrypted_credentials = None
            session.add(row)
            session.flush()
    monkeypatch.setattr(sync, 'lock_workspace', retarget_after_wait)
    response = action(team, connection, 'enable')
    assert response.status_code == 409
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.enabled is False and row.revision == connection['revision']


@pytest.mark.parametrize('lost_grant', ['workspace', 'channel'])
def test_enable_rechecks_acting_administrator_after_provider_io(team, monkeypatch, lost_grant):
    connection = create(team)
    assert action(team, connection, 'test').status_code == 200
    headers = second_admin(team)
    def lose_actor_permission(*args, **kwargs):
        with Session(team.engine) as session:
            row = session.get(WorkspaceMember, ('a', 'private-admin')) if lost_grant == 'workspace' else session.get(ChannelMember, ('room', 'private-admin'))
            row.role = 'member'
            session.add(row)
            session.commit()
        return {'items': [], 'complete': True, 'cursor': None}
    monkeypatch.setattr(providers, 'snapshot', lose_actor_permission)
    response = team.client.post(f'/workspaces/a/sync/{connection["id"]}/enable', headers=headers,
                               json={'password': PASSWORD})
    assert response.status_code == 403
    with Session(team.engine) as session:
        row = session.get(RemoteSyncConnection, connection['id'])
        assert row.enabled is False and row.grant_user_id == 'owner'


def test_provider_test_rechecks_native_session_after_post_io_lock_wait(team, monkeypatch):
    connection = create(team)
    original = sync.lock_workspace
    waits = []
    def revoke_after_wait(session, workspace_id):
        original(session, workspace_id)
        waits.append(True)
        if len(waits) == 2:
            row = session.exec(select(ActiveSession).where(
                ActiveSession.token_hash == token_digest(team.tokens['owner']))).one()
            row.revoked_at = datetime.utcnow()
            session.add(row)
            session.flush()
    monkeypatch.setattr(sync, 'lock_workspace', revoke_after_wait)
    response = action(team, connection, 'test')
    assert response.status_code == 401
    with Session(team.engine) as session:
        assert session.get(RemoteSyncConnection, connection['id']).tested_at is None
