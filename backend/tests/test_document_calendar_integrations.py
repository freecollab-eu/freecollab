"""Real content storage, access boundaries and encrypted channel fail-closed behavior."""
import base64
from hashlib import sha256
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.api import integrations_content, saved_items, secure_messaging
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.account_security import AccountSecurity
from app.models.collaboration import ChannelPin
from app.models.direct_message import UserBlock
from app.models.integration_content import IntegrationContentRevision
from app.models.native_clients import NativeClientSession
from app.models.post import Post  # Register the legacy table used by secure enable's content fence.
from app.models.secure_channel import MatrixAccount, SecureChannel
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import matrix_bridge, native_clients
from app.services.auth import create_token, revoke_token, token_digest
from app.services.integration_content import CalendarFields, document_url


@pytest.fixture
def content():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name + "@example.com", password_hash="unused") for name in ("owner", "member", "peer", "outsider")])
        session.add_all([Channel(id="room", name="Room"), Channel(id="other", name="Other")])
        session.commit()
        session.add_all([ChannelMember(channel_id="room", user_id=name, role="admin" if name == "owner" else "member") for name in ("owner", "member", "peer")])
        session.add(ChannelMember(channel_id="other", user_id="outsider", role="admin"))
        session.commit()
    app = FastAPI(); app.include_router(integrations_content.router); app.include_router(saved_items.router); app.include_router(secure_messaging.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client: yield SimpleNamespace(client=client, engine=engine)
    engine.dispose()


def headers(user="member"):
    return {"Authorization": "Bearer " + create_token(user)}


def encrypted_room(content):
    with Session(content.engine) as session:
        session.add(SecureChannel(channel_id='room', created_by='owner', status='active', matrix_room_id='!room:example.com'))
        session.add(MatrixAccount(user_id='owner', matrix_user_id='@owner:example.com', encrypted_password='unused', provisioned=True))
        session.commit()


def opaque_event():
    return {'room_id': '!room:example.com', 'type': 'm.room.encrypted',
            'sender': '@owner:example.com', 'content': {'ciphertext': 'opaque'}}


DOC = {"title": "Private budget", "provider": "nextcloud", "url": "https://cloud.example.com/s/Abc123", "notes": "Private document note"}
EVENT = {"title": "Private planning", "description": "Team agenda", "location": "Studio",
         "starts_at": "2026-10-25T02:30:00+02:00", "ends_at": "2026-10-25T02:45:00+02:00",
         "all_day": False, "timezone": "Europe/Stockholm", "reminder_minutes": 15, "external_uid": None}


def test_native_documents_are_encrypted_revisioned_scoped_and_deleted(content):
    path = "/channels/room/integrations/documents"
    response = content.client.post(path, json={"id": "doc1", "fields": DOC}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["fields"] == DOC
    with Session(content.engine) as session:
        stored = session.exec(select(IntegrationContentRevision)).one()
        assert DOC["title"] not in stored.encrypted_payload and DOC["url"] not in stored.encrypted_payload
        first_ciphertext = stored.encrypted_payload
    assert content.client.get(path, headers=headers("outsider")).status_code == 403
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 1, "fields": DOC}, headers=headers("peer")).status_code == 403
    fields = {**DOC, "title": "Revised budget"}
    revised = content.client.post(path, json={"id": "doc1", "expected_revision": 1, "fields": fields}, headers=headers("owner"))
    assert revised.status_code == 200 and revised.json()["revision"] == 2
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 1, "fields": DOC}, headers=headers()).status_code == 409
    history = content.client.get(path + "/doc1/revisions", headers=headers("peer")).json()
    assert [row["fields"]["title"] for row in history] == ["Revised budget", "Private budget"]
    with Session(content.engine) as session:
        assert session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.revision == 1)).one().encrypted_payload == first_ciphertext
    assert content.client.post("/channels/other/integrations/documents", json={"id": "doc1", "fields": DOC}, headers=headers("outsider")).status_code == 404
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 2, "deleted": True}, headers=headers()).status_code == 200
    assert content.client.get(path, headers=headers()).json() == []
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 3, "fields": DOC}, headers=headers()).status_code == 409
    assert content.client.get(path + "/doc1/revisions", headers=headers()).json()[0]["deleted"] is True


