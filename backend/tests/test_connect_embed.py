"""Current bilateral grants across trusted Embed and the encrypted gateway.

The upstream is deliberately deterministic here; the companion browser proof
uses the actual Rust SDK and a separate, disposable Synapse homeserver.
"""
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
import pytest
from sqlmodel import Session

from app.api import auth, embed, secure_messaging
from app.config import settings
from app.models.channel import Channel, ChannelMember
from app.models.connect import ConnectConnection, ConnectGrant
from app.models.embed import EmbedApplication, EmbedMatrixBinding, EmbedSession
from app.models.enterprise_governance import Organization, OrganizationWorkspace
from app.models.secure_channel import MatrixAccount, MatrixSession, SecureChannel
from app.models.workspace import WorkspaceChannel, WorkspaceMember
from app.services.auth import token_digest
from app.services.embed import proof_challenge
from app.services.matrix_bridge import membership_epoch
from app.services.sso_scope import bind_scope_for_hash
from .test_connect import activate, grant, shared_api

HOST = "https://partner-product.example.com"
APP = "https://collaboration.example.com"
VERIFIER = "C" * 64


@pytest.fixture
def partner(shared_api, monkeypatch):
    api = shared_api
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "public_app_url", APP)
    api.client.app.include_router(auth.router)
    api.client.app.include_router(embed.router)
    with Session(api.engine) as session:
        session.add(EmbedApplication(id="partner", workspace_id="b", name="Partner product",
            parent_origin=HOST, created_by="b-admin"))
        session.commit()
    return api


def launch(api, connection, actor="b-member-scoped-b"):
    return api.client.post("/workspaces/b/embed/launch", headers={**api.headers(actor), "Origin": HOST},
        json={"application_id": "partner", "channel_id": connection["channel_id"],
              "parent_origin": HOST, "code_challenge": proof_challenge(VERIFIER)})


def exchange(api, connection, code):
    return api.client.post("/embed/partner/exchange", headers={"Origin": APP}, json={
        "code": code, "code_verifier": VERIFIER, "parent_origin": HOST,
        "channel_id": connection["channel_id"]})


def child(api, connection):
    handoff = launch(api, connection)
    assert handoff.status_code == 200, handoff.text
    result = exchange(api, connection, handoff.json()["code"])
    assert result.status_code == 200, result.text
    return result.json()["access_token"]


def matrix_session(api, auth_token, bearer="partner-matrix"):
    with Session(api.engine) as session:
        for name in ("a-admin", "b-member"):
            if not session.get(MatrixAccount, name):
                session.add(MatrixAccount(user_id=name, matrix_user_id="@" + name + ":private.invalid",
                    encrypted_password="unused", provisioned=True))
        session.add(MatrixSession(matrix_token_hash=token_digest(bearer), user_id="b-member",
            auth_token_hash=token_digest(auth_token), auth_expires_at=datetime.utcnow() + timedelta(minutes=5),
            auth_version=0, device_id="PARTNER_DEVICE", encrypted_access_token="unused"))
        session.flush()
        embedded = session.get(EmbedSession, token_digest(auth_token))
        if embedded:
            session.add(EmbedMatrixBinding(matrix_token_hash=token_digest(bearer), application_id=embedded.application_id))
        session.commit()
    return {"Authorization": "Bearer " + bearer}


def epoch(api, connection, token):
    with Session(api.engine) as session:
        assert bind_scope_for_hash(session, token_digest(token), "b-member")
        return membership_epoch(session, connection["channel_id"])


def encrypted_path(api, connection):
    with Session(api.engine) as session:
        room = session.get(SecureChannel, connection["channel_id"]).matrix_room_id
    return "/_matrix/client/v3/rooms/" + quote(room, safe="") + "/send/m.room.encrypted/transaction"


def encrypted_upstream(monkeypatch):
    calls = []
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(200, json={"event_id": "$opaque-event"})
    monkeypatch.setattr(secure_messaging.httpx, "Client", lambda **_: Client())
    return calls


