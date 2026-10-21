"""Real customer-session handoffs, exact origins, revocation and atomic replay."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import auth, embed
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.embed import EmbedApplication, EmbedGrant, EmbedMatrixBinding, EmbedSession
from app.models.session import ActiveSession
from app.models.secure_channel import MatrixSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.models.workspace_crypto_grant import WorkspaceEncryptionUnlock
from app.services.auth import create_token, decode_token_claims, hash_password, token_digest
from app.services.embed import proof_challenge

HOST = "https://product.example.com"
APP = "https://collaboration.example.com"
VERIFIER = "A" * 64
PASSWORD = "correct-password"
PASSWORD_HASH = hash_password(PASSWORD)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "public_app_url", APP)
    monkeypatch.setattr(settings, "jwt_secret", "isolated-embed-test-signing-key-at-least-32-bytes")
    engine = create_engine("sqlite:///" + str(tmp_path / "embed.db"), connect_args={"check_same_thread": False, "timeout": 10})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ("owner", "peer", "outsider"):
            session.add(User(id=name, name=name, email=name + "@example.com", password_hash=PASSWORD_HASH))
        session.add_all([Workspace(id="team", name="Customer"), Workspace(id="other", name="Other")])
        session.commit()
        session.add_all([WorkspaceMember(workspace_id="team", user_id="owner", role="owner"),
                         WorkspaceMember(workspace_id="team", user_id="peer", role="member"),
                         WorkspaceMember(workspace_id="other", user_id="owner", role="owner")])
        session.add_all([Channel(id="shared", name="Customer channel"), Channel(id="elsewhere", name="Other channel")])
        session.commit()
        session.add_all([WorkspaceChannel(channel_id="shared", workspace_id="team"),
                         WorkspaceChannel(channel_id="elsewhere", workspace_id="other"),
                         ChannelMember(channel_id="shared", user_id="owner"),
                         ChannelMember(channel_id="elsewhere", user_id="owner")])
        session.add(WorkspaceOIDCProvider(workspace_id="team", name="Customer SSO", issuer_url="https://id.example.com",
            client_id="customer-app", created_by="owner", enabled=True, revision=4))
        session.add(EmbedApplication(id="application", workspace_id="team", name="Customer product", parent_origin=HOST, created_by="owner"))
        session.commit()
        native = create_token("owner", session)
        peer = create_token("peer", session)
        scoped = create_token("owner", session, auth_methods=["sso"], mfa_verified=True,
            workspace_scope={"workspace_id": "team", "provider_revision": 4})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="team", provider_revision=4))
        session.commit()
    application = FastAPI()
    application.include_router(embed.router)
    application.include_router(auth.router)
    def sessions():
        with Session(engine) as session:
            yield session
    application.dependency_overrides[get_session] = sessions
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, engine=engine, native=native, scoped=scoped, peer=peer)
    engine.dispose()


def headers(token, origin=HOST):
    return {"Authorization": "Bearer " + token, "Origin": origin}


def launch(api, **changes):
    body = {"application_id": "application", "channel_id": "shared", "parent_origin": HOST,
            "code_challenge": proof_challenge(VERIFIER)}
    body.update(changes)
    return api.client.post("/workspaces/team/embed/launch", headers=headers(api.scoped), json=body)


def exchange(api, code, **changes):
    body = {"code": code, "code_verifier": VERIFIER, "parent_origin": HOST, "channel_id": "shared"}
    body.update(changes)
    return api.client.post("/embed/application/exchange", headers={"Origin": APP}, json=body)


def child(api):
    grant = launch(api)
    assert grant.status_code == 200, grant.text
    result = exchange(api, grant.json()["code"])
    assert result.status_code == 200, result.text
    return result.json()["access_token"]


def test_real_handoff_preserves_source_authentication_and_has_no_stored_bearer(api):
    grant = launch(api)
    assert grant.status_code == 200, grant.text
    body = grant.json()
    assert 0 < body["expires_in"] <= 60
    assert body["frame_url"].startswith(APP + "/api/embed/application/document?")
    assert api.scoped not in body["frame_url"] and body["code"] not in body["frame_url"]
    with Session(api.engine) as session:
        row = session.exec(select(EmbedGrant)).one()
        assert row.code_hash == token_digest(body["code"])
        assert row.parent_token_hash == token_digest(api.scoped)
        assert api.scoped not in str(row.model_dump()) and body["code"] not in str(row.model_dump())
    result = exchange(api, body["code"])
    assert result.status_code == 200, result.text
    token = result.json()["access_token"]
    source, derived = decode_token_claims(api.scoped), decode_token_claims(token)
    assert token != api.scoped
    assert {key: derived[key] for key in ("iat", "av", "amr", "sub", "sso_workspace_id", "sso_provider_revision")} == {
        key: source[key] for key in ("iat", "av", "amr", "sub", "sso_workspace_id", "sso_provider_revision")}
    assert derived["exp"] == source["exp"]
    assert 0 < result.json()["expires_in"] <= 600
    assert result.json()["expires_at"] <= derived["exp"]
    assert result.json()["expires_at"] <= int(datetime.now(timezone.utc).timestamp()) + 600
    assert derived["embed_application_id"] == "application"
    principal = api.client.get("/auth/me", headers=headers(token, APP))
    assert principal.status_code == 200 and principal.json()["workspace_id"] == "team"
    assert principal.json()["session_scope"] == "workspace"
    assert exchange(api, body["code"]).status_code == 400


@pytest.mark.parametrize("origin", ["https://evil.example", "https://product.example.com.evil.example", "null", HOST + "/path"])
def test_public_bootstrap_rejects_every_unregistered_origin(api, origin):
    result = api.client.get("/embed/applications/application", params={"parent_origin": origin})
    assert result.status_code in (403, 422)


def test_frame_document_sets_exact_ancestor_csp_without_credentials(api):
    result = api.client.get("/embed/application/document", params={"parent_origin": HOST})
    assert result.status_code == 200
    assert "frame-ancestors " + HOST + " " + APP in result.headers["content-security-policy"]
    assert "object-src 'none'" in result.headers["content-security-policy"]
    assert "no-store" in result.headers["cache-control"]
    assert result.headers["referrer-policy"] == "no-referrer"
    assert 'src="/embed-entry.js"' in result.text and 'href="/embed-entry.css"' in result.text
    assert api.scoped not in result.text and api.native not in result.text


@pytest.mark.parametrize("origin", ["http://product.example.com", "https://*.example.com", HOST + "/path", HOST + "?x=y", HOST + "#x", "https://a:b@product.example.com", "https://product.example.com:443", "https://PRODUCT.example.com", "https://product.example.com\n"])
def test_registration_rejects_non_exact_or_insecure_origins(api, origin):
    result = api.client.post("/workspaces/team/embed/applications", headers=headers(api.native),
        json={"name": "Unsafe", "parent_origin": origin, "password": PASSWORD})
    assert result.status_code == 422


def test_only_native_workspace_admin_with_reauthentication_can_register(api):
    draft = {"name": "Second app", "parent_origin": "https://second.example.com", "password": PASSWORD}
    assert api.client.post("/workspaces/team/embed/applications", headers=headers(api.scoped), json=draft).status_code == 403
    assert api.client.post("/workspaces/team/embed/applications", headers=headers(api.peer), json=draft).status_code == 403
    assert api.client.post("/workspaces/team/embed/applications", headers=headers(api.native), json={**draft, "password": "wrong"}).status_code == 403
    result = api.client.post("/workspaces/team/embed/applications", headers=headers(api.native), json=draft)
    assert result.status_code == 201, result.text
    assert result.json()["parent_origin"] == draft["parent_origin"]
    assert "password" not in result.json()


def test_same_origin_embedding_is_rejected_to_preserve_key_isolation(api):
    result = api.client.post("/workspaces/team/embed/applications", headers=headers(api.native),
        json={"name": "Same origin", "parent_origin": APP, "password": PASSWORD})
    assert result.status_code == 422


def test_launch_rejects_native_account_token_and_cross_workspace_channel(api):
    body = {"application_id": "application", "channel_id": "shared", "parent_origin": HOST, "code_challenge": proof_challenge(VERIFIER)}
    assert api.client.post("/workspaces/team/embed/launch", headers=headers(api.native), json=body).status_code == 403
    assert launch(api, channel_id="elsewhere").status_code == 403
    assert launch(api, parent_origin="https://evil.example").status_code == 403
    assert api.client.post("/workspaces/team/embed/launch", headers=headers(api.scoped, "https://evil.example"), json=body).status_code == 403
    # The trusted popup may launch only for the application's registered parent.
    assert api.client.post("/workspaces/team/embed/launch", headers=headers(api.scoped, APP), json=body).status_code == 200


def test_native_client_cannot_create_a_nested_embed_session(api):
    from app.api import native_clients
    from app.services.auth import revoke_token

    api.client.app.include_router(native_clients.router)
    callback = "http://127.0.0.1:43127/freecollab-native/callback"
    grant = api.client.post("/native-clients/connect", headers=headers(api.scoped), json={
        "code_challenge": proof_challenge(VERIFIER), "redirect_uri": callback, "client_kind": "desktop"})
    assert grant.status_code == 200, grant.text
    result = api.client.post("/native-clients/exchange", json={"code": grant.json()["code"],
        "code_verifier": VERIFIER, "redirect_uri": callback})
    assert result.status_code == 200, result.text
    token = result.json()["access_token"]
    assert api.client.get("/auth/me", headers=headers(token)).status_code == 200
    body = {"application_id": "application", "channel_id": "shared", "parent_origin": HOST,
            "code_challenge": proof_challenge(VERIFIER)}
    blocked = api.client.post("/workspaces/team/embed/launch", headers=headers(token), json=body)
    assert blocked.status_code == 403, blocked.text
    with Session(api.engine) as session:
        assert session.exec(select(EmbedGrant)).first() is None
        revoke_token(api.scoped, session)
    assert api.client.get("/auth/me", headers=headers(token)).status_code == 401


def test_pkce_wrong_parent_wrong_channel_and_wrong_origin_do_not_consume_grant(api):
    code = launch(api).json()["code"]
    assert exchange(api, code, code_verifier="B" * 64).status_code == 400
    assert exchange(api, code, parent_origin="https://evil.example").status_code == 400
    assert exchange(api, code, channel_id="elsewhere").status_code == 400
    request = {"code": code, "code_verifier": VERIFIER, "parent_origin": HOST, "channel_id": "shared"}
    assert api.client.post("/embed/application/exchange", headers={"Origin": HOST}, json=request).status_code == 403
    assert exchange(api, code).status_code == 200


@pytest.mark.parametrize("change", ["source-revoked", "provider-edited", "application-edited", "application-disabled", "workspace-removed", "channel-removed", "account-changed", "account-blocked", "expired"])
def test_pending_grant_rechecks_live_authority(api, change):
    code = launch(api).json()["code"]
    with Session(api.engine) as session:
        if change == "source-revoked":
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(api.scoped))).one()
            row.revoked_at = datetime.utcnow(); session.add(row)
        elif change == "provider-edited":
            row = session.get(WorkspaceOIDCProvider, "team"); row.revision += 1; session.add(row)
        elif change in {"application-edited", "application-disabled"}:
            row = session.get(EmbedApplication, "application"); row.revision += 1
            if change == "application-disabled": row.enabled = False
            session.add(row)
        elif change == "workspace-removed":
            session.delete(session.get(WorkspaceMember, ("team", "owner")))
        elif change == "channel-removed":
            session.delete(session.get(ChannelMember, ("shared", "owner")))
        elif change == "account-changed":
            row = session.get(AccountSecurity, "owner"); row.auth_version += 1; session.add(row)
        elif change == "account-blocked":
            row = session.get(User, "owner"); row.is_blocked = True; session.add(row)
        else:
            row = session.get(EmbedGrant, token_digest(code)); row.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row)
        session.commit()
    assert exchange(api, code).status_code in (400, 403, 404)


@pytest.mark.parametrize("change", ["source-revoked", "provider-disabled", "application-disabled", "application-edited", "workspace-removed"])
def test_existing_child_session_obeys_parent_and_application_revocation(api, change):
    token = child(api)
    with Session(api.engine) as session:
        if change == "source-revoked":
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(api.scoped))).one()
            row.revoked_at = datetime.utcnow()
        elif change == "provider-disabled":
            row = session.get(WorkspaceOIDCProvider, "team"); row.enabled = False
        elif change == "workspace-removed":
            session.delete(session.get(WorkspaceMember, ("team", "owner"))); row = None
        else:
            row = session.get(EmbedApplication, "application")
            if change == "application-disabled": row.enabled = False
            else: row.revision += 1
        if row is not None: session.add(row)
        session.commit()
    assert api.client.get("/auth/me", headers=headers(token, APP)).status_code == 401


def test_embed_logout_revokes_child_without_touching_source_and_no_nested_launch(api):
    token = child(api)
    body = {"application_id": "application", "channel_id": "shared", "parent_origin": HOST, "code_challenge": proof_challenge(VERIFIER)}
    assert api.client.post("/workspaces/team/embed/launch", headers=headers(token), json=body).status_code == 403
    assert api.client.post("/embed/application/logout", headers=headers(api.scoped, APP)).status_code == 403
    assert api.client.post("/embed/application/logout", headers=headers(token, APP)).status_code == 200
    assert api.client.get("/auth/me", headers=headers(token, APP)).status_code == 401
    assert api.client.get("/auth/me", headers=headers(api.scoped, APP)).status_code == 200


def test_deleted_child_restriction_sidecar_fails_closed_for_signed_embed_claim(api):
    token = child(api)
    with Session(api.engine) as session:
        session.delete(session.get(EmbedSession, token_digest(token))); session.commit()
    assert api.client.get("/auth/me", headers=headers(token, APP)).status_code == 401


def test_simultaneous_exchange_commits_exactly_one_child(api):
    code = launch(api).json()["code"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: exchange(api, code), range(2)))
    assert sorted(result.status_code for result in results) == [200, 400]
    with Session(api.engine) as session:
        assert len(session.exec(select(EmbedSession)).all()) == 1


@pytest.mark.parametrize("seconds", [45, 86400])
def test_child_server_lease_never_exceeds_source_or_ten_minutes_and_preserves_older_auth_age(api, seconds):
    import jwt
    from app.services.auth import track_session
    source = decode_token_claims(api.scoped)
    source["iat"] = int((datetime.utcnow() - timedelta(hours=4)).timestamp())
    source["exp"] = int(datetime.utcnow().timestamp()) + seconds
    source["jti"] = "bounded-source-" + str(seconds)
    api.scoped = jwt.encode(source, settings.jwt_secret, algorithm="HS256")
    with Session(api.engine) as session:
        track_session(api.scoped, session)
        session.add(WorkspaceOIDCSession(token_hash=token_digest(api.scoped), workspace_id="team", provider_revision=4))
        session.commit()
    token = child(api)
    derived = decode_token_claims(token)
    assert derived["iat"] == source["iat"]
    assert derived["exp"] == source["exp"]
    with Session(api.engine) as session:
        tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).one()
        assert int(tracked.expires_at.replace(tzinfo=timezone.utc).timestamp()) <= min(source["exp"], int(datetime.now(timezone.utc).timestamp()) + 600)
        if seconds == 45:
            assert int(tracked.expires_at.replace(tzinfo=timezone.utc).timestamp()) == source["exp"]


@pytest.mark.parametrize("change", ["source-revoked", "application-disabled"])
def test_matrix_gateway_and_socket_membership_recheck_live_child_restrictions(api, monkeypatch, change):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.api.secure_messaging import _matrix_authorize
    from app.realtime import socket
    from app.services.sso_scope import current_workspace_scope
    token = child(api)
    claims = decode_token_claims(token)
    with Session(api.engine) as session:
        session.add(MatrixSession(matrix_token_hash=token_digest("matrix-child"), user_id="owner",
            auth_token_hash=token_digest(token), auth_expires_at=datetime.utcfromtimestamp(claims["exp"]),
            device_id="CHILD", encrypted_access_token="not-opened-by-authorization"))
        session.flush()
        session.add(EmbedMatrixBinding(matrix_token_hash=token_digest("matrix-child"), application_id="application"))
        session.add(WorkspaceEncryptionUnlock(token_hash=token_digest(token), workspace_id="team",
            provider_revision=4, auth_version=0, expires_at=datetime.utcnow() + timedelta(minutes=5)))
        session.commit()
    monkeypatch.setattr(socket, "engine", api.engine)
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer matrix-child")]})
    with Session(api.engine) as session:
        assert _matrix_authorize(request, session)[0].device_id == "CHILD"
        assert current_workspace_scope(session) == "team"
    assert socket._is_member("owner", "shared", token) is True
    assert socket._is_member("owner", "elsewhere", token) is False
    with Session(api.engine) as session:
        if change == "source-revoked":
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(api.scoped))).one()
            row.revoked_at = datetime.utcnow()
        else:
            row = session.get(EmbedApplication, "application"); row.enabled = False
        session.add(row); session.commit()
    with Session(api.engine) as session:
        with pytest.raises(HTTPException) as denied:
            _matrix_authorize(request, session)
        assert denied.value.status_code == 401
    assert socket._is_member("owner", "shared", token) is False


def test_child_expiry_uses_utc_when_operator_selects_a_non_utc_process_timezone(api, monkeypatch):
    import os
    import time
    if not hasattr(time, "tzset"):
        pytest.skip("The platform does not expose process timezone selection")
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Stockholm")
    time.tzset()
    try:
        token = child(api)
        claims = decode_token_claims(token)
        assert claims is not None
        assert claims["exp"] > int(time.time())
        with Session(api.engine) as session:
            tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).one()
            assert 0 < int(tracked.expires_at.replace(tzinfo=timezone.utc).timestamp()) - int(time.time()) <= 600
        assert api.client.get("/auth/me", headers=headers(token, APP)).status_code == 200
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def renewal(api, token, origin=APP):
    return api.client.post("/embed/application/renew", headers=headers(token, origin))


def test_renewal_extends_only_same_child_server_lease_preserving_source_age_and_parent(api, monkeypatch):
    from app.services import embed as embed_service
    monkeypatch.setattr(embed_service, "CHILD_SESSION_SECONDS", 20)
    old = child(api)
    monkeypatch.setattr(embed_service, "CHILD_SESSION_SECONDS", 60)
    result = renewal(api, old)
    assert result.status_code == 200, result.text
    token = result.json()["access_token"]
    before, after = decode_token_claims(old), decode_token_claims(token)
    assert token == old
    assert after == before and 20 < result.json()["expires_in"] <= 60
    for key in ("sub", "iat", "av", "amr", "sso_workspace_id", "sso_provider_revision", "embed_application_id"):
        assert after[key] == before[key]
    assert api.client.get("/auth/me", headers=headers(old, APP)).status_code == 200
    assert api.client.get("/auth/me", headers=headers(token, APP)).status_code == 200
    assert renewal(api, old).status_code == 200
    with Session(api.engine) as session:
        derived = session.get(EmbedSession, token_digest(token))
        assert derived.parent_token_hash == token_digest(api.scoped)
        assert derived.source_expires_at == decode_token_claims(api.scoped)["exp"]
        assert derived.channel_id == "shared"


def test_renewal_preserves_existing_matrix_gateway_and_remaining_personal_unlock(api):
    from starlette.requests import Request
    from app.api.secure_messaging import _matrix_authorize
    old = child(api)
    expiry = datetime.utcnow() + timedelta(minutes=4)
    with Session(api.engine) as session:
        session.add(MatrixSession(matrix_token_hash=token_digest("same-matrix-token"), user_id="owner",
            auth_token_hash=token_digest(old), auth_expires_at=datetime.utcfromtimestamp(decode_token_claims(old)["exp"]),
            device_id="VERIFIED-DEVICE", encrypted_access_token="existing-sealed-matrix-token"))
        session.flush()
        session.add(EmbedMatrixBinding(matrix_token_hash=token_digest("same-matrix-token"), application_id="application"))
        session.add(WorkspaceEncryptionUnlock(token_hash=token_digest(old), workspace_id="team", provider_revision=4,
            auth_version=0, expires_at=expiry))
        session.commit()
    result = renewal(api, old)
    assert result.status_code == 200, result.text
    token = result.json()["access_token"]
    with Session(api.engine) as session:
        matrix = session.get(MatrixSession, token_digest("same-matrix-token"))
        assert matrix.auth_token_hash == token_digest(token)
        assert matrix.device_id == "VERIFIED-DEVICE" and matrix.encrypted_access_token == "existing-sealed-matrix-token"
        assert int(matrix.auth_expires_at.replace(tzinfo=timezone.utc).timestamp()) == result.json()["expires_at"]
        unlock = session.get(WorkspaceEncryptionUnlock, token_digest(token))
        assert unlock.expires_at == expiry and unlock.auth_version == 0 and unlock.provider_revision == 4
        request = Request({"type": "http", "headers": [(b"authorization", b"Bearer same-matrix-token")]})
        assert _matrix_authorize(request, session)[0].device_id == "VERIFIED-DEVICE"
    # Original parent revocation still closes the unchanged Matrix gateway token.
    with Session(api.engine) as session:
        row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(api.scoped))).one()
        row.revoked_at = datetime.utcnow(); session.add(row); session.commit()
    from fastapi import HTTPException
    with Session(api.engine) as session:
        with pytest.raises(HTTPException):
            _matrix_authorize(request, session)


@pytest.mark.parametrize("unlock_state", ["absent", "expired"])
def test_renewal_never_creates_or_extends_personal_encryption_unlock(api, unlock_state):
    old = child(api)
    if unlock_state == "expired":
        with Session(api.engine) as session:
            session.add(WorkspaceEncryptionUnlock(token_hash=token_digest(old), workspace_id="team", provider_revision=4,
                auth_version=0, expires_at=datetime.utcnow() - timedelta(seconds=1))); session.commit()
    result = renewal(api, old)
    assert result.status_code == 200, result.text
    with Session(api.engine) as session:
        unlock = session.get(WorkspaceEncryptionUnlock, token_digest(result.json()["access_token"]))
        assert unlock is None if unlock_state == "absent" else unlock.expires_at < datetime.utcnow()


@pytest.mark.parametrize("change", ["source-revoked", "application-disabled", "provider-edited", "workspace-removed", "channel-removed"])
def test_renewal_fails_when_any_original_authority_is_removed(api, change):
    old = child(api)
    with Session(api.engine) as session:
        if change == "source-revoked":
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(api.scoped))).one()
            row.revoked_at = datetime.utcnow(); session.add(row)
        elif change == "application-disabled":
            row = session.get(EmbedApplication, "application"); row.enabled = False; session.add(row)
        elif change == "provider-edited":
            row = session.get(WorkspaceOIDCProvider, "team"); row.revision += 1; session.add(row)
        elif change == "workspace-removed":
            session.delete(session.get(WorkspaceMember, ("team", "owner")))
        else:
            session.delete(session.get(ChannelMember, ("shared", "owner")))
        session.commit()
    assert renewal(api, old).status_code in (401, 403, 404)


def test_renewal_rejects_source_sessions_and_untrusted_parent_origin(api):
    old = child(api)
    assert renewal(api, api.scoped).status_code == 403
    assert renewal(api, old, HOST).status_code == 403
    assert api.client.post("/embed/another-application/renew", headers=headers(old, APP)).status_code == 403


def test_concurrent_renewal_is_idempotent_and_retains_one_child_session(api):
    old = child(api)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: renewal(api, old), range(2)))
    assert [result.status_code for result in results] == [200, 200]
    assert all(result.json()["access_token"] == old for result in results)
    with Session(api.engine) as session:
        children = session.exec(select(EmbedSession)).all()
        assert len(children) == 1
        hashes = {row.token_hash for row in children}
        live = session.exec(select(ActiveSession).where(ActiveSession.token_hash.in_(hashes), ActiveSession.revoked_at.is_(None))).all()
        assert len(live) == 1 and live[0].token_hash == token_digest(old)
    assert api.client.get("/auth/me", headers=headers(old, APP)).status_code == 200


def test_renewal_cannot_extend_the_original_source_expiry_even_after_repeated_lease_extension(api, monkeypatch):
    import jwt
    import time
    from app.services.auth import track_session
    source = decode_token_claims(api.scoped)
    source["iat"] = int(time.time()) - 7200
    source["exp"] = int(time.time()) + 45
    source["jti"] = "renew-bounded-source"
    api.scoped = jwt.encode(source, settings.jwt_secret, algorithm="HS256")
    with Session(api.engine) as session:
        track_session(api.scoped, session)
        session.add(WorkspaceOIDCSession(token_hash=token_digest(api.scoped), workspace_id="team", provider_revision=4)); session.commit()
    token = child(api)
    for _ in range(2):
        result = renewal(api, token)
        assert result.status_code == 200, result.text
        token = result.json()["access_token"]
        claims = decode_token_claims(token)
        assert claims["iat"] == source["iat"] and claims["exp"] == source["exp"]


def test_expired_server_lease_rejects_future_signed_jwt_rest_renew_socket_and_matrix(api, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.api.secure_messaging import _matrix_authorize
    from app.realtime import socket
    token = child(api)
    claims = decode_token_claims(token)
    assert claims["exp"] > int(datetime.now(timezone.utc).timestamp()) + 600
    with Session(api.engine) as session:
        session.add(MatrixSession(matrix_token_hash=token_digest("leased-matrix"), user_id="owner",
            auth_token_hash=token_digest(token), auth_expires_at=datetime.utcfromtimestamp(claims["exp"]),
            device_id="LEASED", encrypted_access_token="unchanged"))
        session.flush()
        session.add(EmbedMatrixBinding(matrix_token_hash=token_digest("leased-matrix"), application_id="application"))
        session.add(WorkspaceEncryptionUnlock(token_hash=token_digest(token), workspace_id="team", provider_revision=4,
            auth_version=0, expires_at=datetime.utcnow() + timedelta(minutes=20)))
        row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).one()
        row.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row); session.commit()
    monkeypatch.setattr(socket, "engine", api.engine)
    assert decode_token_claims(token) is not None  # JWT hard expiry alone is deliberately insufficient.
    assert api.client.get("/auth/me", headers=headers(token, APP)).status_code == 401
    assert renewal(api, token).status_code == 401
    assert socket._is_member("owner", "shared", token) is False
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer leased-matrix")]})
    with Session(api.engine) as session:
        with pytest.raises(HTTPException) as denied:
            _matrix_authorize(request, session)
        assert denied.value.status_code == 401
    assert api.client.get("/auth/me", headers=headers(api.scoped, APP)).status_code == 200


def test_held_matrix_sync_response_is_discarded_when_child_lease_expires_during_wait(api, monkeypatch):
    import httpx
    from app.api import secure_messaging
    token = child(api)
    with Session(api.engine) as session:
        session.add(MatrixSession(matrix_token_hash=token_digest("held-matrix"), user_id="owner",
            auth_token_hash=token_digest(token), auth_expires_at=datetime.utcfromtimestamp(decode_token_claims(token)["exp"]),
            device_id="HELD", encrypted_access_token="unchanged"))
        session.flush()
        session.add(EmbedMatrixBinding(matrix_token_hash=token_digest("held-matrix"), application_id="application"))
        session.add(WorkspaceEncryptionUnlock(token_hash=token_digest(token), workspace_id="team", provider_revision=4,
            auth_version=0, expires_at=datetime.utcnow() + timedelta(minutes=20))); session.commit()
    class HeldUpstream:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs):
            with Session(api.engine) as session:
                row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).one()
                row.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row); session.commit()
            return httpx.Response(200, json={"next_batch": "held-ciphertext-response-must-not-escape", "rooms": {}})
    monkeypatch.setattr(settings, "matrix_enabled", True)
    monkeypatch.setattr(secure_messaging.httpx, "AsyncClient", HeldUpstream)
    api.client.app.include_router(secure_messaging.router)
    result = api.client.get("/_matrix/client/v3/sync", headers={"Authorization": "Bearer held-matrix"})
    assert result.status_code == 401
    assert "held-ciphertext-response-must-not-escape" not in result.text


def test_existing_socket_room_is_pruned_when_server_lease_expires(api, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from app.realtime import socket
    token = child(api)
    server = Mock()
    server.get_session = AsyncMock(return_value={"user_id": "owner", "token": token})
    server.enter_room, server.leave_room, server.emit = AsyncMock(), AsyncMock(), AsyncMock()
    server.manager.get_participants.return_value = [("held-socket", "engine-id")]
    monkeypatch.setattr(socket, "engine", api.engine)
    monkeypatch.setattr(socket, "sio", server)
    assert asyncio.run(socket.join("held-socket", "shared")) == {"ok": True}
    with Session(api.engine) as session:
        row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).one()
        row.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row); session.commit()
    assert "error" in asyncio.run(socket.join("held-socket", "shared"))
    asyncio.run(socket.broadcast_to_channel("shared", "message", {"content": "room delivery follows pruning"}))
    server.leave_room.assert_any_await("held-socket", "shared")