@pytest.mark.parametrize("provider,url", [
    ("google_drive", "https://docs.google.com.evil.example/document/d/secret"),
    ("google_drive", "javascript:alert(1)"),
    ("sharepoint", "https://team.sharepoint.com.evil.example/doc"),
    ("nextcloud", "https://user:password@cloud.example.com/s/doc"),
    ("nextcloud", "https://cloud.example.com/s/doc?access_token=secret"),
    ("nextcloud", "https://cloud.example.com/s/doc#secret"),
    ("nextcloud", "https://127.0.0.1/s/doc"),
    ("nextcloud", "https://cloud.example.com/s/doc%0aInjected"),
    ("nextcloud", "https://cloud.example.com:8443/s/doc"),
])
def test_document_validation_rejects_spoofing_credentials_and_non_navigation_urls(provider, url):
    with pytest.raises(ValueError): document_url(provider, url)


@pytest.mark.parametrize("provider,url", [
    ("google_drive", "https://docs.google.com/document/d/abc123/edit"),
    ("google_drive", "https://drive.google.com/drive/folders/abc_123"),
    ("sharepoint", "https://team.sharepoint.com/sites/studio/Document.docx"),
    ("nextcloud", "https://cloud.example.com/index.php/s/abc123"),
])
def test_real_provider_link_shapes_are_supported(provider, url):
    assert document_url(provider, url) == url