def test_partner_own_sso_can_launch_exchange_and_renew_shared_channel(partner):
    api = partner
    connection = activate(api)
    grant(api, connection)
    # The unauthenticated exchange must bind its verified parent digest before
    # checking organization SSO policy; it may not gain native-account authority.
    with Session(api.engine) as session:
        session.add(Organization(id="org-b", name="Partner", created_by="a-admin", require_sso=True))
        session.flush()
        session.add(OrganizationWorkspace(organization_id="org-b", workspace_id="b", linked_by="a-admin"))
        session.commit()
    token = child(api, connection)
    headers = {"Authorization": "Bearer " + token, "Origin": APP}
    profile = api.client.get("/auth/me", headers=headers)
    assert profile.status_code == 200 and profile.json()["workspace_id"] == "b"
    response = api.client.get("/channels/" + connection["channel_id"], headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["workspace_id"] == "b" and response.json()["owner_workspace_id"] == "a"
    renewed = api.client.post("/embed/partner/renew", headers=headers)
    assert renewed.status_code == 200 and renewed.json()["access_token"] == token
    assert api.client.get("/workspaces/a", headers=headers).status_code == 403
    with Session(api.engine) as session:
        assert session.get(WorkspaceMember, ("a", "b-member")) is None


def test_accepting_admin_and_foreign_ordinary_seat_do_not_become_embed_grants(partner):
    api = partner
    connection = activate(api)
    assert launch(api, connection, "b-admin-scoped-b").status_code == 403
    assert launch(api, connection).status_code == 403
    with Session(api.engine) as session:
        session.add(Channel(id="foreign", name="Foreign ordinary channel"))
        session.flush()
        session.add(WorkspaceChannel(channel_id="foreign", workspace_id="a"))
        session.add(ChannelMember(channel_id="foreign", user_id="b-member"))
        session.commit()
    assert launch(api, {"channel_id": "foreign"}).status_code in (401, 403)


@pytest.mark.parametrize("change", ["grant", "connection", "seat", "canonical-owner"])
def test_pending_handoff_rechecks_current_bilateral_authority(partner, change):
    api = partner
    connection = activate(api)
    grant(api, connection)
    result = launch(api, connection)
    assert result.status_code == 200, result.text
    with Session(api.engine) as session:
        if change == "grant":
            row = session.get(ConnectGrant, (connection["id"], "b-member")); row.revoked_at = datetime.utcnow()
        elif change == "connection":
            row = session.get(ConnectConnection, connection["id"]); row.status = "revoked"
        elif change == "canonical-owner":
            row = session.get(WorkspaceChannel, connection["channel_id"]); row.workspace_id = "b"
        else:
            row = session.get(ChannelMember, (connection["channel_id"], "b-member")); session.delete(row)
        if change != "seat":
            session.add(row)
        session.commit()
    assert exchange(api, connection, result.json()["code"]).status_code == 400


@pytest.mark.parametrize("change", ["grant", "connection"])
def test_retained_child_and_matrix_credentials_fail_after_current_grant_revocation(partner, change):
    api = partner
    connection = activate(api)
    grant(api, connection)
    token = child(api, connection)
    matrix = matrix_session(api, token)
    from app.realtime import socket
    assert socket._is_member("b-member", connection["channel_id"], token)
    with Session(api.engine) as session:
        if change == "grant":
            row = session.get(ConnectGrant, (connection["id"], "b-member")); row.revoked_at = datetime.utcnow()
        else:
            row = session.get(ConnectConnection, connection["id"]); row.status = "revoked"
        session.add(row); session.commit()
        # Intentionally retain the old native ChannelMember to prove the current
        # bilateral grant is independently required on every child side path.
        assert session.get(ChannelMember, (connection["channel_id"], "b-member"))
    headers = {"Authorization": "Bearer " + token, "Origin": APP}
    assert api.client.get("/auth/me", headers=headers).status_code == 401
    assert api.client.post("/embed/partner/renew", headers=headers).status_code == 401
    assert not socket._is_member("b-member", connection["channel_id"], token)
    assert api.client.get("/_matrix/client/v3/sync", headers=matrix).status_code == 401
    # Production revocation also removes this stale seat; the member's own SSO
    # remains valid once its managed-identity metadata is consistent again.
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, (connection["channel_id"], "b-member")))
        session.commit()
    assert api.client.get("/auth/me", headers=api.headers("b-member-scoped-b")).status_code == 200


