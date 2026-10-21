"""Native crypto step-up preserves the customer's room/socket boundary."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.api import account, auth, operations, secure_messaging
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.secure_channel import MatrixAccount, MatrixSession, SecureChannel
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_crypto_grant import WorkspaceEncryptionUnlock
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.realtime import socket
from app.services.auth import active_user_for_token, create_token, hash_password, token_digest
from app.services.crypto import encrypt_message
from app.services.sso_scope import current_workspace_scope


@pytest.fixture
def scoped_crypto(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'scope-crypto.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "matrix_enabled", True)
    monkeypatch.setattr(socket, "engine", engine)
    with Session(engine) as session:
        session.add_all([
            User(id="actor", name="Actor", email="actor@example.com", role="admin", password_hash=hash_password("native-password")),
            User(id="a-peer", name="A peer", email="a@example.com", password_hash="unused"),
            User(id="b-peer", name="B peer", email="b@example.com", password_hash="unused"),
        ])
        for workspace, peer in (("a", "a-peer"), ("b", "b-peer")):
            session.add(Workspace(id=workspace, name=workspace.upper()))
            session.add(WorkspaceMember(workspace_id=workspace, user_id="actor", role="owner"))
            session.add(WorkspaceMember(workspace_id=workspace, user_id=peer))
            session.add(Channel(id=workspace, name=workspace.upper(), type="group"))
            session.add(WorkspaceChannel(channel_id=workspace, workspace_id=workspace))
            session.add_all([ChannelMember(channel_id=workspace, user_id="actor", role="admin"), ChannelMember(channel_id=workspace, user_id=peer)])
            session.add(SecureChannel(channel_id=workspace, matrix_room_id=f"!{workspace}:local.test", created_by="actor", status="active"))
        session.add(WorkspaceOIDCProvider(workspace_id="a", name="Customer", issuer_url="https://idp.customer.example", client_id="client", created_by="actor", enabled=True))
        session.commit()
        native = create_token("actor", session)
        scoped = create_token("actor", session, auth_methods=["sso"], workspace_scope={"workspace_id": "a", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="a", provider_revision=1))
        session.add(MatrixAccount(user_id="actor", matrix_user_id="@actor:local.test", encrypted_password=encrypt_message("matrix-password", "matrix-auth:actor"), provisioned=True))
        for native_token, matrix_token, device in ((native, "native-matrix", "NATIVE"), (scoped, "scoped-matrix", "SCOPED")):
            session.add(MatrixSession(matrix_token_hash=token_digest(matrix_token), user_id="actor", auth_token_hash=token_digest(native_token), auth_expires_at=datetime.utcnow() + timedelta(hours=1), device_id=device, encrypted_access_token=encrypt_message(matrix_token, "matrix-session:" + token_digest(matrix_token))))
        session.commit()
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(operations.router)
    app.include_router(secure_messaging.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, engine, scoped, native
    engine.dispose()


def headers(token):
    return {"Authorization": "Bearer " + token}


def unlock(client, token):
    return client.post("/secure/unlock", headers=headers(token), json={"password": "native-password"})


def test_idp_alone_cannot_initialize_or_read_shared_encryption_identity(scoped_crypto):
    client, _, scoped, native = scoped_crypto
    status = client.get("/secure/status", headers=headers(scoped)).json()
    assert status["requires_encryption_unlock"] is True
    assert status["mfa_required"] is False
    for path, method, body in (("/secure/session", "post", {}), ("/secure/cross-signing-auth", "post", {}), ("/_matrix/client/v3/room_keys/version", "get", None), ("/_matrix/client/v3/sync", "get", None)):
        token = "scoped-matrix" if path.startswith("/_matrix") else scoped
        response = getattr(client, method)(path, headers=headers(token), **({"json": body} if body is not None else {}))
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "requires_encryption_unlock"
    assert client.post("/secure/unlock", headers=headers(scoped), json={"password": "wrong"}).status_code == 403
    assert client.get("/secure/status", headers=headers(native)).json()["requires_encryption_unlock"] is False
    assert unlock(client, scoped).status_code == 200
    assert client.get("/secure/status", headers=headers(scoped)).json()["requires_encryption_unlock"] is False
    assert client.post("/secure/session", headers=headers(scoped), json={}).json()["access_token"] == "scoped-matrix"
    assert client.get("/auth/me", headers=headers(scoped)).json()["session_scope"] == "workspace"
    assert client.get("/account/security", headers=headers(scoped)).status_code == 403
    assert client.post("/secure/devices/NATIVE/revoke", headers=headers(scoped), json={"password": "native-password"}).status_code == 403


def test_unlock_is_token_bound_expires_and_fails_closed_if_scope_sidecar_is_deleted(scoped_crypto):
    client, engine, scoped, _ = scoped_crypto
    assert unlock(client, scoped).status_code == 200
    with Session(engine) as session:
        second = create_token("actor", session, auth_methods=["sso"], workspace_scope={"workspace_id": "a", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(second), workspace_id="a", provider_revision=1))
        session.commit()
    assert client.post("/secure/session", headers=headers(second), json={}).status_code == 403
    with Session(engine) as session:
        grant = session.get(WorkspaceEncryptionUnlock, token_digest(scoped))
        grant.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(grant)
        session.commit()
    assert client.post("/secure/session", headers=headers(scoped), json={}).status_code == 403
    assert unlock(client, scoped).status_code == 200
    with Session(engine) as session:
        session.delete(session.get(WorkspaceOIDCSession, token_digest(scoped)))
        session.commit()
    assert client.get("/auth/me", headers=headers(scoped)).status_code == 401
    assert client.get("/_matrix/client/v3/sync", headers=headers("scoped-matrix")).status_code == 401


def test_unlock_requires_current_native_mfa_and_account_epoch(scoped_crypto, monkeypatch):
    from app.services import account_security
    client, engine, scoped, _ = scoped_crypto
    with Session(engine) as session:
        state = session.get(AccountSecurity, "actor")
        state.totp_secret = "enabled-factor"
        session.add(state)
        session.commit()
    monkeypatch.setattr(account_security, "verify_factor", lambda session, state, code: code == "current-code")
    assert client.get("/secure/status", headers=headers(scoped)).json()["mfa_required"] is True
    assert unlock(client, scoped).status_code == 403
    assert client.post("/secure/unlock", headers=headers(scoped), json={"password": "native-password", "code": "wrong"}).status_code == 403
    assert client.post("/secure/unlock", headers=headers(scoped), json={"password": "native-password", "code": "current-code"}).status_code == 200
    with Session(engine) as session:
        state = session.get(AccountSecurity, "actor")
        state.auth_version += 1
        session.add(state)
        session.commit()
    assert client.get("/secure/status", headers=headers(scoped)).status_code == 401
    assert client.get("/_matrix/client/v3/sync", headers=headers("scoped-matrix")).status_code == 401


def test_unlocked_matrix_sync_and_known_room_ids_stay_workspace_scoped(scoped_crypto, monkeypatch):
    client, _, scoped, _ = scoped_crypto
    assert unlock(client, scoped).status_code == 200
    payload = {"rooms": {"join": {"!a:local.test": {"timeline": {}}, "!b:local.test": {"timeline": {"secret": "b"}}}}, "account_data": {"events": [{"type": "m.direct", "content": {"peer": ["!b:local.test"]}}, {"type": "m.secret_storage.default_key", "content": {"key": "encrypted-key-id"}}]}}
    class Upstream:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs): return httpx.Response(200, json=payload)
    monkeypatch.setattr(secure_messaging.httpx, "AsyncClient", Upstream)
    response = client.get("/_matrix/client/v3/sync", headers=headers("scoped-matrix"))
    assert response.status_code == 200, response.text
    assert set(response.json()["rooms"]["join"]) == {"!a:local.test"}
    assert response.json()["account_data"]["events"] == [{"type": "m.secret_storage.default_key", "content": {"key": "encrypted-key-id"}}]
    assert client.get("/_matrix/client/v3/user/%40actor%3Alocal.test/account_data/m.direct", headers=headers("scoped-matrix")).status_code == 403
    assert client.put("/_matrix/client/v3/user/%40actor%3Alocal.test/account_data/m.ignored_user_list", headers=headers("scoped-matrix"), json={}).status_code == 403
    assert client.get("/_matrix/client/v3/rooms/%21b%3Alocal.test/messages", headers=headers("scoped-matrix")).status_code == 403


@pytest.mark.parametrize("change", ["disable", "revision", "membership"])
def test_provider_changes_during_matrix_sync_discard_inflight_response(scoped_crypto, monkeypatch, change):
    client, engine, scoped, _ = scoped_crypto
    assert unlock(client, scoped).status_code == 200
    class Upstream:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs):
            with Session(engine) as session:
                if change == "membership": session.delete(session.get(WorkspaceMember, ("a", "actor")))
                else:
                    provider = session.get(WorkspaceOIDCProvider, "a")
                    if change == "disable": provider.enabled = False
                    else: provider.revision += 1
                    session.add(provider)
                session.commit()
            return httpx.Response(200, json={"rooms": {"join": {"!a:local.test": {"secret": "must not escape"}}}})
    monkeypatch.setattr(secure_messaging.httpx, "AsyncClient", Upstream)
    response = client.get("/_matrix/client/v3/sync", headers=headers("scoped-matrix"))
    assert response.status_code == 401
    assert "must not escape" not in response.text


def test_scoped_socket_cannot_join_other_workspace_or_receive_its_presence(scoped_crypto, monkeypatch):
    _, engine, scoped, native = scoped_crypto
    saved = {"scoped": {"user_id": "actor", "token": scoped}, "native": {"user_id": "actor", "token": native}}
    sio = Mock()
    sio.get_session = AsyncMock(side_effect=lambda sid: saved[sid])
    sio.enter_room, sio.leave_room, sio.emit = AsyncMock(), AsyncMock(), AsyncMock()
    sio.manager.get_participants.return_value = [("scoped", "1"), ("native", "2")]
    monkeypatch.setattr(socket, "sio", sio)
    assert asyncio.run(socket.join("scoped", "a")) == {"ok": True}
    assert "error" in asyncio.run(socket.join("scoped", "b"))
    assert asyncio.run(socket.join("native", "b")) == {"ok": True}
    asyncio.run(socket.send_to_user("actor", "presence", {"user_id": "b-peer", "status": "online"}))
    assert [call.kwargs["room"] for call in sio.emit.await_args_list] == ["native"]
    sio.emit.reset_mock()
    asyncio.run(socket.send_to_user("actor", "channel_removed", {"channel_id": "b"}))
    assert [call.kwargs["room"] for call in sio.emit.await_args_list] == ["native"]
    with Session(engine) as session:
        provider = session.get(WorkspaceOIDCProvider, "a")
        provider.enabled = False
        session.add(provider)
        session.commit()
    assert "error" in asyncio.run(socket.join("scoped", "a"))
    asyncio.run(socket.broadcast_to_channel("a", "message", {"content": "a secret"}))
    sio.leave_room.assert_any_await("scoped", "a")


def test_scope_context_clears_between_different_socket_authentications(scoped_crypto):
    _, engine, scoped, native = scoped_crypto
    with Session(engine) as session:
        assert active_user_for_token(scoped, session).id == "actor"
        assert current_workspace_scope(session) == "a"
        assert active_user_for_token(native, session).id == "actor"
        assert current_workspace_scope(session) is None
        assert active_user_for_token(scoped, session).id == "actor"
        assert active_user_for_token("invalid", session) is None
        assert current_workspace_scope(session) is None


def test_metrics_direct_auth_cannot_bypass_scoped_admin_restriction(scoped_crypto):
    client, _, scoped, native = scoped_crypto
    assert client.get("/operations/metrics", headers=headers(scoped)).status_code == 403
    assert client.get("/operations/metrics", headers=headers(native)).status_code == 200


def test_caught_midrequest_scope_revocation_never_upgrades_to_account_access(scoped_crypto):
    _, engine, scoped, native = scoped_crypto
    with Session(engine) as session:
        assert active_user_for_token(scoped, session).id == "actor"
        with Session(engine) as other:
            provider = other.get(WorkspaceOIDCProvider, "a")
            provider.enabled = False
            other.add(provider)
            other.commit()
        for _ in range(2):
            with pytest.raises(HTTPException) as denied:
                current_workspace_scope(session)
            assert denied.value.status_code == 401
        assert active_user_for_token(native, session).id == "actor"
        assert current_workspace_scope(session) is None