def test_calendar_normalizes_explicit_dst_offset_and_preserves_all_day_dates(content):
    path = "/channels/room/integrations/calendar"
    response = content.client.post(path, json={"id": "event1", "fields": EVENT}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["starts_at"] == "2026-10-25T00:30:00Z"
    assert response.json()["fields"]["timezone"] == "Europe/Stockholm"
    day = {**EVENT, "all_day": True, "starts_at": "2026-10-25", "ends_at": "2026-10-26"}
    assert content.client.post(path, json={"id": "event2", "fields": day}, headers=headers()).json()["fields"]["ends_at"] == "2026-10-26"
    for bad in ({"ends_at": EVENT["starts_at"]}, {"starts_at": "2026-10-25T02:30:00"}, {"timezone": "Bad/Zone"}, {"external_uid": "uid\r\nATTENDEE:mailto:evil@example.com"}):
        assert content.client.post(path, json={"id": "bad", "fields": {**EVENT, **bad}}, headers=headers()).status_code == 422


def test_secure_content_only_accepts_verified_opaque_events_even_while_preparing(content, monkeypatch):
    with Session(content.engine) as session:
        session.add(SecureChannel(channel_id="room", created_by="owner", status="preparing"))
        session.commit()
    path = "/channels/room/integrations/documents"
    assert content.client.post(path, json={"id": "private", "fields": DOC}, headers=headers()).status_code == 409
    def validate(session, channel, event, user):
        assert channel == "room" and user == "member"
        if event != "$owned": raise HTTPException(403, "Wrong sender or room")
    monkeypatch.setattr(integrations_content, "validate_secure_event", validate)
    assert content.client.post(path, json={"id": "private", "matrix_event_id": "$foreign"}, headers=headers()).status_code == 403
    response = content.client.post(path, json={"id": "private", "matrix_event_id": "$owned"}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["matrix_event_id"] == "$owned" and "fields" not in response.json()
    assert content.client.get(path, headers=headers()).json()[0]["matrix_event_id"] == "$owned"
    assert content.client.post(path, json={"id": "other", "fields": DOC, "matrix_event_id": "$owned"}, headers=headers()).status_code == 422
    with Session(content.engine) as session:
        stored = session.exec(select(IntegrationContentRevision)).one()
        assert stored.encrypted_payload is None


def test_native_channel_rejects_secure_event_and_deleted_plaintext(content):
    path = "/channels/room/integrations/calendar"
    assert content.client.post(path, json={"id": "event", "matrix_event_id": "$opaque"}, headers=headers()).status_code == 409
    assert content.client.post(path, json={"id": "event", "fields": EVENT, "deleted": True}, headers=headers()).status_code == 422


@pytest.mark.parametrize('route', ['document', 'pin'])
@pytest.mark.parametrize('change', ['logout', 'blocked', 'membership', 'auth_version', 'binding_removed', 'preparing', 'account_removed', 'binding_identity', 'account_identity'])
def test_secure_event_rechecks_current_authority_after_matrix_response(content, monkeypatch, route, change):
    encrypted_room(content)
    auth = headers('owner')
    token = auth['Authorization'].removeprefix('Bearer ')
    monkeypatch.setattr(matrix_bridge, 'service_token', lambda *args, **kwargs: ('operator', 'unused'))

    def response_after_authority_change(*args, **kwargs):
        with Session(content.engine) as session:
            if change == 'logout':
                revoke_token(token, session)
            elif change == 'blocked':
                actor = session.get(User, 'owner')
                actor.is_blocked = True
                session.add(actor)
            elif change == 'membership':
                session.delete(session.get(ChannelMember, ('room', 'owner')))
            elif change == 'auth_version':
                state = session.get(AccountSecurity, 'owner') or AccountSecurity(user_id='owner')
                state.auth_version += 1
                session.add(state)
            elif change == 'binding_removed':
                session.delete(session.get(SecureChannel, 'room'))
            elif change == 'preparing':
                binding = session.get(SecureChannel, 'room')
                binding.status = 'preparing'
                session.add(binding)
            elif change == 'binding_identity':
                binding = session.get(SecureChannel, 'room')
                binding.matrix_room_id = '!replacement:example.com'
                session.add(binding)
            elif change == 'account_identity':
                account = session.get(MatrixAccount, 'owner')
                account.matrix_user_id = '@replacement:example.com'
                session.add(account)
            else:
                session.delete(session.get(MatrixAccount, 'owner'))
            session.commit()
        return opaque_event()

    monkeypatch.setattr(matrix_bridge, 'matrix_request', response_after_authority_change)
    if route == 'document':
        response = content.client.post('/channels/room/integrations/documents', json={'id': 'late', 'matrix_event_id': '$owned'}, headers=auth)
    else:
        response = content.client.post('/channels/room/pins', json={'matrix_event_id': '$owned'}, headers=auth)
    assert response.status_code in {401, 403, 409}
    with Session(content.engine) as session:
        assert session.exec(select(IntegrationContentRevision)).all() == []
        assert session.exec(select(ChannelPin)).all() == []


@pytest.mark.parametrize('change', ['workspace', 'sso_revision', 'sso_sidecar', 'native_parent', 'native_child', 'sso_native_revision'])
def test_secure_event_rechecks_workspace_sso_and_native_child_scope(content, monkeypatch, change):
    encrypted_room(content)
    scoped = change in {'sso_revision', 'sso_sidecar', 'sso_native_revision'}
    child = change in {'native_parent', 'native_child', 'sso_native_revision'}
    with Session(content.engine) as session:
        session.add(Workspace(id='team', name='Team'))
        session.flush()
        session.add(WorkspaceMember(workspace_id='team', user_id='owner', role='admin'))
        session.add(WorkspaceChannel(channel_id='room', workspace_id='team'))
        session.add(WorkspaceOIDCProvider(workspace_id='team', name='Customer IdP', issuer_url='https://idp.example.com',
                                        client_id='customer', enabled=True, revision=4, created_by='owner'))
        session.commit()
        source = create_token('owner', session=session,
            auth_methods=['sso'] if scoped else ['pwd'],
            workspace_scope={'workspace_id': 'team', 'provider_revision': 4} if scoped else None)
        if scoped:
            session.add(WorkspaceOIDCSession(token_hash=token_digest(source), workspace_id='team', provider_revision=4))
            session.commit()
        token = source
        if child:
            verifier = 'v' * 43
            challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            callback = 'http://127.0.0.1:43127/freecollab-native/callback'
            grant = native_clients.issue_grant(session, 'owner', source, challenge=challenge, callback=callback, kind='desktop')
            token = native_clients.exchange_grant(session, grant['code'], verifier, callback)['access_token']
    auth = {'Authorization': 'Bearer ' + token}
    assert content.client.get('/channels/room/integrations/documents', headers=auth).status_code == 200
    monkeypatch.setattr(matrix_bridge, 'service_token', lambda *args, **kwargs: ('operator', 'unused'))

    def changed_scope(*args, **kwargs):
        with Session(content.engine) as session:
            if change == 'workspace':
                session.delete(session.get(WorkspaceMember, ('team', 'owner')))
            elif change in {'sso_revision', 'sso_native_revision'}:
                provider = session.get(WorkspaceOIDCProvider, 'team')
                provider.revision += 1
                session.add(provider)
            elif change == 'sso_sidecar':
                session.delete(session.get(WorkspaceOIDCSession, token_digest(token)))
            elif change == 'native_parent':
                revoke_token(source, session)
            else:
                session.delete(session.get(NativeClientSession, token_digest(token)))
            session.commit()
        return opaque_event()

    monkeypatch.setattr(matrix_bridge, 'matrix_request', changed_scope)
    response = content.client.post('/channels/room/integrations/documents', json={'id': 'late', 'matrix_event_id': '$owned'}, headers=auth)
    assert response.status_code == (403 if change == 'workspace' else 401)
    with Session(content.engine) as session:
        assert session.exec(select(IntegrationContentRevision)).all() == []


@pytest.mark.parametrize('denied', [False, True])
def test_secure_event_guard_preserves_caller_pending_objects_and_transaction(content, monkeypatch, denied):
    encrypted_room(content)
    monkeypatch.setattr(matrix_bridge, 'service_token', lambda *args, **kwargs: ('operator', 'unused'))
    with Session(content.engine) as session:
        actor = session.get(User, 'owner')
        session.exec(update(User).where(User.id == 'peer').values(name='Uncommitted caller transaction'))

        def remote(*args, **kwargs):
            actor.name = 'Pending caller name'
            if denied:
                with session.no_autoflush:
                    session.execute(update(User).where(User.id == 'owner').values(is_blocked=True)
                                    .execution_options(synchronize_session=False))
            return opaque_event()

        monkeypatch.setattr(matrix_bridge, 'matrix_request', remote)
        if denied:
            with pytest.raises(HTTPException) as failure:
                matrix_bridge.validate_secure_event(session, 'room', '$owned', 'owner')
            assert failure.value.status_code == 401
        else:
            # Trusted callers without an HTTP request context remain supported.
            assert matrix_bridge.validate_secure_event(session, 'room', '$owned', 'owner') == opaque_event()
        assert session.in_transaction()
        assert actor.name == 'Pending caller name' and actor in session.dirty
        with session.no_autoflush:
            assert session.exec(select(User.name).where(User.id == 'owner')).one() == 'owner'
            assert session.exec(select(User.name).where(User.id == 'peer')).one() == 'Uncommitted caller transaction'
        session.rollback()
        assert session.get(User, 'peer').name == 'peer'
        assert not session.get(User, 'owner').is_blocked


def test_secure_event_does_not_preserve_a_demoted_pins_administrator(content, monkeypatch):
    encrypted_room(content)
    monkeypatch.setattr(matrix_bridge, 'service_token', lambda *args, **kwargs: ('operator', 'unused'))

    def demoted(*args, **kwargs):
        with Session(content.engine) as session:
            member = session.get(ChannelMember, ('room', 'owner'))
            member.role = 'member'
            session.add(member)
            session.commit()
        return opaque_event()

    monkeypatch.setattr(matrix_bridge, 'matrix_request', demoted)
    response = content.client.post('/channels/room/pins', json={'matrix_event_id': '$owned'}, headers=headers('owner'))
    assert response.status_code == 403
    with Session(content.engine) as session:
        assert session.exec(select(ChannelPin)).all() == []


def test_secure_enable_cannot_relabel_native_document_or_calendar_history(content, monkeypatch):
    monkeypatch.setattr(secure_messaging, "require_matrix", lambda: None)
    for kind, fields in (("documents", DOC), ("calendar", EVENT)):
        path = "/channels/room/integrations/" + kind
        assert content.client.post(path, json={"id": "item-" + kind, "fields": fields}, headers=headers()).status_code == 200
    # Even removing visible items retains immutable native revisions.
    for kind in ("documents", "calendar"):
        path = "/channels/room/integrations/" + kind
        assert content.client.post(path, json={"id": "item-" + kind, "expected_revision": 1, "deleted": True}, headers=headers()).status_code == 200
    assert content.client.post("/secure/channels/room/enable", headers=headers("owner")).status_code == 409


def test_calendar_model_rejects_nonexistent_dates_and_unsupported_reminder():
    for bad in ({"all_day": True, "starts_at": "2026-02-30", "ends_at": "2026-03-01"}, {"reminder_minutes": 10081}):
        with pytest.raises(ValueError): CalendarFields.model_validate({**EVENT, **bad})