@pytest.mark.parametrize("embedded", [False, True])
def test_partner_encrypted_put_uses_current_explicit_grant_under_channel_lock(partner, monkeypatch, embedded):
    api = partner
    connection = activate(api)
    grant(api, connection)
    token = child(api, connection) if embedded else api.tokens["b-member-scoped-b"]
    matrix = matrix_session(api, token)
    calls = encrypted_upstream(monkeypatch)
    matrix["X-FreeCollab-Membership-Epoch"] = epoch(api, connection, token)
    response = api.client.put(encrypted_path(api, connection), headers=matrix,
        json={"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "opaque"})
    assert response.status_code == 200, response.text
    assert response.json() == {"event_id": "$opaque-event"} and len(calls) == 1
    assert calls[0][2]["headers"]["Authorization"] == matrix["Authorization"]
    assert calls[0][2]["content"] == b'{"algorithm":"m.megolm.v1.aes-sha2","ciphertext":"opaque"}'


@pytest.mark.parametrize("mutation", ["grant", "connection", "room", "epoch"])
def test_shared_encrypted_put_rechecks_grant_room_and_epoch_before_upstream(partner, monkeypatch, mutation):
    api = partner
    connection = activate(api)
    grant(api, connection)
    token = api.tokens["b-member-scoped-b"]
    matrix = matrix_session(api, token)
    matrix["X-FreeCollab-Membership-Epoch"] = epoch(api, connection, token)
    path = encrypted_path(api, connection)
    calls = encrypted_upstream(monkeypatch)
    original = secure_messaging.lock_channel_writes
    def lock_and_change(session, channel_id):
        original(session, channel_id)
        if mutation == "grant":
            row = session.get(ConnectGrant, (connection["id"], "b-member")); row.revoked_at = datetime.utcnow()
        elif mutation == "connection":
            row = session.get(ConnectConnection, connection["id"]); row.status = "revoked"
        elif mutation == "room":
            row = session.get(SecureChannel, channel_id); row.matrix_room_id = "!different:private.invalid"
        else:
            row = session.get(ChannelMember, (channel_id, "a-admin")); session.delete(row)
        if mutation != "epoch":
            session.add(row)
        session.flush()
    monkeypatch.setattr(secure_messaging, "lock_channel_writes", lock_and_change)
    response = api.client.put(path, headers=matrix,
        json={"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "opaque"})
    assert response.status_code in (401, 403, 409), response.text
    assert calls == []


@pytest.mark.parametrize("mutation", ["sidecar", "marker", "mismatch"])
def test_matrix_child_cannot_upgrade_when_embed_restriction_is_deleted(partner, mutation):
    from fastapi import HTTPException
    from starlette.requests import Request
    api = partner
    connection = activate(api)
    grant(api, connection)
    token = child(api, connection)
    matrix_session(api, token)
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer partner-matrix")]})
    with Session(api.engine) as session:
        assert secure_messaging._matrix_authorize(request, session)[0].user_id == "b-member"
        if mutation == "sidecar":
            session.delete(session.get(EmbedSession, token_digest(token)))
        elif mutation == "marker":
            session.delete(session.get(EmbedMatrixBinding, token_digest("partner-matrix")))
        else:
            row = session.get(EmbedMatrixBinding, token_digest("partner-matrix"))
            row.application_id = "another-application"
            session.add(row)
        session.commit()
        with pytest.raises(HTTPException):
            secure_messaging._matrix_authorize(request, session)


def test_real_session_endpoint_records_and_repairs_marker_only_with_signed_child(partner, monkeypatch, tmp_path):
    api = partner
    connection = activate(api)
    grant(api, connection)
    token = child(api, connection)
    monkeypatch.setattr(settings, "message_encryption_keys", {})
    monkeypatch.setattr(settings, "message_encryption_key_file", str(tmp_path / "keys.json"))
    def ensure_account(session, user_id):
        account = session.get(MatrixAccount, user_id)
        if not account:
            account = MatrixAccount(user_id=user_id, matrix_user_id="@b-member:private.invalid",
                encrypted_password="unused", provisioned=True)
            session.add(account)
            session.flush()
        return account
    monkeypatch.setattr(secure_messaging, "ensure_account", ensure_account)
    monkeypatch.setattr(secure_messaging, "login_account", lambda account: {
        "user_id": account.matrix_user_id, "device_id": "REAL_ENDPOINT_DEVICE", "access_token": "endpoint-matrix"})
    headers = {"Authorization": "Bearer " + token}
    response = api.client.post("/secure/session", headers=headers, json={})
    assert response.status_code == 200, response.text
    digest = token_digest("endpoint-matrix")
    with Session(api.engine) as session:
        assert session.get(EmbedMatrixBinding, digest).application_id == "partner"
        session.delete(session.get(EmbedMatrixBinding, digest))
        session.commit()
    # Old devices after an additive migration fail closed until the trusted
    # client re-authenticates using its signed, current child at /secure/session.
    assert api.client.get("/_matrix/client/v3/sync", headers={"Authorization": "Bearer endpoint-matrix"}).status_code == 401
    repaired = api.client.post("/secure/session", headers=headers, json={})
    assert repaired.status_code == 200 and repaired.json()["access_token"] == "endpoint-matrix"
    with Session(api.engine) as session:
        assert session.get(EmbedMatrixBinding, digest).application_id == "partner"
